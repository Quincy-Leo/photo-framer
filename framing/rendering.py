# SPDX-FileCopyrightText: 2026 QuincyLeo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Frame effects, caption layout and the two frame renderers."""

from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFilter, ImageStat

from logos import MarkLibrary
from .metadata import number
from .params import BarStyle, BlurStyle, Params
from .typography import FontBook, NikonZ, TextLayout


class FrameEffects:
    """Backgrounds and alpha masks used by the blurred frame."""

    @staticmethod
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

    @staticmethod
    def make_backdrop(photo, canvas_size, blur_strength):
        """A Gaussian-blurred, zoomed, centre-cropped copy of the photo.

        The blur runs at a reduced working resolution and is scaled back up.  The
        reduction factor is chosen so the working-resolution blur radius always
        stays several times wider than the upscale factor, which is what keeps the
        result indistinguishable from blurring at full size -- the earlier fixed
        900px working width turned a mild blur into visible bicubic mush.
        """
        cw, ch = canvas_size
        strength = min(max(number(blur_strength) or 0.0, BlurStyle.BLUR_FLOOR), 100.0)
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

    @staticmethod
    def shadow_mask(canvas_size, rect, radius, blur, opacity):
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

    @staticmethod
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
        scale = min(max(number(params.text_scale) or 1.0, 0.2), 4.0)
        one = bool(params.one_line)
        title_pct = (BlurStyle.TITLE_PCT_ONE if one else BlurStyle.TITLE_PCT) * scale
        params_pct = (BlurStyle.PARAMS_PCT_ONE if one else BlurStyle.PARAMS_PCT) * scale

        def segment(text, pct, weight, alpha, titled=False):
            size = max(0.2, pct) / 100.0 * short_side
            font = FontBook.shared.sans(size, weight)
            tracking = BlurStyle.TRACKING_EM * font.size
            cjk = FontBook.shared.cjk(size, weight)
            glyphs, width, (ascent, descent) = \
                TextLayout.plan_title(text, font, cjk, tracking, mark) if titled \
                else TextLayout.plan_text(text, font, cjk, tracking)
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

        light = (FrameEffects.auto_text_light(canvas, box) if self.params.text_auto
                 else min(max(number(self.params.text_light) or 0.0, 0.0), 100.0))
        level = int(round(light / 100.0 * 255.0))
        if report is not None:
            report["text_light"] = light
        size = (box[2] - box[0], box[3] - box[1])

        mask = Image.new("L", size, 0)
        for x, baseline, segment in placed:
            TextLayout.draw_plan(mask, (x - box[0], baseline - box[1]), segment["glyphs"],
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


@dataclass(frozen=True)
class _BarMeasure:
    """The band at one type scale: what it holds, and what room is left.

    Only BarRenderer.plan's fitting loop sees one of these -- it measures the
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

    `strokes` are in band-local coordinates. FrameRenderer.layout reads the
    height; BarRenderer.render draws from the same measurements.
    """

    canvas: tuple
    height: int
    strokes: list
    bg: tuple
    ink: tuple


class BarRenderer:
    """Measure and draw the information bar below the photo."""

    @staticmethod
    def _fit(value):
        return min(max(number(value) or 1.0, 0.1), 8.0)

    @staticmethod
    def plan(photo_size, params, texts=None, mark=None):
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
        band = BarRenderer._fit(params.bar_width)
        type_scale = BarRenderer._fit(params.bar_text_scale)
        logo_scale = BarRenderer._fit(params.bar_logo_scale)

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
            fonts = [FontBook.shared.sans(size, weight) for size, weight in zip(sizes, weights)]
            caps = [TextLayout.cap_height(font) for font in fonts]

            rows = [[None, None], [None, None]]
            for side, lines in enumerate(column):
                for line, text in enumerate(lines):
                    font = fonts[line]
                    cjk = FontBook.shared.cjk(font.size, weights[line])
                    glyphs, width, _ = TextLayout.plan_text(text, font, cjk, tracks[line] * font.size)
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
                    * MarkLibrary.shared.fit(mark).scale
                logo = MarkLibrary.shared.mask(mark, min(ink, height * BarStyle.CONTENT_SHARE))
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

    @staticmethod
    def render(photo, params, texts=None, mark=None):
        """The photo on a flat band of its own, the camera's data set in the band.

        The photo is pasted at 1:1 at the top left; only the band is synthesised.
        Type and logo are stamped into one mask and composited in a single pass,
        so every mark on the band is the same ink at its own alpha.
        """
        plan = BarRenderer.plan(photo.size, params, texts, mark)
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
                TextLayout.draw_plan(mask, (stroke[1], stroke[2]), stroke[3], fill)
            elif kind == "mark":
                TextLayout.paste_mark(mask, (stroke[3], 0.0), (stroke[1], stroke[2] + stroke[3].height),
                           fill)
            else:
                pen.rectangle(stroke[1], fill=fill)

        region = (0, photo.height, size[0], photo.height + size[1])
        patch = Image.composite(Image.new("RGB", size, plan.ink),
                                canvas.crop(region), mask)
        canvas.paste(patch, (0, photo.height))
        return canvas


class FrameRenderer:
    """Select the frame style and compose the image."""

    @staticmethod
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
        if Params.is_bar(params):
            plan = BarRenderer.plan(photo_size, params, texts, mark)
            return {"pad": 0, "bottom": plan.height, "short_side": min(pw, ph),
                    "radius": 0.0, "canvas": plan.canvas}
        short_side = min(pw, ph)
        border = min(max(number(params.border) or 1.0, 0.05), 8.0)
        pad = int(round(border * BlurStyle.BASE_PAD / 100.0 * short_side))
        caption = Caption(params, title, settings, short_side, mark).height()
        bottom = pad + int(math.ceil(caption))
        radius = min(max(number(params.radius) or 0.0, 0.0), 100.0) / 100.0 * BlurStyle.BASE_RADIUS \
            / 100.0 * short_side
        return {
            "pad": pad, "bottom": bottom, "short_side": short_side,
            "radius": min(radius, short_side / 2.0),
            "canvas": (pw + 2 * pad, ph + pad + bottom),
        }

    @staticmethod
    def render(photo, params, title="", settings="", report=None, mark=None, texts=None):
        """Compose the framed image.  `photo` is pasted at 1:1, never resampled.

        `mark` names the maker logo to set in place of the maker's name -- see
        CaptionContent.resolve_mark. `texts` carries
        the info bar's four strings; the blurred style ignores it.
        """
        if Params.is_bar(params):
            return BarRenderer.render(photo, params, texts, mark)

        pw, ph = photo.size
        box = FrameRenderer.layout(photo.size, params, title, settings, mark)
        pad, short_side, radius = box["pad"], box["short_side"], box["radius"]
        cw, ch = box["canvas"]
        ox = oy = pad

        canvas = FrameEffects.make_backdrop(photo, (cw, ch), params.blur)

        strength = min(max(number(params.shadow) or 0.0, 0.0), 100.0)
        if strength > 0:
            scale = strength / 100.0
            blur = (BlurStyle.SHADOW_BLUR_MIN + BlurStyle.SHADOW_BLUR_SPAN * scale) / 100.0 * short_side
            dy = BlurStyle.SHADOW_DY / 100.0 * short_side
            rect = [ox, oy + dy, ox + pw - 1, oy + ph + dy - 1]
            mask = FrameEffects.shadow_mask((cw, ch), rect, radius, blur, BlurStyle.SHADOW_MAX_OPACITY * scale)
            canvas = Image.composite(Image.new("RGB", (cw, ch), (0, 0, 0)), canvas, mask)

        if radius >= 1.0:
            canvas.paste(photo, (ox, oy), FrameEffects.rounded_mask((pw, ph), radius))
        else:
            canvas.paste(photo, (ox, oy))

        Caption(params, title, settings, short_side, mark) \
            .draw(canvas, (0, oy + ph, cw, ch), report)
        return canvas
