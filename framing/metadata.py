# SPDX-FileCopyrightText: 2026 QuincyLeo
# SPDX-License-Identifier: GPL-3.0-or-later
"""EXIF extraction and caption content."""

from __future__ import annotations

import math
import os
import re

from PIL import ExifTags

from logos import MarkLibrary
from .params import BlurStyle, Params


# Shared scalar conversion for EXIF rationals and rendering parameters.
def number(value):
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


class ExifMetadata:
    """Extract and format the photo's shooting information."""

    TEXT_VARS = ["brand", "model", "focal", "focal35", "aperture", "shutter", "iso", "ev",
                 "date", "time", "lens", "filename", "wxh"]
    BRAND_NAMES = {
        "NIKON CORPORATION": "NIKON", "EASTMAN KODAK COMPANY": "Kodak",
        "OLYMPUS IMAGING CORP.": "OLYMPUS", "OM DIGITAL SOLUTIONS": "OM SYSTEM",
        "CANON": "Canon", "SONY": "SONY", "FUJIFILM": "FUJIFILM", "APPLE": "Apple",
        "DJI": "DJI", "HASSELBLAD": "HASSELBLAD", "PANASONIC": "Panasonic",
        "LEICA CAMERA AG": "Leica", "RICOH IMAGING COMPANY, LTD.": "RICOH",
    }
    NIKON_MODEL = re.compile(r"^(Z\s?\d+)_(\d)$")
    ROMAN_NUMERALS = {"2": "II", "3": "III", "4": "IV"}

    @staticmethod
    def _shutter(value):
        f = number(value)
        if not f or f <= 0:
            return ""
        if f >= 1:
            return f"{f:g}s"
        denominator = round(1.0 / f)
        return f"1/{denominator}"

    @staticmethod
    def tidy_model(brand, model):
        """`model` as the camera itself prints it.

        Nikon writes a mark II body as "Z50_2" in the EXIF and as "Z50II" on the
        body, the box and every page about it; the underscore is a field
        convention, not a name.  Nothing else is rewritten.
        """
        found = ExifMetadata.NIKON_MODEL.match(model) if brand.upper().startswith("NIKON") else None
        return found[1] + ExifMetadata.ROMAN_NUMERALS[found[2]] \
            if found and found[2] in ExifMetadata.ROMAN_NUMERALS else model

    @staticmethod
    def read(img, path=""):
        """Pull the fields used by the text templates out of an image's EXIF."""
        out = dict.fromkeys(ExifMetadata.TEXT_VARS, "")
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
        brand = ExifMetadata.BRAND_NAMES.get(make.upper(), make)
        if brand and model.upper().startswith(brand.upper()):
            model = model[len(brand):].strip(" -_")
        out["brand"], out["model"] = brand, ExifMetadata.tidy_model(brand, model)
        out["lens"] = str(get("LensModel") or "").strip().rstrip("\x00").strip()

        focal = number(get("FocalLength"))
        if focal:
            out["focal"] = f"{focal:.1f}mm"
        focal35 = number(get("FocalLengthIn35mmFilm"))
        if focal35:
            out["focal35"] = f"{focal35:.0f}mm"
        aperture = number(get("FNumber"))
        if aperture is None:
            apex = number(get("ApertureValue"))
            aperture = 2.0 ** (apex / 2.0) if apex else None
        if aperture:
            out["aperture"] = f"f/{round(aperture, 1):g}"
        out["shutter"] = ExifMetadata._shutter(get("ExposureTime"))
        iso = get("ISOSpeedRatings", "PhotographicSensitivity", "ISO")
        iso = number(iso[0] if isinstance(iso, (tuple, list)) and iso else iso)
        if iso:
            out["iso"] = f"ISO{iso:.0f}"

        # exposure compensation: only worth printing when the photographer dialled
        # something in, so a plain 0 EV is left out
        bias = number(get("ExposureBiasValue"))
        if bias is not None and abs(bias) >= 0.01:
            out["ev"] = f"{bias:+.1f}".rstrip("0").rstrip(".") + "EV"

        stamp = str(get("DateTimeOriginal", "DateTime", "DateTimeDigitized") or "")
        m = re.match(r"(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2})", stamp)
        if m:
            out["date"] = f"{m[1]}-{m[2]}-{m[3]}"
            out["time"] = f"{m[4]}:{m[5]}"
        return out


class CaptionContent:
    """Resolve automatic captions, overrides and maker marks."""

    @staticmethod
    def auto_title(values):
        """Maker and model exactly as the camera wrote them, two spaces apart.

        No case folding: whatever is in the EXIF is what gets printed.  The only
        cleanup is dropping corporate suffixes and repeated brand prefixes
        during EXIF extraction.
        """
        brand = str((values or {}).get("brand") or "").strip()
        model = str((values or {}).get("model") or "").strip()
        return BlurStyle.TITLE_GAP.join(part for part in (brand, model) if part)

    @staticmethod
    def auto_params(values):
        """Focal length, aperture, shutter, ISO and -- when it isn't zero -- the
        exposure compensation, pipe separated."""
        values = values or {}
        parts = [values.get(key) for key in ("focal", "aperture", "shutter", "iso", "ev")]
        return BlurStyle.SEPARATOR.join(str(p).strip() for p in parts if str(p or "").strip())

    @staticmethod
    def resolve_texts(params, values):
        """The two caption strings: from EXIF unless the user typed over them."""
        title = CaptionContent.auto_title(values) if params.title_auto else str(params.title or "")
        settings = CaptionContent.auto_params(values) if params.params_auto else str(params.params_text or "")
        return title.strip(), settings.strip()

    @staticmethod
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

    @staticmethod
    def auto_bar_lens(values):
        return str((values or {}).get("lens") or "").strip()

    @staticmethod
    def auto_bar_model(values):
        """Maker and body, one space apart -- "NIKON Z50II"."""
        values = values or {}
        brand = str(values.get("brand") or "").strip()
        model = str(values.get("model") or "").strip()
        return " ".join(part for part in (brand, model) if part)

    @staticmethod
    def auto_bar_date(values):
        values = values or {}
        return " ".join(part for part in (str(values.get("date") or "").strip(),
                                          str(values.get("time") or "").strip()) if part)

    @classmethod
    def bar_slots(cls):
        """Automatic text providers, shared by rendering and session summaries."""
        return {"params": cls.auto_bar_params, "lens": cls.auto_bar_lens,
                "model": cls.auto_bar_model, "date": cls.auto_bar_date}

    @staticmethod
    def bar_texts(params, values):
        """The info bar's four strings: from EXIF unless the user typed over them.

        Emptying a field is a real answer -- that line is simply left out -- so
        "following EXIF" is tracked by its own flag rather than by emptiness.
        """
        out = {}
        for slot, auto in CaptionContent.bar_slots().items():
            following = bool(getattr(params, f"bar_{slot}_auto", True))
            text = auto(values) if following else str(getattr(params, f"bar_{slot}", "") or "")
            out[slot] = text.strip()
        return out

    @staticmethod
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
        if Params.is_bar(params):
            if not params.bar_model_auto:
                typed = (texts or {}).get("model") or params.bar_model or ""
                found = MarkLibrary.shared.split_leading(typed)
                return found[0] if found else None
        elif not params.title_auto:
            found = MarkLibrary.shared.split_leading(title or params.title or "")
            return found[0] if found else None
        brand = str(values.get("brand") or "").strip().lower()
        if brand:
            return brand if brand in MarkLibrary.shared else None
        found = MarkLibrary.shared.split_leading(str(values.get("model") or ""))
        return found[0] if found else None
