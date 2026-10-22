# SPDX-FileCopyrightText: 2026 QuincyLeo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Font caching, glyph planning and maker-aware text drawing."""

from __future__ import annotations

import math
import os
import re
import sys
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw, ImageFont

from logos import MarkLibrary
from .params import BlurStyle


class FontBook:
    """The faces the caption is set in, kept open and measured once.

    On a Mac the caption is set in the system's own faces, which is what
    makes it look like it belongs there: SF Pro is a variable font, so one
    file answers every weight, and Hiragino carries the CJK glyphs SF Pro has
    none of.  Neither may be redistributed, so everywhere else the two open
    faces in assets/fonts stand in -- Inter for the Latin text, Noto Sans SC
    for the Chinese.  Those travel with the program, so a caption renders the
    same whether or not the machine happens to have any fonts of its own.

    Which set is in use is settled once, in __init__, and everything below
    reads it out of `self.faces`.  The sets cannot be collapsed into one by
    pointing the same paths somewhere else, because the two do not take a
    weight the same way -- see _face.

    Opening a face is expensive and the info bar measures the same band up to
    six times per render, so every face is cached by size and weight.

    FontBook.shared supplies the process-wide cache used by every renderer.
    """

    @dataclass(frozen=True)
    class FaceSet:
        """One platform's pair of faces, and how each is asked for a weight."""

        sans: str
        cjk: str
        cjk_faces: tuple = (0, 0)   # collection index for the light / bold face
        by_name: bool = False       # weight as an instance name, not an axis

    FONT_DIR = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")

    APPLE = FaceSet(sans="/System/Library/Fonts/SFNS.ttf",
                    cjk="/System/Library/Fonts/Hiragino Sans GB.ttc",
                    cjk_faces=(0, 2),      # Hiragino Sans GB W3 / W6
                    by_name=True)
    OPEN = FaceSet(sans=os.path.join(FONT_DIR, "Inter-Variable.ttf"),
                   cjk=os.path.join(FONT_DIR, "NotoSansSC-Variable.ttf"))

    # The weights the caption asks for, and where each one stands on the
    # `wght` axis.  The numbers are only consulted for the open faces; SF Pro
    # is asked by name, because its own instances do not sit on this grid.
    WEIGHTS = {"Ultralight": 100, "Thin": 200, "Light": 300, "Regular": 400,
               "Medium": 500, "Semibold": 600, "Bold": 700, "Heavy": 800,
               "Black": 900}
    HEAVY_WEIGHTS = ("Medium", "Semibold", "Bold", "Heavy", "Black")

    # What `covers` renders to find out what this face's .notdef looks like.
    # A Unicode noncharacter, permanently unassigned, so no face can have a
    # glyph for it.  The private use area will not do: it is unassigned only
    # in the sense that fonts may put whatever they like there, and Inter
    # does exactly that at U+E000.
    NOTDEF_PROBE = "﷐"

    def __init__(self):
        self._cache = {}
        # Settled once.  An Apple face is only worth looking for on an Apple
        # system, and if SFNS.ttf is not where it has always been then this
        # is a stripped install -- not something to re-probe on every one of
        # the thousands of measurements a render makes.
        self.faces = self.APPLE if (sys.platform == "darwin"
                                    and os.path.exists(self.APPLE.sans)) \
            else self.OPEN

    def sans(self, size, weight="Regular"):
        size = max(1, int(round(size)))
        key = (size, weight)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        font = self._face(self.faces.sans, size, weight)
        if font is None:
            font = self._last_resort(size)
        self._cache[key] = font
        return font

    def cjk(self, size, weight="Regular"):
        """Neither Latin face carries CJK glyphs, so Chinese needs a second one."""
        size = max(1, int(round(size)))
        heavy = weight in self.HEAVY_WEIGHTS
        key = ("cjk", size, heavy)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        # Hiragino offers two usable faces, so the caption has only ever
        # asked for one of two bands.  Noto is held to the same two, both to
        # keep the platforms looking alike and because the cache key above
        # already collapses everything else onto them.
        font = self._face(self.faces.cjk, size, "Bold" if heavy else "Regular",
                          index=self.faces.cjk_faces[heavy])
        if font is None:
            font = self.sans(size, weight)
        self._cache[key] = font
        return font

    def _face(self, path, size, weight, index=0):
        """Open one face standing at `weight`, or None if the file is absent.

        This is the one place the two sets genuinely differ, and the reason
        the choice cannot be reduced to swapping the paths over.  SF Pro's
        instances are named exactly as the caption asks for them, so the name
        goes straight in -- and has to, because those names do not sit on the
        standard axis grid: its Semibold is a little lighter than wght 600,
        its Ultralight far lighter than 100, and its weight is the fourth of
        four axes rather than the only one.  Inter and Noto Sans SC spell
        their instances differently and omit some outright ("SemiBold", no
        "Heavy" at all), so there the `wght` axis is set by number instead;
        it also cannot be left alone, since Noto Sans SC's own default is
        Thin and an untouched face would set the caption in hairlines.  Every
        other axis -- Inter's optical size -- keeps its default, and the
        order they come in is read off the face rather than assumed, because
        putting the weight in the wrong slot would set the optical size to
        600 and the weight to nothing.  Hiragino is not variable at all and
        answers only to the face index it was opened with.
        """
        try:
            font = ImageFont.truetype(path, size, index=index)
        except Exception:
            return None
        if self.faces.by_name:
            try:
                font.set_variation_by_name(
                    weight if weight in self.WEIGHTS else "Regular")
            except Exception:
                pass        # static face: the index already chose the weight
            return font
        try:
            axes = font.get_variation_axes()
        except Exception:
            return font     # static face: its weight is already decided
        target = self.WEIGHTS.get(weight, self.WEIGHTS["Regular"])
        values = []
        for axis in axes:
            name = axis.get("name") or b""
            if isinstance(name, bytes):
                name = name.decode("ascii", "ignore")
            if name.strip().lower() in ("weight", "wght"):
                values.append(min(max(target, axis["minimum"]), axis["maximum"]))
            else:
                values.append(axis["default"])
        try:
            font.set_variation_by_axes(values)
        except Exception:
            pass
        return font

    @staticmethod
    def _last_resort(size):
        """Pillow's built-in face, for when assets/fonts has been emptied.

        Asked for without a size it hands back a fixed 10px bitmap, which
        would set a 100-megapixel photo's caption in glyphs too small to see
        rather than visibly failing, so the size is passed through wherever
        Pillow is new enough to take it.
        """
        try:
            return ImageFont.load_default(size)
        except TypeError:
            return ImageFont.load_default()

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
                notdef = bytes(font.getmask(self.NOTDEF_PROBE, mode="L"))
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


# One cache serves all previews and exports in this process.
FontBook.shared = FontBook()


class NikonZ:
    """Nikon's barred Z, cut out of whatever face the caption is set in.

    Two steps, a measurement apart: `marked` finds the one Z that is part of
    a body name and swaps in a private-use stand-in, and `glyph` turns that
    stand-in into a mask when the line is finally drawn.
    """

    PLACEHOLDER = "\ue001"

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
        return NikonZ._PATTERN.sub(NikonZ.PLACEHOLDER, text, count=1)

    @staticmethod
    def _arm(mask):
        """How thick the Z's arms are: the full-width run at the top of the mask."""
        full = NikonZ.ink_span(mask, 0)
        if full is None:
            return 0
        for y in range(mask.height):
            span = NikonZ.ink_span(mask, y)
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
        cap = max(1, int(math.ceil(TextLayout.cap_height(font))))
        x1 = font.getbbox("Z", anchor="ls")[2]
        width = max(1, int(math.ceil(max(font.getlength("Z"), x1))))
        mask = Image.new("L", (width, cap), 0)
        pen = ImageDraw.Draw(mask)
        pen.text((0, cap), "Z", font=font, fill=255, anchor="ls")

        arm = NikonZ._arm(mask)
        gap = max(1.0, arm * NikonZ.GAP)
        top, bottom = int(round(arm + gap)), int(round(cap - arm - gap))
        spans = [NikonZ.ink_span(mask, top), NikonZ.ink_span(mask, bottom)]
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

    @staticmethod
    def ink_span(mask, y):
        """Where the ink starts and ends across one row of a mask, or None."""
        if not 0 <= y < mask.height:
            return None
        row = mask.crop((0, y, mask.width, y + 1)).point(lambda v: 255 if v > 127 else 0)
        box = row.getbbox()
        return None if box is None else (box[0], box[2] - 1)


class TextLayout:
    """Measure and draw baseline-aligned text and logo segments."""

    MARK_HEIGHT = 1.2  # Maker logo height as a multiple of the caption's capitals.

    @staticmethod
    def tracked_length(font, text, tracking):
        """Advance width of `text` once letter spacing is added."""
        width = font.getlength(text)
        return width + max(0, len(text) - 1) * tracking if tracking > 0.01 else width

    @staticmethod
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
        if NikonZ.PLACEHOLDER not in text and FontBook.shared.covers_all(font, text):
            glyphs = [(char, font, font.getlength(text[:index]) + index * tracking)
                      for index, char in enumerate(text)]
            return glyphs, TextLayout.tracked_length(font, text, tracking), font.getmetrics()

        glyphs, cursor, faces = [], 0.0, set()
        for char in text:
            if char == NikonZ.PLACEHOLDER:
                faces.add(font)
                glyphs.append(((NikonZ.glyph(font), 0.0), None, cursor))
                cursor += font.getlength("Z") + tracking
                continue
            face = font if FontBook.shared.covers(font, char) else fallback
            faces.add(face)
            glyphs.append((char, face, cursor))
            cursor += face.getlength(char) + tracking
        ascent = max(f.getmetrics()[0] for f in faces)
        descent = max(f.getmetrics()[1] for f in faces)
        return glyphs, max(0.0, cursor - tracking), (ascent, descent)

    @staticmethod
    def cap_height(font):
        """How tall this face's capitals are, in pixels."""
        try:
            return -font.getbbox("H", anchor="ls")[1] or font.getmetrics()[0]
        except Exception:
            return font.getmetrics()[0] * 0.72

    @staticmethod
    def plan_title(text, font, fallback, tracking, mark=None):
        """`plan_text`, except that `mark`'s maker name, where it opens `text`,
        becomes that maker's logo.

        The logo stands on the same baseline as the type and runs MARK_HEIGHT cap
        heights tall, so it reads as one more word on the line rather than as a
        pasted-in picture.  `mark` is whatever `resolve_mark` decided; without
        one -- or if that maker's name turns out not to open the caption after
        all -- this is plain text.
        """
        found = MarkLibrary.shared.split_leading(text, mark) if mark else None
        if not found:
            return TextLayout.plan_text(text, font, fallback, tracking)
        name, rest = found
        ascent, descent = font.getmetrics()
        fit = MarkLibrary.shared.fit(name)
        image = MarkLibrary.shared.mask(
            name, TextLayout.cap_height(font) * TextLayout.MARK_HEIGHT * fit.scale)
        if image is None:
            return TextLayout.plan_text(text, font, fallback, tracking)

        below = image.height * fit.drop   # a descender in the mark, in pixels
        items, width = [((image, below), None, 0.0)], float(image.width)
        if rest:
            gap = font.getlength(BlurStyle.TITLE_GAP)   # the same air the two spaces gave
            glyphs, tail, (ascent, descent) = TextLayout.plan_text(rest, font, fallback, tracking)
            items += [(char, face, offset + width + gap) for char, face, offset in glyphs]
            width += gap + tail
        return items, width, (max(ascent, image.height - below), max(descent, below))

    @staticmethod
    def draw_plan(mask, origin, glyphs, fill):
        """Stamp a plan into the caption mask.  An item with no face is a logo."""
        pen = ImageDraw.Draw(mask)
        x, y = origin
        for item, font, offset in glyphs:
            if font is None:
                TextLayout.paste_mark(mask, item, (x + offset, y), fill)
            elif not item.isspace():
                pen.text((x + offset, y), item, font=font, fill=fill, anchor="ls")

    @staticmethod
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
