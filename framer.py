# SPDX-FileCopyrightText: 2026 QuincyLeo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Photo framer core renderer.

Design rule: the photo's own pixels are never resampled, colour-converted or
lossily re-encoded.  The source image is pasted 1:1 into a larger canvas and
only the surrounding backdrop, shadow and caption are synthesised.  The
source ICC profile and EXIF block are carried through to the output file.

Every geometric parameter is expressed as a percentage of the photo's short
side, so rendering a downscaled copy (the live preview) is proportionally
identical to rendering the full-resolution original (the export).
"""

from __future__ import annotations

import io
import math
import os
import re
from dataclasses import asdict, dataclass, fields

from PIL import (
    ExifTags,
    Image,
    ImageChops,
    ImageDraw,
    ImageFilter,
    ImageFont,
    ImageOps,
    ImageStat,
    UnidentifiedImageError,
)

import logos

Image.MAX_IMAGE_PIXELS = None

try:  # optional: iPhone / HEIC input
    import pillow_heif

    pillow_heif.register_heif_opener()
    READABLE_HEIF = {".heic", ".heif"}
except Exception:
    READABLE_HEIF = set()

READABLE_EXT = {".jpg", ".jpeg", ".jpe", ".png", ".tif", ".tiff", ".webp", ".bmp",
                ".avif", ".jp2"} | READABLE_HEIF

TEXT_VARS = ["brand", "model", "focal", "focal35", "aperture", "shutter", "iso", "ev",
             "date", "time", "lens", "filename", "wxh"]

MARK_HEIGHT = 1.2       # a maker's logo, as a multiple of the caption's cap height


class BlurStyle:
    """Tuned base proportions, all as a percentage of the photo's short side.

    The UI exposes multipliers and 0-100 strengths on top of these; nobody has
    to think in percentages.
    """

    BASE_PAD = 6.72         # frame width at border = 1.0
    BASE_RADIUS = 8.0       # corner radius at radius = 100
    BASE_BG_BLUR = 12.0     # backdrop blur at blur = 100
    BLUR_FLOOR = 3.0        # the blur slider does not go below this
    BG_ZOOM = 1.15
    SHADOW_BLUR_MIN, SHADOW_BLUR_SPAN = 1.4, 2.2
    SHADOW_DY, SHADOW_MAX_OPACITY = 0.8, 88.0
    # Caption type sizes, as a percentage of the photo's short side -- so the
    # caption keeps the same relative weight on a 12MP phone shot and a 100MP
    # medium-format frame alike.  text_scale multiplies these.
    TITLE_PCT, PARAMS_PCT = 4.42, 2.86          # two-line layout
    TITLE_PCT_ONE, PARAMS_PCT_ONE = 3.38, 2.93  # one-line layout
    # LINE_GAP separates the two stacked lines; SEGMENT_GAP separates the
    # maker/model group from the settings group when they share one line -- wide
    # enough to read as a grouping break rather than a word space.
    LINE_GAP_PCT, SEGMENT_GAP_PCT = 1.9, 3.4
    TITLE_WEIGHT, PARAMS_WEIGHT = "Semibold", "Regular"
    PARAMS_ALPHA = 0.88     # the parameter line sits slightly back
    TRACKING_EM = 0.03      # letter spacing, as a fraction of the font size
    SEPARATOR = "  |  "
    TITLE_GAP = "  "        # between maker and model: two spaces, not one
    AUTO_LIGHT_THRESHOLD = 0.55  # backdrop luma above this gets black text

class BarStyle:
    """The info-bar style: a flat band under the photo carrying the camera data.

    Its proportions are a percentage of the photo's *width*, so the band keeps
    the same weight on a 3:2 frame and a 16:9 panorama.
    """

    H_PCT = 9.25            # band height at bar_width = 1.0
    MARGIN_PCT = 4.33       # left and right margin
    CONTENT_SHARE = 0.478   # how much of the band's height its content may
                            # fill: at bar_width = 1.0 that is exactly the
                            # two-line block, which is why a logo turned up
                            # until it stands level with the type needs a
                            # wider band, not a bigger number, to grow again
    MIN_AIR_PCT = 0.40      # ... but never less air than this: the band
                            # grows rather than clip type it cannot hold
    SIZE1_PCT = 2.30        # type size of the first line
    SIZE2_PCT = 1.78        # ... and of the second
    TRACK1_EM = 0.02        # letter spacing, as a fraction of the type size
    TRACK2_EM = 0.03        # ... the smaller line carries a little more
    LINE_GAP_PCT = 1.53     # first baseline down to the second line's cap top
    LOGO_GAP_PCT = 2.97     # logo to rule
    RULE_GAP_PCT = 1.70     # rule to type
    RULE_W_PCT = 0.17       # rule thickness
    COLUMN_GAP_PCT = 4.0    # the narrowest the left and right blocks may sit
    LOGO_FRACTION = 0.52    # logo height at bar_logo_scale = 1.0, as a
                            # fraction of the two-line block; the ceiling is
                            # the band's own inner height -- past that only a
                            # wider band makes the logo any bigger
    LINE1_WEIGHT, LINE2_WEIGHT = "Bold", "Regular"
    LINE1_ALPHA, LINE2_ALPHA = 0.92, 0.75
    RULE_ALPHA = 0.30

    BG = {"white": (255, 255, 255), "black": (0, 0, 0)}
    INK = {"white": (0, 0, 0), "black": (255, 255, 255)}

__all__ = ["Params", "TEXT_VARS", "READABLE_EXT", "load_source", "exif_values",
           "resolve_texts", "resolve_mark", "render", "render_file", "render_bytes",
           "save_render", "default_out_path", "output_name", "downscale_for_preview",
           "is_bar", "bar_texts", "auto_bar_params", "auto_bar_lens",
           "auto_bar_model", "auto_bar_date"]


@dataclass
class Params:
    """Everything the UI can change.  Seven controls, tuned bases behind them."""

    style: str = "blur"        # "blur" = blurred backdrop, "bar" = info bar

    border: float = 1.0        # frame width, multiple of BlurStyle.BASE_PAD (0.1 - 5.0)
    radius: float = 28.0       # 0 = square corners, 100 = very round
    shadow: float = 60.0       # 0 = no shadow
    blur: float = 25.0         # backdrop blur strength; floor is BlurStyle.BLUR_FLOOR
    text_scale: float = 1.0    # font size multiplier (0.5 - 2.0)

    one_line: bool = False     # maker/model and settings share one baseline
    title: str = ""            # maker + model; used only when title_auto is off
    title_auto: bool = True
    params_text: str = ""      # focal | aperture | shutter | ISO
    params_auto: bool = True

    text_light: float = 100.0  # 0 = black, 100 = white
    text_auto: bool = True     # pick it from the backdrop behind the text

    # ---- info-bar style only.  Every multiplier below is 1.0 by default and
    # runs 0.5 - 5.0 in steps of 0.1, so one number means "as designed".
    bar_color: str = "white"      # "white" or "black" band; the type inverts
    bar_width: float = 1.0        # band height, multiple of BarStyle.H_PCT
    bar_text_scale: float = 1.0   # type size multiplier
    bar_logo_scale: float = 1.0   # logo size multiplier, capped by the band
    bar_params: str = ""          # focal, aperture, shutter, ISO
    bar_params_auto: bool = True
    bar_lens: str = ""            # lens model, under the exposure line
    bar_lens_auto: bool = True
    bar_model: str = ""           # maker and body, set at the right edge
    bar_model_auto: bool = True
    bar_date: str = ""            # when it was taken, under the body name
    bar_date_auto: bool = True

    @classmethod
    def from_dict(cls, data):
        known = {f.name: f for f in fields(cls)}
        kw = {}
        for key, value in (data or {}).items():
            f = known.get(key)
            if f is None:
                continue
            try:
                kw[key] = bool(value) if isinstance(f.default, bool) \
                    else float(value) if isinstance(f.default, float) else str(value)
            except (TypeError, ValueError):
                continue
        return cls(**kw)

    def to_dict(self):
        return asdict(self)


# --------------------------------------------------------------------------
# source loading
# --------------------------------------------------------------------------

def load_source(path):
    """Open an image, apply EXIF orientation, and return (photo, info).

    Orientation is applied with lossless 90-degree rotations / flips.  No
    colour conversion is performed on an already-RGB image, and the ICC
    profile is handed back untouched so the caller can re-embed it.
    """
    try:
        img = Image.open(path)
        img.load()
    except UnidentifiedImageError:
        hint = ""
        if os.path.splitext(path)[1].lower() in (".heic", ".heif"):
            hint = "：HEIC/HEIF 需要先安装 pillow-heif（pip3 install pillow-heif）"
        raise ValueError(f"无法识别的图片格式{hint}") from None

    original_mode = img.mode
    source_format = img.format
    bit_depth = 8
    try:  # TIFF can carry more than 8 bits per channel; Pillow truncates it
        sample = getattr(img, "tag_v2", {}).get(258)
        if sample:
            bit_depth = max(sample) if isinstance(sample, (tuple, list)) else int(sample)
    except Exception:
        pass
    icc = img.info.get("icc_profile")
    dpi = img.info.get("dpi")
    exif_bytes = img.info.get("exif")

    exif_raw = img.getexif()
    orientation = exif_raw.get(0x0112, 1)
    if orientation != 1:
        img = ImageOps.exif_transpose(img)

    converted = False
    if img.mode == "RGBA" or (img.mode == "P" and "transparency" in img.info):
        flat = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        flat.paste(rgba, (0, 0), rgba.getchannel("A"))
        img, converted = flat, True
    elif img.mode != "RGB":
        img, converted = img.convert("RGB"), True

    info = {
        "icc_profile": icc,
        "exif": exif_bytes,
        "dpi": dpi,
        "exif_raw": exif_raw,
        "orientation": orientation,
        "source_mode": original_mode,
        "bit_depth": bit_depth,
        "converted": converted,
        "source_format": source_format,
    }
    return img, info


# --------------------------------------------------------------------------
# EXIF -> display strings
# --------------------------------------------------------------------------

_BRAND_TIDY = {
    "NIKON CORPORATION": "NIKON", "EASTMAN KODAK COMPANY": "Kodak",
    "OLYMPUS IMAGING CORP.": "OLYMPUS", "OM DIGITAL SOLUTIONS": "OM SYSTEM",
    "CANON": "Canon", "SONY": "SONY", "FUJIFILM": "FUJIFILM", "APPLE": "Apple",
    "DJI": "DJI", "HASSELBLAD": "HASSELBLAD", "PANASONIC": "Panasonic",
    "LEICA CAMERA AG": "Leica", "RICOH IMAGING COMPANY, LTD.": "RICOH",
}


def _num(value):
    if isinstance(value, (tuple, list)):
        if len(value) == 2 and value[1]:
            try:
                return float(value[0]) / float(value[1])
            except (TypeError, ValueError, ZeroDivisionError):
                return None
        value = value[0] if value else None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _shutter(value):
    f = _num(value)
    if not f or f <= 0:
        return ""
    if f >= 1:
        return f"{f:g}s"
    denominator = round(1.0 / f)
    return f"1/{denominator}"


_NIKON_MARK = re.compile(r"^(Z\s?\d+)_(\d)$")
_ROMAN = {"2": "II", "3": "III", "4": "IV"}


def tidy_model(brand, model):
    """`model` as the camera itself prints it.

    Nikon writes a mark II body as "Z50_2" in the EXIF and as "Z50II" on the
    body, the box and every page about it; the underscore is a field
    convention, not a name.  Nothing else is rewritten.
    """
    found = _NIKON_MARK.match(model) if brand.upper().startswith("NIKON") else None
    return found[1] + _ROMAN[found[2]] if found and found[2] in _ROMAN else model


def exif_values(img, path=""):
    """Pull the fields used by the text templates out of an image's EXIF."""
    out = dict.fromkeys(TEXT_VARS, "")
    out["filename"] = os.path.splitext(os.path.basename(path))[0] if path else ""
    out["wxh"] = f"{img.width}×{img.height}"

    try:
        exif = img.getexif()
        base = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
        sub = {ExifTags.TAGS.get(k, k): v for k, v in exif.get_ifd(ExifTags.IFD.Exif).items()}
    except Exception:
        return out
    get = lambda *names: next((v for v in (sub.get(n, base.get(n)) for n in names)
                               if v not in (None, "")), None)

    make = str(get("Make") or "").strip().rstrip("\x00").strip()
    model = str(get("Model") or "").strip().rstrip("\x00").strip()
    brand = _BRAND_TIDY.get(make.upper(), make)
    if brand and model.upper().startswith(brand.upper()):
        model = model[len(brand):].strip(" -_")
    out["brand"], out["model"] = brand, tidy_model(brand, model)
    out["lens"] = str(get("LensModel") or "").strip().rstrip("\x00").strip()

    focal = _num(get("FocalLength"))
    if focal:
        out["focal"] = f"{focal:.1f}mm"
    focal35 = _num(get("FocalLengthIn35mmFilm"))
    if focal35:
        out["focal35"] = f"{focal35:.0f}mm"
    aperture = _num(get("FNumber"))
    if aperture is None:
        apex = _num(get("ApertureValue"))
        aperture = 2.0 ** (apex / 2.0) if apex else None
    if aperture:
        out["aperture"] = f"f/{round(aperture, 1):g}"
    out["shutter"] = _shutter(get("ExposureTime"))
    iso = get("ISOSpeedRatings", "PhotographicSensitivity", "ISO")
    iso = _num(iso[0] if isinstance(iso, (tuple, list)) and iso else iso)
    if iso:
        out["iso"] = f"ISO{iso:.0f}"

    # exposure compensation: only worth printing when the photographer dialled
    # something in, so a plain 0 EV is left out
    bias = _num(get("ExposureBiasValue"))
    if bias is not None and abs(bias) >= 0.01:
        out["ev"] = f"{bias:+.1f}".rstrip("0").rstrip(".") + "EV"

    stamp = str(get("DateTimeOriginal", "DateTime", "DateTimeDigitized") or "")
    m = re.match(r"(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2})", stamp)
    if m:
        out["date"] = f"{m[1]}-{m[2]}-{m[3]}"
        out["time"] = f"{m[4]}:{m[5]}"
    return out


def auto_title(values):
    """Maker and model exactly as the camera wrote them, two spaces apart.

    No case folding: whatever is in the EXIF is what gets printed.  The only
    cleanup is dropping the corporate noise some makers append (see
    _BRAND_TIDY) and the brand prefix cameras repeat in the model field.
    """
    brand = str((values or {}).get("brand") or "").strip()
    model = str((values or {}).get("model") or "").strip()
    return BlurStyle.TITLE_GAP.join(part for part in (brand, model) if part)


def auto_params(values):
    """Focal length, aperture, shutter, ISO and -- when it isn't zero -- the
    exposure compensation, pipe separated."""
    values = values or {}
    parts = [values.get(key) for key in ("focal", "aperture", "shutter", "iso", "ev")]
    return BlurStyle.SEPARATOR.join(str(p).strip() for p in parts if str(p or "").strip())


def resolve_texts(params, values):
    """The two caption strings: from EXIF unless the user typed over them."""
    title = auto_title(values) if params.title_auto else str(params.title or "")
    settings = auto_params(values) if params.params_auto else str(params.params_text or "")
    return title.strip(), settings.strip()


def is_bar(params):
    """Whether this is the info-bar style rather than the blurred backdrop."""
    return str(getattr(params, "style", "blur") or "blur").lower() == "bar"


def auto_bar_params(values):
    """The exposure line the info bar prints: "16mm F5.6, 1/800s, ISO100".

    The same numbers the blurred style sets, written the way a camera back
    writes them -- commas rather than pipes, a capital F, and a shutter speed
    that says it is a time.
    """
    values = values or {}
    focal = re.sub(r"\.0+(?=mm$)", "", str(values.get("focal") or ""))
    aperture = str(values.get("aperture") or "").replace("f/", "F")
    shutter = str(values.get("shutter") or "")
    if shutter and not shutter.endswith("s"):
        shutter += "s"
    parts = (focal, aperture, shutter, values.get("iso"), values.get("ev"))
    return ", ".join(str(p).strip() for p in parts if str(p or "").strip())


def auto_bar_lens(values):
    return str((values or {}).get("lens") or "").strip()


def auto_bar_model(values):
    """Maker and body, one space apart -- "NIKON Z50II"."""
    values = values or {}
    brand = str(values.get("brand") or "").strip()
    model = str(values.get("model") or "").strip()
    return " ".join(part for part in (brand, model) if part)


def auto_bar_date(values):
    values = values or {}
    return " ".join(part for part in (str(values.get("date") or "").strip(),
                                      str(values.get("time") or "").strip()) if part)


BAR_SLOTS = {"params": auto_bar_params, "lens": auto_bar_lens,
             "model": auto_bar_model, "date": auto_bar_date}


def bar_texts(params, values):
    """The info bar's four strings: from EXIF unless the user typed over them.

    Emptying a field is a real answer -- that line is simply left out -- so
    "following EXIF" is tracked by its own flag rather than by emptiness.
    """
    out = {}
    for slot, auto in BAR_SLOTS.items():
        following = bool(getattr(params, f"bar_{slot}_auto", True))
        text = auto(values) if following else str(getattr(params, f"bar_{slot}", "") or "")
        out[slot] = text.strip()
    return out


def resolve_mark(params, values, title="", texts=None):
    """Which maker's logo the caption carries, if any -- or None for text.

    EXIF decides, the way every other framer decides it: the Make field, or
    the Model field for the files whose Make was stripped on the way here
    (Nikon and Canon repeat the brand inside Model, and Make is the tag that
    editors and sharing apps drop).  A hand-typed title has no field to
    trust, so it is matched on its own text instead: typing "FUJIFILM X-T5"
    still gets the logo, and typing anything else drops it.

    Each style keeps the maker's name in its own field -- the caption title
    for the blurred backdrop, the body name for the info bar -- so that is
    the field a hand-typed answer is read from.

    This is the only place the question is answered; `render` and the caption
    layout just carry out the answer.
    """
    values = values or {}
    if is_bar(params):
        if not params.bar_model_auto:
            typed = (texts or {}).get("model") or params.bar_model or ""
            found = logos.LIBRARY.split_leading(typed)
            return found[0] if found else None
    elif not params.title_auto:
        found = logos.LIBRARY.split_leading(title or params.title or "")
        return found[0] if found else None
    brand = str(values.get("brand") or "").strip().lower()
    if brand:
        return brand if brand in logos.LIBRARY else None
    found = logos.LIBRARY.split_leading(str(values.get("model") or ""))
    return found[0] if found else None


# --------------------------------------------------------------------------
# drawing primitives
# --------------------------------------------------------------------------

MISSING_CHAR = "\ue000"   # private use area: no font defines it
Z_MARK = "\ue001"         # ... and here it stands in for Nikon's barred Z


class FontBook:
    """The faces the caption is set in, kept open and measured once.

    SF Pro is a variable font, so one file answers every weight; Arial stands
    in where it is missing, and Hiragino carries the CJK glyphs SF Pro has
    none of.  Opening a face is expensive and the info bar measures the same
    band up to six times per render, so every face is cached by size and
    weight.

    There is one book -- FONTS, just below -- for the life of the process.  A
    second one would re-open SFNS.ttf and re-run `set_variation_by_name` on
    every measurement, which is why there is no way to ask for a private
    cache.
    """

    SF_FONT = "/System/Library/Fonts/SFNS.ttf"
    CJK_FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"
    CJK_FACES = {"light": 0, "bold": 2}      # Hiragino Sans GB W3 / W6
    ARIAL = "/System/Library/Fonts/Supplemental/Arial.ttf"
    ARIAL_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
    HEAVY_WEIGHTS = ("Medium", "Semibold", "Bold", "Heavy", "Black")

    WEIGHTS = ["Ultralight", "Thin", "Light", "Regular", "Medium", "Semibold",
               "Bold", "Heavy", "Black"]

    def __init__(self):
        self._cache = {}

    def sans(self, size, weight="Regular"):
        size = max(1, int(round(size)))
        key = (size, weight)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        font = None
        try:
            font = ImageFont.truetype(self.SF_FONT, size)
            font.set_variation_by_name(weight if weight in self.WEIGHTS else "Regular")
        except Exception:
            try:
                font = ImageFont.truetype(
                    self.ARIAL_BOLD if weight in self.HEAVY_WEIGHTS else self.ARIAL,
                    size)
            except Exception:
                font = ImageFont.load_default()
        self._cache[key] = font
        return font

    def cjk(self, size, weight="Regular"):
        """SF Pro carries no CJK glyphs, so Chinese text needs a second face."""
        size = max(1, int(round(size)))
        key = ("cjk", size, weight in self.HEAVY_WEIGHTS)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        face = self.CJK_FACES["bold" if weight in self.HEAVY_WEIGHTS else "light"]
        try:
            font = ImageFont.truetype(self.CJK_FONT, size, index=face)
        except Exception:
            font = self.sans(size, weight)
        self._cache[key] = font
        return font

    def covers(self, font, char):
        """Whether `font` has a real glyph for `char`.

        FreeType happily returns the .notdef box for anything missing, so the
        only reliable check is to render the character and compare it against
        one that no font defines.  The answer for a face is stashed on the
        face itself, which is where it stays valid for as long as the face
        does.
        """
        notdef = getattr(font, "_notdef_bitmap", None)
        if notdef is None:
            try:
                notdef = bytes(font.getmask(MISSING_CHAR, mode="L"))
            except Exception:
                notdef = b""
            try:
                font._notdef_bitmap = notdef
            except Exception:
                pass
        if not notdef:
            return True
        try:
            return bytes(font.getmask(char, mode="L")) != notdef
        except Exception:
            return True

    def covers_all(self, font, text):
        return all(self.covers(font, char) for char in set(text)
                   if not char.isspace())


FONTS = FontBook()


def rounded_mask(size, radius, supersample=4):
    """Opaque rectangle with antialiased rounded corners.

    The interior is exactly 255, which is what keeps a masked paste of the
    photo bit-identical everywhere except on the corner arcs themselves.
    Only the four corner patches are supersampled, so memory stays small
    even for a 100-megapixel photo.
    """
    width, height = size
    r = int(round(max(0.0, min(radius, min(width, height) / 2.0))))
    mask = Image.new("L", size, 255)
    if r < 1:
        return mask
    span = r * supersample
    corner = Image.new("L", (span, span), 0)
    ImageDraw.Draw(corner).pieslice([0, 0, 2 * span - 1, 2 * span - 1], 180, 270, fill=255)
    corner = corner.resize((r, r), Image.LANCZOS)
    mask.paste(corner, (0, 0))
    mask.paste(corner.transpose(Image.FLIP_LEFT_RIGHT), (width - r, 0))
    mask.paste(corner.transpose(Image.FLIP_TOP_BOTTOM), (0, height - r))
    mask.paste(corner.transpose(Image.ROTATE_180), (width - r, height - r))
    return mask


def make_backdrop(photo, canvas_size, blur_strength):
    """A Gaussian-blurred, zoomed, centre-cropped copy of the photo.

    The blur runs at a reduced working resolution and is scaled back up.  The
    reduction factor is chosen so the working-resolution blur radius always
    stays several times wider than the upscale factor, which is what keeps the
    result indistinguishable from blurring at full size -- the earlier fixed
    900px working width turned a mild blur into visible bicubic mush.
    """
    cw, ch = canvas_size
    strength = min(max(_num(blur_strength) or 0.0, BlurStyle.BLUR_FLOOR), 100.0)
    radius = strength / 100.0 * BlurStyle.BASE_BG_BLUR / 100.0 * min(cw, ch)

    k = 1.0
    if radius > 3.0:
        k = min(1.0, max(24.0 / radius, math.sqrt(3.0 / radius)))
    work = (max(8, round(cw * k)), max(8, round(ch * k)))

    scale = max(work[0] / photo.width, work[1] / photo.height) * BlurStyle.BG_ZOOM
    filled = (max(work[0], math.ceil(photo.width * scale)),
              max(work[1], math.ceil(photo.height * scale)))
    bg = photo.resize(filled, Image.BILINEAR, reducing_gap=2.0)
    left, top = (filled[0] - work[0]) // 2, (filled[1] - work[1]) // 2
    bg = bg.crop((left, top, left + work[0], top + work[1]))

    if radius * k >= 0.4:
        bg = bg.filter(ImageFilter.GaussianBlur(radius * k))
    return bg if work == (cw, ch) else bg.resize((cw, ch), Image.BICUBIC)


# --------------------------------------------------------------------------
# text block
# --------------------------------------------------------------------------

def tracked_length(font, text, tracking):
    """Advance width of `text` once letter spacing is added."""
    width = font.getlength(text)
    return width + max(0, len(text) - 1) * tracking if tracking > 0.01 else width


def plan_text(text, font, fallback, tracking):
    """Decide where every glyph goes, falling back for characters `font` lacks.

    Pillow has no tracking parameter and no font fallback, so glyphs are
    placed one at a time.  When the primary face covers the whole string the
    offsets still come from `getlength` of the preceding substring, which
    keeps the font's own kerning intact; a mixed string (Chinese typed into
    the caption, say) has to accumulate per-character advances instead.
    """
    if not text:
        return [], 0.0, font.getmetrics()
    if Z_MARK not in text and FONTS.covers_all(font, text):
        glyphs = [(char, font, font.getlength(text[:index]) + index * tracking)
                  for index, char in enumerate(text)]
        return glyphs, tracked_length(font, text, tracking), font.getmetrics()

    glyphs, cursor, faces = [], 0.0, set()
    for char in text:
        if char == Z_MARK:
            faces.add(font)
            glyphs.append(((NikonZ.glyph(font), 0.0), None, cursor))
            cursor += font.getlength("Z") + tracking
            continue
        face = font if FONTS.covers(font, char) else fallback
        faces.add(face)
        glyphs.append((char, face, cursor))
        cursor += face.getlength(char) + tracking
    ascent = max(f.getmetrics()[0] for f in faces)
    descent = max(f.getmetrics()[1] for f in faces)
    return glyphs, max(0.0, cursor - tracking), (ascent, descent)


def cap_height(font):
    """How tall this face's capitals are, in pixels."""
    try:
        return -font.getbbox("H", anchor="ls")[1] or font.getmetrics()[0]
    except Exception:
        return font.getmetrics()[0] * 0.72


def _ink_span(mask, y):
    """Where the ink starts and ends across one row of a mask, or None."""
    if not 0 <= y < mask.height:
        return None
    row = mask.crop((0, y, mask.width, y + 1)).point(lambda v: 255 if v > 127 else 0)
    box = row.getbbox()
    return None if box is None else (box[0], box[2] - 1)


class NikonZ:
    """Nikon's barred Z, cut out of whatever face the caption is set in.

    Two steps, a measurement apart: `marked` finds the one Z that is part of
    a body name and swaps in a private-use stand-in, and `glyph` turns that
    stand-in into a mask when the line is finally drawn.
    """

    GAP = 0.34            # gap width, as a multiple of an arm
    SUPERSAMPLE = 6       # ... the gap is cut this much bigger, then averaged

    # Nikon writes the Z of its mirrorless bodies as a Z with a bar struck
    # through it.  Only the body's own Z counts: the Z in "NIKKOR Z DX 16-50mm"
    # is a plain letter, and so is a Z anywhere else in the caption.
    _PATTERN = re.compile(r"(?<![0-9A-Za-z])Z(?=\d|f[cC]?(?![0-9A-Za-z]))")

    @staticmethod
    def marked(text):
        """`text` with the Z of a Nikon Z body marked for `glyph`."""
        text = text or ""
        if "nikon" not in text.lower():
            return text
        return NikonZ._PATTERN.sub(Z_MARK, text, count=1)

    @staticmethod
    def _arm(mask):
        """How thick the Z's arms are: the full-width run at the top of the mask."""
        full = _ink_span(mask, 0)
        if full is None:
            return 0
        for y in range(mask.height):
            span = _ink_span(mask, y)
            if span is None or span[1] - span[0] < (full[1] - full[0]) * 0.8:
                return y
        return mask.height

    @staticmethod
    def glyph(font):
        """Nikon's Z: this face's own Z with its diagonal split in two, as a mask.

        The Z series is written with a double-struck Z -- blackboard bold, the
        mathematician's Z -- and no system sans carries that character.  Rather
        than borrow a foreign face for one letter, the hairline is cut into the
        face's own Z, so the glyph keeps the weight, the width and the cap height
        of the type it stands in.  The cut follows the diagonal itself, found by
        reading the ink back off the rendered glyph, so it works on any face.

        Handed back as a baseline-standing mark, one advance wide and cap tall,
        so it drops straight into a glyph plan beside real letters and picks up
        the line's lightness and alpha like any other glyph.
        """
        cap = max(1, int(math.ceil(cap_height(font))))
        x1 = font.getbbox("Z", anchor="ls")[2]
        width = max(1, int(math.ceil(max(font.getlength("Z"), x1))))
        mask = Image.new("L", (width, cap), 0)
        pen = ImageDraw.Draw(mask)
        pen.text((0, cap), "Z", font=font, fill=255, anchor="ls")

        arm = NikonZ._arm(mask)
        gap = max(1.0, arm * NikonZ.GAP)
        top, bottom = int(round(arm + gap)), int(round(cap - arm - gap))
        spans = [_ink_span(mask, top), _ink_span(mask, bottom)]
        if bottom - top < 2 or None in spans:
            return mask                 # not a Z we recognise: leave it be
        centre = [(span[0] + span[1]) / 2.0 for span in spans]
        # the gap is cut on a supersampled layer and averaged down, so the
        # hairline has soft edges even where it is barely a pixel wide
        step = max(1, min(NikonZ.SUPERSAMPLE, int(2000 / max(width, cap))))
        cut = Image.new("L", (width * step, cap * step), 0)
        ImageDraw.Draw(cut).line([((centre[0] + 0.5) * step, (top + 0.5) * step),
                                  ((centre[1] + 0.5) * step, (bottom + 0.5) * step)],
                                 fill=255, width=max(1, int(round(gap * step))))
        if step > 1:
            cut = cut.resize((width, cap), Image.BOX)
        return ImageChops.subtract(mask, cut)


def plan_title(text, font, fallback, tracking, mark=None):
    """`plan_text`, except that `mark`'s maker name, where it opens `text`,
    becomes that maker's logo.

    The logo stands on the same baseline as the type and runs MARK_HEIGHT cap
    heights tall, so it reads as one more word on the line rather than as a
    pasted-in picture.  `mark` is whatever `resolve_mark` decided; without
    one -- or if that maker's name turns out not to open the caption after
    all -- this is plain text.
    """
    found = logos.LIBRARY.split_leading(text, mark) if mark else None
    if not found:
        return plan_text(text, font, fallback, tracking)
    name, rest = found
    ascent, descent = font.getmetrics()
    fit = logos.LIBRARY.fit(name)
    image = logos.LIBRARY.mask(name, cap_height(font) * MARK_HEIGHT * fit.scale)
    if image is None:
        return plan_text(text, font, fallback, tracking)

    below = image.height * fit.drop   # a descender in the mark, in pixels
    items, width = [((image, below), None, 0.0)], float(image.width)
    if rest:
        gap = font.getlength(BlurStyle.TITLE_GAP)   # the same air the two spaces gave
        glyphs, tail, (ascent, descent) = plan_text(rest, font, fallback, tracking)
        items += [(char, face, offset + width + gap) for char, face, offset in glyphs]
        width += gap + tail
    return items, width, (max(ascent, image.height - below), max(descent, below))


def draw_plan(mask, origin, glyphs, fill):
    """Stamp a plan into the caption mask.  An item with no face is a logo."""
    pen = ImageDraw.Draw(mask)
    x, y = origin
    for item, font, offset in glyphs:
        if font is None:
            paste_mark(mask, item, (x + offset, y), fill)
        elif not item.isspace():
            pen.text((x + offset, y), item, font=font, fill=fill, anchor="ls")


def paste_mark(mask, mark, origin, fill):
    """Composite a logo into the caption mask, standing on the baseline.

    `mark` is the mask and how far it hangs below that baseline.  Lightened in
    rather than pasted, so it can never rub out type it overlaps, and scaled
    by `fill` so it carries the segment's own alpha.
    """
    image, below = mark
    left, bottom = int(round(origin[0])), int(round(origin[1] + below))
    box = (left, bottom - image.height, left + image.width, bottom)
    inked = image.point(lambda v: v * fill // 255) if fill < 255 else image
    mask.paste(ImageChops.lighter(mask.crop(box), inked), box[:2])


def auto_text_light(backdrop, box):
    """Pure white or pure black, whichever reads better behind the caption.

    The mean luma of the backdrop under the text decides it, so a night shot
    gets white and a snow shot gets black.  The slider can still be dragged
    to any grey in between; this only picks the automatic default.
    """
    try:
        patch = backdrop.crop(box).convert("L")
        mean = ImageStat.Stat(patch).mean[0] / 255.0
    except Exception:
        return 100.0
    return 0.0 if mean > BlurStyle.AUTO_LIGHT_THRESHOLD else 100.0


class Caption:
    """The caption under a blurred-backdrop frame: its layout, and its ink.

    Built once from the five things that decide it -- the params, the two
    strings, the photo's short side and which maker's logo to set -- because
    the height the canvas needs and the pixels drawn into it have to come
    from exactly the same measurements.  `layout` asks for the height before
    there is a canvas; `render` asks for the ink once there is one.
    """

    def __init__(self, params, title, settings, short_side, mark=None):
        self.params = params
        self.title = title
        self.settings = settings
        self.short_side = short_side
        self.mark = mark

    def lines(self):
        """Lay the caption out as lines of baseline-aligned segments.

        Two-line mode gives the title its own larger line.  One-line mode keeps
        the title bigger and bolder but puts it on the same baseline as the
        settings, separated by whitespace rather than another pipe.
        """
        params, short_side, mark = self.params, self.short_side, self.mark
        scale = min(max(_num(params.text_scale) or 1.0, 0.2), 4.0)
        one = bool(params.one_line)
        title_pct = (BlurStyle.TITLE_PCT_ONE if one else BlurStyle.TITLE_PCT) * scale
        params_pct = (BlurStyle.PARAMS_PCT_ONE if one else BlurStyle.PARAMS_PCT) * scale

        def segment(text, pct, weight, alpha, titled=False):
            size = max(0.2, pct) / 100.0 * short_side
            font = FONTS.sans(size, weight)
            tracking = BlurStyle.TRACKING_EM * font.size
            cjk = FONTS.cjk(size, weight)
            glyphs, width, (ascent, descent) = \
                plan_title(text, font, cjk, tracking, mark) if titled \
                else plan_text(text, font, cjk, tracking)
            return {"text": text, "font": font, "alpha": alpha, "glyphs": glyphs,
                    "width": width, "ascent": ascent, "descent": descent}

        head = segment(self.title, title_pct, BlurStyle.TITLE_WEIGHT, 1.0,
                       titled=True) if self.title else None
        tail = segment(self.settings, params_pct, BlurStyle.PARAMS_WEIGHT,
                       BlurStyle.PARAMS_ALPHA) if self.settings else None

        if one:
            row = [s for s in (head, tail) if s]
            return [row] if row else []
        return [[s] for s in (head, tail) if s]

    def _measure(self, rows):
        """Pixel height of the laid-out caption, and per-row metrics."""
        gap = BlurStyle.SEGMENT_GAP_PCT / 100.0 * self.short_side
        line_gap = BlurStyle.LINE_GAP_PCT / 100.0 * self.short_side
        metrics, total = [], 0.0
        for index, row in enumerate(rows):
            ascent = max(s["ascent"] for s in row)
            descent = max(s["descent"] for s in row)
            width = sum(s["width"] for s in row) + gap * (len(row) - 1)
            metrics.append({"row": row, "ascent": ascent, "height": ascent + descent,
                            "width": width, "gap": gap})
            total += ascent + descent + (line_gap if index else 0.0)
        return metrics, total, line_gap

    def height(self):
        rows = self.lines()
        if not rows:
            return 0.0
        return self._measure(rows)[1]

    def draw(self, canvas, region, report=None):
        """Composite the caption, vertically centred in `region`.

        `report`, if given, receives the lightness actually used, so the UI can
        show where "auto" landed on the slider.
        """
        rows = self.lines()
        if not rows:
            return
        x0, y0, x1, y1 = region
        metrics, total, line_gap = self._measure(rows)
        centre = (x0 + x1) / 2.0
        top = y0 + (y1 - y0 - total) / 2.0

        placed, cursor, widest = [], top, max(m["width"] for m in metrics)
        for index, row in enumerate(metrics):
            cursor += line_gap if index else 0.0
            pen_x = centre - row["width"] / 2.0
            for segment in row["row"]:
                placed.append((pen_x, cursor + row["ascent"], segment))
                pen_x += segment["width"] + row["gap"]
            cursor += row["height"]

        halo_blur = 0.30 / 100.0 * self.short_side
        margin = int(math.ceil(halo_blur * 3 + 2))
        box = (max(0, int(centre - widest / 2.0) - margin),
               max(0, int(top) - margin),
               min(canvas.width, int(math.ceil(centre + widest / 2.0)) + margin),
               min(canvas.height, int(math.ceil(cursor)) + margin))
        if box[2] <= box[0] or box[3] <= box[1]:
            return

        light = (auto_text_light(canvas, box) if self.params.text_auto
                 else min(max(_num(self.params.text_light) or 0.0, 0.0), 100.0))
        level = int(round(light / 100.0 * 255.0))
        if report is not None:
            report["text_light"] = light
        size = (box[2] - box[0], box[3] - box[1])

        mask = Image.new("L", size, 0)
        for x, baseline, segment in placed:
            draw_plan(mask, (x - box[0], baseline - box[1]), segment["glyphs"],
                      int(round(255 * segment["alpha"])))

        patch = canvas.crop(box)
        if halo_blur >= 0.4:
            # a soft counter-shade keeps the caption legible over a busy backdrop
            halo = mask.filter(ImageFilter.GaussianBlur(halo_blur)) \
                       .point(lambda v: int(v * 0.5))
            shade = (0, 0, 0) if level > 128 else (255, 255, 255)
            patch = Image.composite(Image.new("RGB", size, shade), patch, halo)
        patch = Image.composite(Image.new("RGB", size, (level,) * 3), patch, mask)
        canvas.paste(patch, (box[0], box[1]))


# --------------------------------------------------------------------------
# info bar
# --------------------------------------------------------------------------

def _bar_fit(value):
    return min(max(_num(value) or 1.0, 0.1), 8.0)


@dataclass(frozen=True)
class _BarMeasure:
    """The band at one type scale: what it holds, and what room is left.

    Only `bar_plan`'s fitting loop ever sees one of these -- it measures the
    band, reads `room` against `text`, and measures again at a smaller scale
    until the type fits the width.
    """

    rows: list          # [side][line] -> {"glyphs", "width", "alpha"}
    block: float        # cap top of the first line to baseline of the last
    baselines: tuple    # each line's baseline, down from the block's top
    logo: object        # the maker's mark at this size, or None
    lead: float         # where the left column starts
    room: float         # width the two columns have to share
    text: float         # ... and how much of it they want
    height: int         # the band itself


@dataclass(frozen=True)
class BarPlan:
    """A finished info bar: the canvas it needs, and every mark on the band.

    `strokes` are in band-local coordinates, so `layout` can ask for the
    height alone while `render_bar` draws from exactly the same numbers.
    """

    canvas: tuple
    height: int
    strokes: list
    bg: tuple
    ink: tuple


def bar_plan(photo_size, params, texts=None, mark=None):
    """Lay the info bar out: how tall the band is, and every piece of ink in it.

    The band carries four strings -- exposure over lens on the left of a
    rule, body over date hard against the right edge -- with the maker's logo
    ahead of the rule.  Everything is proportional to the photo's width, and
    the band is never shorter than its own contents, so no slider setting can
    clip the type.
    """
    pw, ph = photo_size
    texts = texts or {}
    pct = lambda value: value / 100.0 * pw
    band = _bar_fit(params.bar_width)
    type_scale = _bar_fit(params.bar_text_scale)
    logo_scale = _bar_fit(params.bar_logo_scale)

    margin, air = pct(BarStyle.MARGIN_PCT), pct(BarStyle.MIN_AIR_PCT)
    rule_w = max(1.0, pct(BarStyle.RULE_W_PCT))
    logo_gap, rule_gap = pct(BarStyle.LOGO_GAP_PCT), pct(BarStyle.RULE_GAP_PCT)
    column = [[texts.get("params", ""), texts.get("lens", "")],
              [NikonZ.marked(texts.get("model", "")), texts.get("date", "")]]

    def measure(scale):
        """The band at this type scale, and how much room the type still has."""
        sizes = (pct(BarStyle.SIZE1_PCT) * scale, pct(BarStyle.SIZE2_PCT) * scale)
        weights = (BarStyle.LINE1_WEIGHT, BarStyle.LINE2_WEIGHT)
        alphas = (BarStyle.LINE1_ALPHA, BarStyle.LINE2_ALPHA)
        tracks = (BarStyle.TRACK1_EM, BarStyle.TRACK2_EM)
        fonts = [FONTS.sans(size, weight) for size, weight in zip(sizes, weights)]
        caps = [cap_height(font) for font in fonts]

        rows = [[None, None], [None, None]]
        for side, lines in enumerate(column):
            for line, text in enumerate(lines):
                font = fonts[line]
                cjk = FONTS.cjk(font.size, weights[line])
                glyphs, width, _ = plan_text(text, font, cjk, tracks[line] * font.size)
                rows[side][line] = {"glyphs": glyphs, "width": width,
                                    "alpha": alphas[line]}

        # an empty line takes no room: one line of text sits centred on its own
        used = [any(rows[side][line]["glyphs"] for side in (0, 1)) for line in (0, 1)]
        gap = pct(BarStyle.LINE_GAP_PCT) * scale if all(used) else 0.0
        block = sum(cap for cap, on in zip(caps, used) if on) + gap
        baselines = (caps[0] if used[0] else 0.0, block)

        height = max(int(round(band * pct(BarStyle.H_PCT))),
                     int(math.ceil(block + 2 * air)))
        logo = None
        if mark is not None and block > 0:
            ink = logo_scale * BarStyle.LOGO_FRACTION * block \
                * logos.LIBRARY.fit(mark).scale
            logo = logos.LIBRARY.mask(mark, min(ink, height * BarStyle.CONTENT_SHARE))
        lead = margin + (logo.width + logo_gap + rule_w + rule_gap if logo else 0.0)
        wide = [max(rows[side][0]["width"], rows[side][1]["width"]) for side in (0, 1)]
        room = pw - lead - margin - (pct(BarStyle.COLUMN_GAP_PCT) if all(wide) else 0.0)
        return _BarMeasure(rows=rows, block=block, baselines=baselines, logo=logo,
                           lead=lead, room=room, text=sum(wide), height=height)

    scale, fit = type_scale, measure(type_scale)
    for _ in range(4):   # settle on the largest type that still fits the width
        if fit.room <= 0 or fit.text <= 0:
            break
        want = min(type_scale, scale * fit.room / fit.text)
        if abs(want - scale) < scale * 0.01:
            break
        scale, fit = want, measure(want)
    if 0 < fit.room < fit.text:   # one last pass, downward only
        fit = measure(scale * fit.room / fit.text)

    height, block = fit.height, fit.block
    top = (height - block) / 2.0
    strokes, x = [], fit.lead
    if fit.logo is not None:
        logo = fit.logo
        strokes.append(("mark", margin, (height - logo.height) / 2.0, logo, 1.0))
        rule_x = margin + logo.width + logo_gap
        strokes.append(("rect", (rule_x, top, rule_x + rule_w, top + block),
                        BarStyle.RULE_ALPHA))
    for side, edge in ((0, None), (1, pw - margin)):
        for line, row in enumerate(fit.rows[side]):
            if not row["glyphs"]:
                continue
            start = x if edge is None else edge - row["width"]
            strokes.append(("text", start, top + fit.baselines[line],
                            row["glyphs"], row["alpha"]))

    shade = "black" if str(params.bar_color or "white").lower() == "black" else "white"
    return BarPlan(canvas=(pw, ph + height), height=height, strokes=strokes,
                   bg=BarStyle.BG[shade], ink=BarStyle.INK[shade])


def render_bar(photo, params, texts=None, mark=None):
    """The photo on a flat band of its own, the camera's data set in the band.

    The photo is pasted at 1:1 at the top left; only the band is synthesised.
    Type and logo are stamped into one mask and composited in a single pass,
    so every mark on the band is the same ink at its own alpha.
    """
    plan = bar_plan(photo.size, params, texts, mark)
    canvas = Image.new("RGB", plan.canvas, plan.bg)
    canvas.paste(photo, (0, 0))
    size = (plan.canvas[0], plan.height)
    if size[1] < 1 or not plan.strokes:
        return canvas

    mask = Image.new("L", size, 0)
    pen = ImageDraw.Draw(mask)
    for stroke in plan.strokes:
        kind, alpha = stroke[0], stroke[-1]
        fill = int(round(255 * min(max(alpha, 0.0), 1.0)))
        if kind == "text":
            draw_plan(mask, (stroke[1], stroke[2]), stroke[3], fill)
        elif kind == "mark":
            paste_mark(mask, (stroke[3], 0.0), (stroke[1], stroke[2] + stroke[3].height),
                       fill)
        else:
            pen.rectangle(stroke[1], fill=fill)

    region = (0, photo.height, size[0], photo.height + size[1])
    patch = Image.composite(Image.new("RGB", size, plan.ink),
                            canvas.crop(region), mask)
    canvas.paste(patch, (0, photo.height))
    return canvas


# --------------------------------------------------------------------------
# main render
# --------------------------------------------------------------------------

def _shadow_mask(canvas_size, rect, radius, blur, opacity):
    """Soft rounded-rectangle alpha mask, rendered at a reduced resolution
    when the blur is wide enough to hide the resampling."""
    cw, ch = canvas_size
    k = 1.0
    if blur >= 2.0:
        k = max(0.03, min(1.0, 30.0 / blur, 1400.0 / max(cw, ch)))
    width, height = max(4, round(cw * k)), max(4, round(ch * k))
    mask = Image.new("L", (width, height), 0)
    scaled = [v * k for v in rect]
    ImageDraw.Draw(mask).rounded_rectangle(scaled, radius=max(0.0, radius * k), fill=255)
    if blur * k >= 0.4:
        mask = mask.filter(ImageFilter.GaussianBlur(blur * k))
    if k != 1.0:
        mask = mask.resize((cw, ch), Image.BICUBIC)
    factor = min(max(opacity, 0.0), 100.0) / 100.0
    if factor < 1.0:
        mask = mask.point(lambda v: int(v * factor))
    return mask


def layout(photo_size, params, title="", settings="", mark=None, texts=None):
    """Work out the canvas.

    Left, right and top all get the same margin.  The bottom band is the
    caption plus that same margin, split evenly above and below the text --
    so the whitespace around the caption *adds up* to one border width and
    the caption sits vertically centred in the band.

    The info-bar style has no margins at all: the photo keeps its own edges
    and the band is simply hung underneath it.
    """
    pw, ph = photo_size
    if is_bar(params):
        plan = bar_plan(photo_size, params, texts, mark)
        return {"pad": 0, "bottom": plan.height, "short_side": min(pw, ph),
                "radius": 0.0, "canvas": plan.canvas}
    short_side = min(pw, ph)
    border = min(max(_num(params.border) or 1.0, 0.05), 8.0)
    pad = int(round(border * BlurStyle.BASE_PAD / 100.0 * short_side))
    caption = Caption(params, title, settings, short_side, mark).height()
    bottom = pad + int(math.ceil(caption))
    radius = min(max(_num(params.radius) or 0.0, 0.0), 100.0) / 100.0 * BlurStyle.BASE_RADIUS \
        / 100.0 * short_side
    return {
        "pad": pad, "bottom": bottom, "short_side": short_side,
        "radius": min(radius, short_side / 2.0),
        "canvas": (pw + 2 * pad, ph + pad + bottom),
    }


def render(photo, params, title="", settings="", report=None, mark=None, texts=None):
    """Compose the framed image.  `photo` is pasted at 1:1, never resampled.

    `mark` names the maker logo to set in place of the maker's name -- see
    `resolve_mark`, which is the one place that decides it.  `texts` carries
    the info bar's four strings; the blurred style ignores it.
    """
    if is_bar(params):
        return render_bar(photo, params, texts, mark)

    pw, ph = photo.size
    box = layout(photo.size, params, title, settings, mark)
    pad, short_side, radius = box["pad"], box["short_side"], box["radius"]
    cw, ch = box["canvas"]
    ox = oy = pad

    canvas = make_backdrop(photo, (cw, ch), params.blur)

    strength = min(max(_num(params.shadow) or 0.0, 0.0), 100.0)
    if strength > 0:
        scale = strength / 100.0
        blur = (BlurStyle.SHADOW_BLUR_MIN + BlurStyle.SHADOW_BLUR_SPAN * scale) / 100.0 * short_side
        dy = BlurStyle.SHADOW_DY / 100.0 * short_side
        rect = [ox, oy + dy, ox + pw - 1, oy + ph + dy - 1]
        mask = _shadow_mask((cw, ch), rect, radius, blur, BlurStyle.SHADOW_MAX_OPACITY * scale)
        canvas = Image.composite(Image.new("RGB", (cw, ch), (0, 0, 0)), canvas, mask)

    if radius >= 1.0:
        canvas.paste(photo, (ox, oy), rounded_mask((pw, ph), radius))
    else:
        canvas.paste(photo, (ox, oy))

    Caption(params, title, settings, short_side, mark) \
        .draw(canvas, (0, oy + ph, cw, ch), report)
    return canvas


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------

EXT_FOR_FORMAT = {"png": ".png", "jpeg": ".jpg", "tiff": ".tif"}


def _output_exif(info, size):
    """Carry the source EXIF across, fixing up the pixel-dimension tags."""
    exif = info.get("exif_raw")
    if exif is None or not len(exif):
        return info.get("exif")
    try:
        exif[0x0112] = 1  # orientation already baked into the pixels
        sub = exif.get_ifd(ExifTags.IFD.Exif)
        if sub is not None:
            sub[0xA002], sub[0xA003] = size
        return exif.tobytes()
    except Exception:
        return info.get("exif")


def save_render(image, out, fmt="png", info=None, jpeg_quality=100):
    """Write the frame to a path or a file object, metadata and all."""
    info = info or {}
    fmt = (fmt or "png").lower()
    extra = {}
    if info.get("icc_profile"):
        extra["icc_profile"] = info["icc_profile"]
    exif = _output_exif(info, image.size)
    if exif:
        extra["exif"] = exif
    if info.get("dpi"):
        extra["dpi"] = info["dpi"]

    if fmt == "jpeg":
        base = dict(quality=int(jpeg_quality), subsampling=0, progressive=False, **extra)
        try:
            # Huffman optimisation shaves a few percent, but Pillow sizes its
            # encoder buffer at 2 bytes/pixel for it, which very high-entropy
            # frames can overflow at quality 100 / 4:4:4.
            image.save(out, "JPEG", optimize=True, **base)
        except OSError:
            if hasattr(out, "seek"):
                out.seek(0)
                out.truncate()
            image.save(out, "JPEG", **base)
    elif fmt == "tiff":
        image.save(out, "TIFF", compression="tiff_lzw", **extra)
    else:
        image.save(out, "PNG", compress_level=6, **extra)
    return out


def output_name(src, fmt="png", suffix="_framed"):
    base = os.path.splitext(os.path.basename(src))[0]
    return base + suffix + EXT_FOR_FORMAT.get((fmt or "png").lower(), ".png")


def default_out_path(src, fmt="png", out_dir=None, suffix="_framed"):
    base = os.path.splitext(os.path.basename(src))[0]
    ext = EXT_FOR_FORMAT.get((fmt or "png").lower(), ".png")
    folder = out_dir or os.path.join(os.path.dirname(os.path.abspath(src)), "framed")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, base + suffix + ext)
    index = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{base}{suffix}-{index}{ext}")
        index += 1
    return path


def _compose(src, params, overrides=None):
    photo, info = load_source(src)
    values = exif_values(photo, src) | (overrides or {})
    title, settings = resolve_texts(params, values)
    texts = bar_texts(params, values)
    mark = resolve_mark(params, values, title, texts)
    return render(photo, params, title, settings, mark=mark, texts=texts), info


def render_bytes(src, params, fmt="png", jpeg_quality=100, overrides=None):
    """Full-resolution render, encoded in memory.

    Used by the single-photo export, which hands the bytes straight to the
    browser's downloader: no intermediate file, and nothing in between
    touches the pixels.
    """
    out, info = _compose(src, params, overrides)
    buffer = io.BytesIO()
    save_render(out, buffer, fmt, info, jpeg_quality)
    return buffer.getvalue(), out.size


def render_file(src, params, fmt="png", out_dir=None, out_path=None,
                jpeg_quality=100, overrides=None):
    """Full-resolution render written to disk.  Returns the path and size."""
    out, info = _compose(src, params, overrides)
    target = out_path or default_out_path(src, fmt, out_dir)
    save_render(out, target, fmt, info, jpeg_quality)
    return target, out.size


def downscale_for_preview(photo, long_side=1024):
    """A cheap proxy for the live preview.  Geometry is percentage based, so
    the proxy render is proportionally identical to the full-size one."""
    if max(photo.size) <= long_side:
        return photo
    scale = long_side / float(max(photo.size))
    size = (max(1, round(photo.width * scale)), max(1, round(photo.height * scale)))
    return photo.resize(size, Image.BILINEAR, reducing_gap=2.0)
