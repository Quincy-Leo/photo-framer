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
    check("one line is the default layout", framer.Params().one_line is True)

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
    pad = round(framer.BASE_PAD / 100 * short)
    check("side and top borders are one border width", box["pad"] == pad,
          f'{box["pad"]} vs {pad}')
    caption = framer.caption_height(p, title, settings, short)
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
    expect_pad = round(3.0 * framer.BASE_PAD / 100 * short)
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
    r = int(round(p.radius / 100 * framer.BASE_RADIUS / 100 * short)) + 2
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
    floored = framer.render(photo, framer.Params(blur=framer.BLUR_FLOOR), title, settings)
    check("blur below the floor is clamped to it",
          sharp.tobytes() == floored.tobytes(),
          f"blur=0 renders the same as blur={framer.BLUR_FLOOR:g}")
    strong = framer.render(photo, framer.Params(blur=60), title, settings)
    corner = lambda im: list(im.crop((0, 0, pad, pad)).convert("L").get_flattened_data())
    spread = lambda im: max(corner(im)) - min(corner(im))
    check("more blur flattens the backdrop", spread(strong) < spread(floored),
          f"corner contrast {spread(floored)} -> {spread(strong)}")

    # ---- letter spacing widens the caption ----
    font = framer.load_font(40, "Regular")
    plain_width = font.getlength("DJI pocket 4")
    tracked = framer.tracked_length(font, "DJI pocket 4", framer.TRACKING_EM * 40)
    check("letter spacing widens the caption",
          tracked > plain_width and tracked < plain_width * 1.2,
          f"{plain_width:.0f}px -> {tracked:.0f}px with {framer.TRACKING_EM:g}em tracking")

    # ---- type size is proportional to the photo, never fixed ----
    sizes = {}
    for side in (800, 1600, 4800):
        rows = framer.caption_lines(p, title, settings, side)
        sizes[side] = rows[0][0]["font"].size
    ratios = [size / side * 100 for side, size in sizes.items()]
    check("font size scales with the photo's short side",
          max(ratios) - min(ratios) < 0.1,
          "  ".join(f"short {k}px -> {v}px type ({v / k * 100:.2f}%)"
                    for k, v in sizes.items()))
    check("the tuned title size is the expected fraction of the short side",
          abs(sizes[4800] / 4800 * 100 - framer.TITLE_PCT_ONE) < 0.03,
          f"{sizes[4800] / 4800 * 100:.2f}% vs {framer.TITLE_PCT_ONE}% (one-line default)")
    two_line_row = framer.caption_lines(
        framer.Params(one_line=False), title, settings, 4800)[0]
    check("the two-line layout uses its own larger title size",
          abs(two_line_row[0]["font"].size / 4800 * 100 - framer.TITLE_PCT) < 0.03,
          f'{two_line_row[0]["font"].size / 4800 * 100:.2f}% vs {framer.TITLE_PCT}%')

    # a caption rendered on two different photo sizes occupies the same
    # fraction of the frame -- which is what makes the small preview faithful
    fractions = []
    for side in (600, 3000):
        square_photo = Image.new("RGB", (side, side))
        box = framer.layout(square_photo.size, p, title, settings)
        fractions.append(framer.caption_height(p, title, settings, side) / box["canvas"][1])
    check("the caption keeps the same share of the canvas at any resolution",
          abs(fractions[0] - fractions[1]) < 0.003,
          f"{fractions[0] * 100:.2f}% vs {fractions[1] * 100:.2f}% of canvas height")

    # ---- Chinese in the caption must not come out as tofu boxes ----
    # SF Pro carries no CJK glyphs; without a fallback face every Chinese
    # character renders as the identical .notdef box.
    sf = framer.load_font(60, "Semibold")
    check("the primary face is known not to cover CJK",
          framer._covers(sf, "A") and not framer._covers(sf, "摄"))
    check("the fallback face does cover CJK", framer._covers(framer.load_cjk_font(60), "摄"))

    glyphs, width, _ = framer.plan_text("风光摄影", sf, framer.load_cjk_font(60), 1.8)
    used = {font for _, font, _ in glyphs}
    check("Chinese characters are drawn with the fallback face",
          sf not in used and len(used) == 1 and width > 0,
          f"{len(glyphs)} glyphs, {width:.0f}px wide")

    mixed, mixed_width, metrics = framer.plan_text(
        "DJI 风光", sf, framer.load_cjk_font(60), 1.8)
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
                     ("empty caption", dict(title_auto=False, params_auto=False))]:
        try:
            framer.render(proxy, framer.Params(**kw), title, settings)
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
