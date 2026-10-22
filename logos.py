# SPDX-FileCopyrightText: 2026 QuincyLeo
# SPDX-License-Identifier: GPL-3.0-or-later
"""SVG maker marks, polygon geometry and cached logo masks."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw

__all__ = ["Mark", "MarkLibrary", "SvgPath", "PolygonGeometry", "LIBRARY"]


@dataclass(frozen=True)
class Mark:
    """One maker's mark: its artwork, and how that artwork fits the type.

    The three numbers are documented on MARKS below, where they were measured.
    """

    file: str
    scale: float = 1.0
    drop: float = 0.0
    stretch: float = 1.0


class SvgPath:
    """Flatten SVG path commands into closed polygons."""

    CHORDS_PER_UNIT = 128
    MAX_CHORDS = 64
    TOKEN = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])"
                       r"|([-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?)")
    ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7}

    @staticmethod
    def tokens(data):
        """A path's `d` attribute as a flat list of commands and numbers."""
        return [command or float(number) for command, number in SvgPath.TOKEN.findall(data)]

    @staticmethod
    def chords(span):
        """How many straight chords a curve of this user-unit length deserves."""
        return max(2, min(SvgPath.MAX_CHORDS, int(math.ceil(span * SvgPath.CHORDS_PER_UNIT))))

    @staticmethod
    def cubic(p0, p1, p2, p3):
        (x0, y0), (x1, y1), (x2, y2), (x3, y3) = p0, p1, p2, p3
        steps = SvgPath.chords(math.dist(p0, p1) + math.dist(p1, p2) + math.dist(p2, p3))
        out = []
        for step in range(1, steps + 1):
            t = step / steps
            u = 1.0 - t
            a, b, c, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
            out.append((a * x0 + b * x1 + c * x2 + d * x3,
                        a * y0 + b * y1 + c * y2 + d * y3))
        return out

    @staticmethod
    def quad(p0, p1, p2):
        (x0, y0), (x1, y1), (x2, y2) = p0, p1, p2
        steps = SvgPath.chords(math.dist(p0, p1) + math.dist(p1, p2))
        out = []
        for step in range(1, steps + 1):
            t = step / steps
            u = 1.0 - t
            a, b, c = u * u, 2 * u * t, t * t
            out.append((a * x0 + b * x1 + c * x2, a * y0 + b * y1 + c * y2))
        return out

    @staticmethod
    def arc(p0, rx, ry, rotation, large, sweep, p1):
        """An SVG elliptical arc, flattened.

        Endpoint to centre parameterisation, following the conversion in the SVG
        spec (appendix F.6.5), then sampled along the sweep.
        """
        x0, y0 = p0
        x1, y1 = p1
        rx, ry = abs(rx), abs(ry)
        if rx < 1e-9 or ry < 1e-9 or (x0 == x1 and y0 == y1):
            return [p1]
        phi = math.radians(rotation)
        cos_phi, sin_phi = math.cos(phi), math.sin(phi)
        dx, dy = (x0 - x1) / 2.0, (y0 - y1) / 2.0
        ux = cos_phi * dx + sin_phi * dy
        uy = -sin_phi * dx + cos_phi * dy
        over = (ux * ux) / (rx * rx) + (uy * uy) / (ry * ry)
        if over > 1.0:                       # radii too small for the endpoints
            rx, ry = rx * math.sqrt(over), ry * math.sqrt(over)
        denominator = rx * rx * uy * uy + ry * ry * ux * ux
        lead = math.sqrt(max(0.0, (rx * rx * ry * ry - denominator) / denominator))
        if bool(large) == bool(sweep):
            lead = -lead
        cxu, cyu = lead * rx * uy / ry, -lead * ry * ux / rx
        cx = cos_phi * cxu - sin_phi * cyu + (x0 + x1) / 2.0
        cy = sin_phi * cxu + cos_phi * cyu + (y0 + y1) / 2.0
        start = math.atan2((uy - cyu) / ry, (ux - cxu) / rx)
        span = math.atan2((-uy - cyu) / ry, (-ux - cxu) / rx) - start
        if sweep and span < 0:
            span += 2 * math.pi
        elif not sweep and span > 0:
            span -= 2 * math.pi

        steps = SvgPath.chords(abs(span) * max(rx, ry))
        out = []
        for step in range(1, steps + 1):
            angle = start + span * step / steps
            ex, ey = rx * math.cos(angle), ry * math.sin(angle)
            out.append((cos_phi * ex - sin_phi * ey + cx,
                        sin_phi * ex + cos_phi * ey + cy))
        return out

    @staticmethod
    def outline(data):
        """Flatten one `d` attribute into closed polygons, in SVG user units."""
        tokens = SvgPath.tokens(data)
        polygons, current = [], []
        index, total = 0, len(tokens)
        x = y = first_x = first_y = 0.0
        control = None                       # reflection point for S and T
        command = "M"

        def close():
            if len(current) >= 3:
                polygons.append(current)
            return []

        while index < total:
            token = tokens[index]
            if isinstance(token, str):        # otherwise: an implicit repeat
                command, index = token, index + 1
                if command in "Zz":
                    current, control = close(), None
                    x, y = first_x, first_y
                    continue
            operation, relative = command.upper(), command.islower()
            count = SvgPath.ARGS.get(operation)
            if count is None or index + count > total:
                break
            args = tokens[index:index + count]
            if any(isinstance(arg, str) for arg in args):
                break                         # malformed: stop rather than guess
            index += count
            here = (x, y)

            if operation == "M":
                close()
                x, y = (x + args[0], y + args[1]) if relative else (args[0], args[1])
                first_x, first_y, current, control = x, y, [(x, y)], None
                command = "l" if relative else "L"    # further pairs are linetos
                continue
            if operation in ("L", "H", "V"):
                if operation == "L":
                    x, y = (x + args[0], y + args[1]) if relative \
                        else (args[0], args[1])
                elif operation == "H":
                    x = x + args[0] if relative else args[0]
                else:
                    y = y + args[0] if relative else args[0]
                current.append((x, y))
                control = None
            elif operation in ("C", "S", "Q", "T"):
                pairs = [(args[i], args[i + 1]) for i in range(0, count, 2)]
                points = [(x + a, y + b) if relative else (a, b) for a, b in pairs]
                if operation in ("S", "T"):   # first control point mirrors the last
                    points.insert(0, (2 * x - control[0], 2 * y - control[1])
                                  if control else here)
                curve = SvgPath.cubic if operation in ("C", "S") else SvgPath.quad
                current.extend(curve(here, *points))
                control, (x, y) = points[-2], points[-1]
            else:                                     # A
                end = (x + args[5], y + args[6]) if relative else (args[5], args[6])
                current.extend(SvgPath.arc(here, args[0], args[1], args[2],
                                    args[3], args[4], end))
                control, (x, y) = None, end

        close()
        return polygons


class PolygonGeometry:
    """Measure polygon bounds and nesting."""

    @staticmethod
    def box(polygon):
        return (min(p[0] for p in polygon), min(p[1] for p in polygon),
                max(p[0] for p in polygon), max(p[1] for p in polygon))

    @staticmethod
    def inside(polygon, point):
        """Ray casting: does `point` fall inside this subpath?"""
        px, py = point
        hit, (x0, y0) = False, polygon[-1]
        for x1, y1 in polygon:
            if (y1 > py) != (y0 > py) and px < x0 + (py - y0) / (y1 - y0) * (x1 - x0):
                hit = not hit
            x0, y0 = x1, y1
        return hit

    @staticmethod
    def depth(polygons, boxes, index):
        """How many other subpaths enclose this one.  An odd answer is a counter:
        the hole in an "o", punched back out of the letter around it.

        An enclosing subpath has to contain the whole box *and* the middle of the
        shape.  Two letters that merely overlap -- which wordmarks do where one
        letter is cut into the next -- enclose nothing, so both stay ink; an
        even-odd fill would wrongly cancel the overlap.
        """
        polygon, (bx0, by0, bx1, by1) = polygons[index], boxes[index]
        middle = (sum(p[0] for p in polygon) / len(polygon),
                  sum(p[1] for p in polygon) / len(polygon))
        depth = 0
        for other, (ox0, oy0, ox1, oy1) in enumerate(boxes):
            if other != index and ox0 <= bx0 and oy0 <= by0 and ox1 >= bx1 \
                    and oy1 >= by1 and (ox1 - ox0) * (oy1 - oy0) \
                    > (bx1 - bx0) * (by1 - by0) and PolygonGeometry.inside(polygons[other], middle):
                depth += 1
        return depth


class MarkLibrary:
    """The marks we have, and the two caches that keep them cheap.

    An outline is flattened once per maker and a mask once per (maker, pixel
    height), so a preview and an export of the same photo each pay for their
    own size and nothing more. Both caches live on the instance;
    MarkLibrary.shared supplies the cache used by every renderer.
    """

    SUPERSAMPLE = 8
    PIXEL_BUDGET = 24e6

    LOGO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "assets", "logos")

    # Maker name, exactly as the caption would print it, mapped to its mark file
    # and the three numbers that fit the mark to the type beside it:
    #   scale   -- the mark's whole ink height, as a multiple of the part of it
    #              that should line up with capitals.  A wordmark whose tallest
    #              letter is a capital sits at 1.0; an ascender or a descender in
    #              the mark means the ink runs taller than the letters do.
    #   drop    -- how much of the mark hangs below the baseline, as a fraction of
    #              its own height.  0 means it stands on the baseline.
    #   stretch -- the mark's width, as a multiple of the artwork's own.  1.0
    #              leaves the file alone; above that widens it, which is how a
    #              wordmark drawn condensed to fit an icon grid is set back to the
    #              proportions the maker actually prints it at.
    # Measured off each outline, not guessed.  How large the letters themselves
    # are relative to the caption is framer.MARK_HEIGHT's business, not this
    # table's.
    MARKS = {
        # Simple Icons draws the Nikon wordmark condensed into its 24-unit grid;
        # 1.36 is the aspect ratio Nikon's own wordmark has.
        "nikon": Mark("nikon.svg", 1.04, 0.0, 1.36),     # the i's dot clears the N
        "nikon corporation": Mark("nikon.svg", 1.04, 0.0, 1.36),
        "dji": Mark("dji.svg", 1.35, 0.26, 1.0),         # d ascends, the J tail drops
        "fujifilm": Mark("fujifilm.svg", 1.0, 0.0, 1.0),  # all capitals, flat to flat
        "apple": Mark("apple.svg", 1.16, 0.0, 1.0),      # a symbol, not letters
    }

    def __init__(self):
        self._outlines = {}
        self._masks = {}

    def __contains__(self, name):
        return name in self.MARKS

    def fit(self, name):
        """How `name`'s artwork fits the type beside it -- see MARKS."""
        return self.MARKS[name]

    def split_leading(self, text, name=None):
        """Split `text` into (mark name, the rest) when it opens with a maker we
        have a mark for; None when it does not.

        `name` pins the search to the one mark the EXIF already settled on, so
        all this has to do is take that maker's name back off the front of the
        caption.  Left out, every mark is tried -- which is how a hand-typed
        "FUJIFILM X-T5" still finds its logo.
        """
        stripped = (text or "").strip()
        lowered = stripped.lower()
        for key in [name] if name else sorted(self.MARKS, key=len, reverse=True):
            if key in self.MARKS and lowered.startswith(key) and (
                    len(lowered) == len(key) or lowered[len(key)].isspace()):
                return key, stripped[len(key):].strip()
        return None

    # ---- polygons -> mask ------------------------------------------------

    def outline(self, name):
        """A mark as (ink subpaths, counter subpaths, box), worked out once.

        The box is the ink's own box, not the SVG viewport: the mark is sized and
        placed by the shape the eye sees, never by the padding the icon happened
        to be drawn with.
        """
        cached = self._outlines.get(name)
        if cached is None:
            entry = self.MARKS.get(name)
            if entry is None:
                return None
            polygons = []
            try:
                with open(os.path.join(self.LOGO_DIR, entry.file),
                          encoding="utf-8") as handle:
                    svg = handle.read()
                for data in re.findall(r'\sd="([^"]+)"', svg):
                    polygons.extend(SvgPath.outline(data))
            except OSError:
                polygons = []
            if not polygons:
                self._outlines[name] = ()     # asked and answered: no mark
                return None
            boxes = [PolygonGeometry.box(polygon) for polygon in polygons]
            groups = ([], [])
            for index, polygon in enumerate(polygons):
                groups[PolygonGeometry.depth(polygons, boxes, index) % 2].append(polygon)
            points = [point for polygon in polygons for point in polygon]
            cached = self._outlines[name] = (groups[0], groups[1], (
                min(p[0] for p in points), min(p[1] for p in points),
                max(p[0] for p in points), max(p[1] for p in points)))
        return cached or None

    def mask(self, name, height):
        """A mark as an 8-bit mask: white ink on nothing, `height` pixels tall,
        cropped tight to the ink.  None when there is no mark for `name`.

        Filled at a multiple of the final size and then area-averaged down, which
        is where the antialiasing comes from.  The supersampling factor backs off
        on a very large mark so the working buffer stays bounded.  A mark with a
        `stretch` is filled wide in the first place, not resampled wider
        afterwards, so its outline stays as clean as any other.
        """
        height = max(1, int(round(height)))
        key = (name, height)
        cached = self._masks.get(key)
        if cached is not None:
            return cached
        outline = self.outline(name)
        if outline is None:
            return None
        letters, counters, (x0, y0, x1, y1) = outline
        ink_w, ink_h = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)
        stretch = self.fit(name).stretch
        width = max(1, int(round(height * ink_w / ink_h * stretch)))
        factor = max(2, min(MarkLibrary.SUPERSAMPLE,
                            int(math.sqrt(MarkLibrary.PIXEL_BUDGET / (width * height)))))
        scale = height * factor / ink_h
        wide = scale * stretch
        size = (max(1, int(round(ink_w * wide))), height * factor)

        def fill(polygons):
            """The union of these subpaths: overlaps stay ink."""
            layer = Image.new("L", size, 0)
            pen = ImageDraw.Draw(layer)
            for polygon in polygons:
                pen.polygon([((px - x0) * wide, (py - y0) * scale)
                             for px, py in polygon], fill=255)
            return layer

        ink = fill(letters)
        if counters:
            ink = ImageChops.subtract(ink, fill(counters))

        mask = ink.resize((width, height), Image.BOX)
        if len(self._masks) > 16:             # preview and export sizes, not more
            self._masks.clear()
        self._masks[key] = mask
        return mask


# Retain the original module entry point for callers using the shared cache.
MarkLibrary.shared = MarkLibrary()
LIBRARY = MarkLibrary.shared
