# SPDX-FileCopyrightText: 2026 QuincyLeo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Verifies the core promise: the photo's own pixels survive untouched.

Run:  python3 tests/test_lossless.py
"""

import io
import math
import os
import sys
import tempfile
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageCms, ImageDraw
from PIL.TiffImagePlugin import IFDRational
import framer
import logos

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))


def make_source(path, size=(600, 400), bias=None):
    """A high-frequency noise pattern: any resampling shows up immediately."""
    img = Image.new("RGB", size)
    pixels = []
    for y in range(size[1]):
        for x in range(size[0]):
            pixels.append(((x * 37 + y * 11) % 256, (x * 7 + y * 53) % 256, (x ^ y) % 256))
    img.putdata(pixels)
    profile = ImageCms.createProfile("sRGB")
    icc = ImageCms.ImageCmsProfile(profile).tobytes()
    exif = Image.Exif()
    exif[0x010F] = "DJI"
    exif[0x0110] = "DJI Pocket 4"
    exif[0x0112] = 1
    sub = exif.get_ifd(0x8769)
    sub[0x829A] = IFDRational(1, 1000)   # ExposureTime
    sub[0x829D] = IFDRational(2, 1)      # FNumber
    sub[0x920A] = IFDRational(200, 10)   # FocalLength
    sub[0x8827] = 50                     # ISO
    sub[0x9003] = "2025:10:12 14:03:22"
    if bias is not None:
        sub[0x9204] = IFDRational(bias.numerator, bias.denominator)
    img.save(path, "PNG", icc_profile=icc, exif=exif.tobytes())
    return path if bias is not None else icc


def mark_height(params, size, values):
    """How tall the logo ends up in the info bar, in pixels."""
    texts = framer.bar_texts(params, values)
    mark = framer.resolve_mark(params, values, "", texts)
    plan = framer.bar_plan(size, params, texts, mark)
    return next((s[3].height for s in plan.strokes if s[0] == "mark"), 0)


def bar_tests(photo, proxy, values):
    """The info-bar style: a flat band under the photo, camera data set in it."""
    nikon = dict(values, brand="NIKON", model="Z50II",
                 lens="NIKKOR Z DX 16-50mm f/2.8 VR")
    bar = framer.Params(style="bar")

    texts = framer.bar_texts(bar, nikon)
    check("the band reads its four strings off the EXIF",
          texts["model"] == "NIKON Z50II"
          and texts["params"] == "20mm, F2, 1/1000s, ISO50"
          and texts["lens"] == nikon["lens"] and texts["date"].startswith("2025-10-12"),
          repr(texts))
    check("the exposure line is written the way a camera back writes it",
          framer.auto_bar_params({"focal": "16.0mm", "aperture": "f/5.6",
                                  "shutter": "1/800", "iso": "ISO100"})
          == "16mm, F5.6, 1/800s, ISO100")
    check("emptying a field drops that line rather than refilling it",
          framer.bar_texts(framer.Params(style="bar", bar_lens_auto=False),
                           nikon)["lens"] == "")

    out = framer.render(photo, bar, texts=texts,
                        mark=framer.resolve_mark(bar, nikon, "", texts))
    check("the photo keeps its own three edges; the band hangs below",
          out.width == photo.width and out.height > photo.height)
    check("the photo's pixels are untouched under the info bar",
          out.crop((0, 0, photo.width, photo.height)).tobytes() == photo.tobytes())

    shares = [framer.bar_plan(size, bar, texts, "nikon").height / size[0]
              for size in (photo.size, proxy.size, (12000, 8000))]
    check("the band keeps the same share of the photo's width at any resolution",
          max(shares) - min(shares) < 0.002,
          "  ".join(f"{s * 100:.2f}%" for s in shares))

    heights = {w: framer.bar_plan(photo.size, framer.Params(style="bar", bar_width=w),
                                  texts, "nikon").height for w in (0.5, 1.0, 3.0)}
    check("the band's height follows the width control",
          heights[0.5] < heights[1.0] < heights[3.0], repr(heights))
    short = {"params": "20mm", "lens": "Z", "model": "Z8", "date": "2025"}
    big = framer.bar_plan(photo.size, framer.Params(style="bar", bar_text_scale=4.0),
                          short, "nikon")
    check("the band grows rather than clip type it cannot hold",
          big.height > heights[1.0], f"{big.height}px vs {heights[1.0]}px")

    logos_at = {s: mark_height(framer.Params(style="bar", bar_logo_scale=s),
                               photo.size, nikon) for s in (1.0, 1.5, 2.5, 5.0)}
    check("the logo grows with its own control until it stands level with the type",
          logos_at[1.0] < logos_at[1.5] and logos_at[2.5] == logos_at[5.0],
          repr(logos_at))
    check("... and past that only a wider band makes it bigger",
          mark_height(framer.Params(style="bar", bar_logo_scale=5.0, bar_width=2.0),
                      photo.size, nikon) > logos_at[5.0])

    z = framer.FONTS.sans(40, framer.BarStyle.LINE1_WEIGHT)
    plain = framer.plan_text("Z", z, z, 0.0)[0]
    split = framer.plan_text(framer.NikonZ.marked("NIKON Z50II"), z, z, 0.0)[0]
    check("Nikon's Z is set as a mark, not as the font's own letter",
          plain[0][1] is z and any(face is None for _, face, _ in split),
          f"{len(split)} items, one of them a mark")
    check("... and the Z of a NIKKOR Z lens stays a plain letter",
          framer.NikonZ.marked("NIKKOR Z DX 16-50mm f/2.8 VR")
          == "NIKKOR Z DX 16-50mm f/2.8 VR"
          and framer.NikonZ.marked("NIKON Zfc") != "NIKON Zfc")
    cut = framer.NikonZ.glyph(z)
    rows = [framer._ink_span(cut, y) for y in range(cut.height)]
    middle = [s for s in rows[len(rows) // 3: len(rows) * 2 // 3] if s]
    check("the split Z is one glyph with a gap cut down its diagonal",
          cut.size[1] == int(math.ceil(framer.cap_height(z)))
          and all(span[1] - span[0] < cut.width * 0.6 for span in middle),
          f"{cut.size[0]}x{cut.size[1]}, diagonal {middle[0][1] - middle[0][0] + 1}px wide")

    corners = []
    for shade in ("white", "black"):
        band = framer.render(proxy, framer.Params(style="bar", bar_color=shade),
                             texts=texts, mark="nikon")
        corners.append(band.getpixel((2, band.height - 2)))
    check("the band is a flat white or a flat black", corners == [(255,) * 3, (0,) * 3],
          repr(corners))


def main():
    tmp = tempfile.mkdtemp(prefix="framer-test-")
    src = os.path.join(tmp, "source.png")
    icc = make_source(src)
    photo, info = framer.load_source(src)
    check("ICC profile read from source", info["icc_profile"] == icc)

    values = framer.exif_values(photo, src)
    check("EXIF parsed", values["brand"] == "DJI" and values["model"] == "Pocket 4"
          and values["shutter"] == "1/1000" and values["aperture"] == "f/2"
          and values["focal"] == "20.0mm" and values["iso"] == "ISO50",
          repr({k: v for k, v in values.items() if v}))
    check("a Nikon mark number is printed the way the camera prints it",
          framer.tidy_model("NIKON", "Z50_2") == "Z50II"
          and framer.tidy_model("NIKON", "Z6_3") == "Z6III"
          and framer.tidy_model("NIKON", "D850") == "D850"
          and framer.tidy_model("SONY", "Z50_2") == "Z50_2",
          "Z50_2 is a field convention, not the name on the body")

    p = framer.Params()
    title, settings = framer.resolve_texts(p, values)
    check("caption built from EXIF",
          title == "DJI  Pocket 4" and settings == "20.0mm  |  f/2  |  1/1000  |  ISO50",
          f"{title!r} / {settings!r}")
    check("maker and model are separated by two spaces",
          title.count(" ") == 3 and "DJI  Pocket" in title, repr(title))
    check("maker and model keep the EXIF's own capitalisation",
          framer.auto_title({"brand": "Nikon", "model": "z5_2"}) == "Nikon  z5_2"
          and framer.auto_title({"brand": "FUJIFILM", "model": "X-T5"}) == "FUJIFILM  X-T5")
    check("two lines is the default layout", framer.Params().one_line is False)

    # ---- exposure compensation only shows up when it isn't zero ----
    check("zero EV is left out of the caption", values["ev"] == "",
          f'ev={values["ev"]!r}')
    for bias, want in ((Fraction(7, 10), "+0.7EV"), (Fraction(-4, 3), "-1.3EV"),
                       (Fraction(1, 1), "+1EV"), (Fraction(0, 1), "")):
        shifted = dict(values)
        biased = make_source(os.path.join(tmp, f"ev{bias.numerator}.png"), bias=bias)
        got = framer.exif_values(Image.open(biased))["ev"]
        check(f"EV {float(bias):+.2f} renders as {want!r}", got == want, repr(got))
        if want:
            shifted["ev"] = got
            check(f"EV {want} joins the parameter line",
                  framer.auto_params(shifted).endswith(want),
                  framer.auto_params(shifted))

    typed = framer.Params(title_auto=False, title="MyCam",
                          params_auto=False, params_text="50mm")
    check("typed caption overrides EXIF",
          framer.resolve_texts(typed, values) == ("MyCam", "50mm"))

    # ---- geometry: one border on three sides, caption + one border below ----
    short = min(photo.size)
    out = framer.render(photo, p, title, settings)
    box = framer.layout(photo.size, p, title, settings)
    pad = round(p.border * framer.BlurStyle.BASE_PAD / 100 * short)
    check("side and top borders are one border width", box["pad"] == pad,
          f'{box["pad"]} vs {pad}')
    caption = framer.Caption(p, title, settings, short).height()
    check("bottom band is caption plus exactly one border width",
          box["bottom"] == pad + math.ceil(caption),
          f'bottom {box["bottom"]} = caption {caption:.1f} + pad {pad}')
    check("canvas matches the layout",
          out.size == (photo.width + 2 * pad, photo.height + pad + box["bottom"]),
          f'{out.size}')

    # the whitespace above and below the caption adds up to one border width
    gap_above = box["bottom"] - math.ceil(caption)
    check("caption whitespace above + below equals one border", gap_above == pad,
          f"{gap_above / 2:.0f} + {gap_above / 2:.0f} = {pad}")

    # ---- the border multiplier scales all four sides together ----
    wide = framer.layout(photo.size, framer.Params(border=3.0), title, settings)
    expect_pad = round(3.0 * framer.BlurStyle.BASE_PAD / 100 * short)
    check("border multiplier scales the frame", wide["pad"] == expect_pad,
          f'{wide["pad"]} vs {expect_pad}')

    # ---- one line is shorter than two ----
    two = framer.layout(photo.size, framer.Params(one_line=False), title, settings)
    one = framer.layout(photo.size, framer.Params(one_line=True), title, settings)
    check("one-line layout needs a shorter bottom band", one["bottom"] < two["bottom"],
          f'{one["bottom"]} < {two["bottom"]}')

    # ---- bit exactness, square corners ----
    square = framer.Params(radius=0, shadow=0)
    flat = framer.render(photo, square, title, settings)
    region = flat.crop((pad, pad, pad + photo.width, pad + photo.height))
    check("square corners: photo region is bit-identical",
          region.tobytes() == photo.tobytes())

    # ---- bit exactness inside the rounded corners ----
    rounded = framer.render(photo, p, title, settings)
    r = int(round(p.radius / 100 * framer.BlurStyle.BASE_RADIUS / 100 * short)) + 2
    got = rounded.crop((pad + r, pad, pad + photo.width - r, pad + photo.height))
    want = photo.crop((r, 0, photo.width - r, photo.height))
    check("rounded corners: interior is bit-identical", got.tobytes() == want.tobytes())

    # ---- the four corner arcs are the only altered pixels ----
    full = rounded.crop((pad, pad, pad + photo.width, pad + photo.height))
    differing = sum(1 for a, b in zip(full.tobytes(), photo.tobytes()) if a != b)
    budget = 4 * (r * r) * 3
    check("corner differences stay inside the arcs", 0 < differing < budget,
          f"{differing} differing bytes, arc budget {budget}")

    # ---- PNG export: lossless end to end ----
    png_path, _ = framer.render_file(src, square, "png", out_dir=tmp)
    saved = Image.open(png_path)
    saved.load()
    saved_region = saved.crop((pad, pad, pad + photo.width, pad + photo.height))
    check("PNG export: photo region bit-identical to the original",
          saved_region.tobytes() == photo.tobytes())
    check("PNG export: ICC profile embedded", saved.info.get("icc_profile") == icc)
    saved_exif = saved.getexif()
    check("PNG export: EXIF camera tags survive",
          str(saved_exif.get(0x0110, "")) == "DJI Pocket 4"
          and saved_exif.get_ifd(0x8769).get(0x8827) == 50,
          repr(dict(saved_exif)))
    check("PNG export: orientation normalised", saved_exif.get(0x0112) == 1)
    check("PNG export: EXIF pixel dims updated to the canvas",
          saved_exif.get_ifd(0x8769).get(0xA002) == saved.width)

    # ---- the in-memory render the browser downloads is the same file ----
    payload, size = framer.render_bytes(src, square, "png")
    with open(png_path, "rb") as handle:
        check("render_bytes matches render_file byte for byte",
              payload == handle.read() and size == saved.size,
              f"{len(payload)} bytes, {size}")
    check("the download filename is derived from the source",
          framer.output_name(src, "png") == "source_framed.png"
          and framer.output_name(src, "jpeg") == "source_framed.jpg",
          framer.output_name(src, "png"))

    # ---- JPEG export: metadata survives, pixels are close ----
    jpg_path, _ = framer.render_file(src, square, "jpeg", out_dir=tmp)
    jpg = Image.open(jpg_path)
    jpg.load()
    check("JPEG export: ICC profile embedded", jpg.info.get("icc_profile") == icc)
    check("JPEG export: 4:4:4, no chroma subsampling",
          __import__("PIL.JpegImagePlugin", fromlist=["x"]).get_sampling(jpg) == 0)
    jr = jpg.crop((pad, pad, pad + photo.width, pad + photo.height))
    worst = max(abs(a - b) for a, b in zip(jr.tobytes(), photo.tobytes()))
    check("JPEG export: deviation stays within a couple of levels", worst <= 4,
          f"max channel deviation {worst}/255 on a noise pattern")

    # ---- orientation handling is a lossless rotation ----
    rot_src = os.path.join(tmp, "rot.png")
    ex = Image.Exif()
    ex[0x0112] = 6  # 90 deg CW
    photo.save(rot_src, "PNG", exif=ex.tobytes())
    rot, _ = framer.load_source(rot_src)
    check("EXIF orientation applied losslessly",
          rot.size == (photo.height, photo.width)
          and rot.transpose(Image.ROTATE_90).tobytes() == photo.tobytes())

    # ---- preview proxy matches the export proportionally ----
    proxy = framer.downscale_for_preview(photo, 300)
    small = framer.render(proxy, p, title, settings)
    check("preview proxy keeps the export aspect ratio",
          abs(small.width / small.height - out.width / out.height) < 0.02,
          f"{small.size} vs {out.size}")

    # ---- automatic text lightness is pure white or pure black ----
    auto = framer.Params(text_auto=True)
    for name, colour, expect in (("dark backdrop", (12, 12, 14), 100.0),
                                 ("bright backdrop", (246, 246, 248), 0.0)):
        plain = Image.new("RGB", (400, 300), colour)
        report = {}
        framer.render(plain, auto, title, settings, report)
        got = report.get("text_light", -1)
        check(f"auto text colour on a {name} is {'white' if expect else 'black'}",
              got == expect, f"lightness {got}")

    manual = {}
    framer.render(photo, framer.Params(text_auto=False, text_light=35), title, settings, manual)
    check("manual text lightness is still respected",
          round(manual.get("text_light", 0)) == 35, repr(manual))

    # ---- blur has a floor, and is a real Gaussian ----
    sharp = framer.render(photo, framer.Params(blur=0), title, settings)
    floored = framer.render(photo, framer.Params(blur=framer.BlurStyle.BLUR_FLOOR),
                            title, settings)
    check("blur below the floor is clamped to it",
          sharp.tobytes() == floored.tobytes(),
          f"blur=0 renders the same as blur={framer.BlurStyle.BLUR_FLOOR:g}")
    strong = framer.render(photo, framer.Params(blur=60), title, settings)
    corner = lambda im: list(im.crop((0, 0, pad, pad)).convert("L").get_flattened_data())
    spread = lambda im: max(corner(im)) - min(corner(im))
    check("more blur flattens the backdrop", spread(strong) < spread(floored),
          f"corner contrast {spread(floored)} -> {spread(strong)}")

    # ---- letter spacing widens the caption ----
    font = framer.FONTS.sans(40, "Regular")
    plain_width = font.getlength("DJI pocket 4")
    tracked = framer.tracked_length(font, "DJI pocket 4",
                                    framer.BlurStyle.TRACKING_EM * 40)
    check("letter spacing widens the caption",
          tracked > plain_width and tracked < plain_width * 1.2,
          f"{plain_width:.0f}px -> {tracked:.0f}px with "
          f"{framer.BlurStyle.TRACKING_EM:g}em tracking")

    # ---- type size is proportional to the photo, never fixed ----
    sizes = {}
    for side in (800, 1600, 4800):
        rows = framer.Caption(p, title, settings, side).lines()
        sizes[side] = rows[0][0]["font"].size
    ratios = [size / side * 100 for side, size in sizes.items()]
    check("font size scales with the photo's short side",
          max(ratios) - min(ratios) < 0.1,
          "  ".join(f"short {k}px -> {v}px type ({v / k * 100:.2f}%)"
                    for k, v in sizes.items()))
    check("the tuned title size is the expected fraction of the short side",
          abs(sizes[4800] / 4800 * 100 - framer.BlurStyle.TITLE_PCT) < 0.03,
          f"{sizes[4800] / 4800 * 100:.2f}% vs "
          f"{framer.BlurStyle.TITLE_PCT}% (two-line default)")
    one_line_row = framer.Caption(
        framer.Params(one_line=True), title, settings, 4800).lines()[0]
    one_line_pct = one_line_row[0]["font"].size / 4800 * 100
    check("the one-line layout uses its own smaller title size",
          abs(one_line_pct - framer.BlurStyle.TITLE_PCT_ONE) < 0.03,
          f"{one_line_pct:.2f}% vs {framer.BlurStyle.TITLE_PCT_ONE}%")

    # a caption rendered on two different photo sizes occupies the same
    # fraction of the frame -- which is what makes the small preview faithful
    fractions = []
    for side in (600, 3000):
        square_photo = Image.new("RGB", (side, side))
        box = framer.layout(square_photo.size, p, title, settings)
        fractions.append(framer.Caption(p, title, settings, side).height() / box["canvas"][1])
    check("the caption keeps the same share of the canvas at any resolution",
          abs(fractions[0] - fractions[1]) < 0.003,
          f"{fractions[0] * 100:.2f}% vs {fractions[1] * 100:.2f}% of canvas height")

    # ---- four makers print their logo where their name would go ----
    # Which logo, if any, is settled once by resolve_mark: the EXIF field
    # first, the caption's own text only when the title was typed by hand.
    auto = framer.Params()
    typed = framer.Params(title_auto=False, title="FUJIFILM X-T5")
    check("the logo is keyed off the EXIF maker",
          framer.resolve_mark(auto, values) == "dji"
          and framer.resolve_mark(auto, {"brand": "SONY", "model": "ILCE-7M3"}) is None,
          "Make picks the mark; a maker we have no mark for stays text")
    check("a stripped Make falls back to the Model field",
          framer.resolve_mark(auto, {"brand": "", "model": "NIKON D850"}) == "nikon",
          "Nikon and Canon repeat the brand inside Model, and Make is the tag "
          "editors drop")
    check("a hand-typed title is matched on its own text instead",
          framer.resolve_mark(typed, {"brand": "DJI"}, "FUJIFILM X-T5") == "fujifilm"
          and framer.resolve_mark(framer.Params(title_auto=False),
                                  {"brand": "DJI"}, "My camera") is None,
          "typing a maker gets its logo; typing anything else drops it")
    check("the maker's name is taken back off the front of the caption",
          logos.LIBRARY.split_leading("FUJIFILM  X-T5", "fujifilm")
          == ("fujifilm", "X-T5")
          and logos.LIBRARY.split_leading("NIKON CORPORATION  Z8")
          == ("nikon corporation", "Z8")
          and logos.LIBRARY.split_leading("Nikonos V") is None,
          "and a word that merely starts with a maker's letters stays text")

    marked = framer.Caption(p, "DJI  Pocket 4", settings, 1600, "dji").lines()[0][0]
    unmarked = framer.Caption(p, "DJI  Pocket 4", settings, 1600).lines()[0][0]
    marks = [item for item, face, _ in marked["glyphs"] if face is None]
    check("the maker's name becomes one logo and the model stays type",
          len(marks) == 1
          and "".join(c for c, face, _ in marked["glyphs"] if face) == "Pocket 4"
          and not [i for i, face, _ in unmarked["glyphs"] if face is None],
          f'{len(marked["glyphs"])} items with a mark, '
          f'{len(unmarked["glyphs"])} glyphs without one')

    image, below = marks[0]
    cap = framer.cap_height(marked["font"])
    check(f"the logo stands {framer.MARK_HEIGHT:g} cap heights tall on the baseline",
          abs((image.height - below) - cap * framer.MARK_HEIGHT) <= 2,
          f"{image.height - below:.0f}px above the baseline vs {cap:.0f}px cap "
          f"height x {framer.MARK_HEIGHT:g}")
    check("the caption band makes room for a logo taller than the type",
          marked["ascent"] >= image.height - below and marked["descent"] >= below,
          f'ascent {marked["ascent"]:.0f}px, descent {marked["descent"]:.0f}px')

    small = logos.LIBRARY.mask("fujifilm", 40)
    large = logos.LIBRARY.mask("fujifilm", 400)
    check("the logo is filled at the size asked for, not scaled from a bitmap",
          abs(small.width / small.height - large.width / large.height) < 0.01
          and large.getextrema() == (0, 255),
          f"{small.size} and {large.size} share one aspect ratio")

    def subpaths(name):
        letters, counters, _ = logos.LIBRARY.outline(name)
        return len(letters), len(counters)
    letters, holes = subpaths("nikon")
    overlapping, none = subpaths("fujifilm")
    check("a counter inside a letter is punched out; overlapping letters are not",
          holes == 1 and none == 0,
          f"nikon: {letters} subpaths of ink, {holes} counter   "
          f"fujifilm: {overlapping} of ink, {none} counters, so the JI overlap "
          f"stays ink")

    # same photo, same caption, same parameters: only the mark differs
    check("the logo reaches the canvas",
          framer.render(proxy, p, "FUJIFILM  X-T5", settings,
                        mark="fujifilm").tobytes()
          != framer.render(proxy, p, "FUJIFILM  X-T5", settings).tobytes())

    # ---- Chinese in the caption must not come out as tofu boxes ----
    # SF Pro carries no CJK glyphs; without a fallback face every Chinese
    # character renders as the identical .notdef box.
    sf = framer.FONTS.sans(60, "Semibold")
    check("the primary face is known not to cover CJK",
          framer.FONTS.covers(sf, "A") and not framer.FONTS.covers(sf, "摄"))
    check("the fallback face does cover CJK", framer.FONTS.covers(framer.FONTS.cjk(60), "摄"))

    glyphs, width, _ = framer.plan_text("风光摄影", sf, framer.FONTS.cjk(60), 1.8)
    used = {font for _, font, _ in glyphs}
    check("Chinese characters are drawn with the fallback face",
          sf not in used and len(used) == 1 and width > 0,
          f"{len(glyphs)} glyphs, {width:.0f}px wide")

    mixed, mixed_width, metrics = framer.plan_text(
        "DJI 风光", sf, framer.FONTS.cjk(60), 1.8)
    faces = {font.getname()[0] for char, font, _ in mixed if not char.isspace()}
    check("mixed Latin and Chinese each use their own face", len(faces) == 2,
          " + ".join(sorted(faces)))
    check("a mixed line is laid out left to right without overlap",
          all(b[2] > a[2] for a, b in zip(mixed, mixed[1:])) and mixed_width > 0,
          f"{mixed_width:.0f}px wide, line height {sum(metrics)}px")

    rendered = {}
    for label, text in (("latin", "DJI pocket 4"), ("chinese", "风光摄影 测试")):
        canvas = framer.render(proxy, framer.Params(), text, "20mm")
        rendered[label] = canvas
    check("a Chinese caption renders different pixels than a Latin one",
          rendered["latin"].tobytes() != rendered["chinese"].tobytes())

    bar_tests(photo, proxy, values)

    # ---- degenerate parameters must not crash ----
    for name, kw in [("tiny border", dict(border=0.1)),
                     ("huge border", dict(border=5.0)),
                     ("square corners", dict(radius=0)),
                     ("max radius", dict(radius=100)),
                     ("no shadow", dict(shadow=0)),
                     ("max shadow", dict(shadow=100)),
                     ("no blur", dict(blur=0)),
                     ("max blur", dict(blur=100)),
                     ("one line", dict(one_line=True)),
                     ("huge text", dict(text_scale=2.5)),
                     ("empty caption", dict(title_auto=False, params_auto=False)),
                     ("info bar", dict(style="bar")),
                     ("info bar, narrowest band", dict(style="bar", bar_width=0.5)),
                     ("info bar, widest band", dict(style="bar", bar_width=5.0)),
                     ("info bar, huge type", dict(style="bar", bar_text_scale=5.0)),
                     ("info bar, tiny type", dict(style="bar", bar_text_scale=0.5)),
                     ("info bar, huge logo", dict(style="bar", bar_logo_scale=5.0)),
                     ("info bar, black band", dict(style="bar", bar_color="black")),
                     ("info bar, nothing to say",
                      dict(style="bar", bar_params_auto=False, bar_lens_auto=False,
                           bar_model_auto=False, bar_date_auto=False))]:
        try:
            edge = framer.Params(**kw)
            framer.render(proxy, edge, title, settings,
                          mark=framer.resolve_mark(edge, values),
                          texts=framer.bar_texts(edge, values))
            check(f"edge case: {name}", True)
        except Exception as exc:
            check(f"edge case: {name}", False, f"{type(exc).__name__}: {exc}")

    blank = framer.layout(photo.size, framer.Params(title_auto=False, params_auto=False))
    check("no caption means no extra bottom band", blank["bottom"] == blank["pad"],
          f'bottom {blank["bottom"]} vs pad {blank["pad"]}')

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("failed: " + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
