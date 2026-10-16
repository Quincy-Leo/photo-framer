# SPDX-FileCopyrightText: 2026 QuincyLeo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Maker marks for the caption.

A handful of makers print their logo where the caption would otherwise print
their name.  Each mark ships as an SVG, is flattened into polygons once, and
is then rasterised at exactly the pixel height the caption asks for -- so the
mark is as sharp as the type beside it in the 1024px preview and in a
100-megapixel export alike.  Nothing here is resolution-dependent.

A mark is a single-colour silhouette stamped into the caption's own text
mask, which means it picks up the caption's lightness, its alpha and its halo
for free.  Any maker not listed in MARKS keeps printing as text.

Only Pillow is needed: the SVG path data is flattened and filled here, so the
tool still has no dependency beyond Pillow.
"""

from __future__ import annotations

import math
import os
import re

from PIL import Image, ImageChops, ImageDraw

LOGO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "logos")

# Maker name, exactly as the caption would print it, mapped to its mark file
# and the two numbers that fit the mark to the type beside it:
#   scale -- the mark's whole ink height, as a multiple of the part of it that
#            should line up with capitals.  A wordmark whose tallest letter is
#            a capital sits at 1.0; an ascender or a descender in the mark
#            means the ink runs taller than the letters do.
#   drop  -- how much of the mark hangs below the baseline, as a fraction of
#            its own height.  0 means it stands on the baseline.
# Measured off each outline, not guessed.  How large the letters themselves
# are relative to the caption is framer.MARK_HEIGHT's business, not this
# table's.
MARKS = {
    "nikon": ("nikon.svg", 1.04, 0.0),           # the i's dot clears the N
    "nikon corporation": ("nikon.svg", 1.04, 0.0),
    "dji": ("dji.svg", 1.35, 0.26),              # d ascends, the J tail drops
    "fujifilm": ("fujifilm.svg", 1.0, 0.0),      # all capitals, flat to flat
    "apple": ("apple.svg", 1.16, 0.0),           # a symbol, not letters
}

CHORDS_PER_UNIT = 128   # curve flattening density, in chords per SVG user unit
MAX_CHORDS = 64         # ... capped, so one long sweep cannot blow up
SUPERSAMPLE = 8         # the mask is filled this much bigger, then area-averaged
PIXEL_BUDGET = 24e6     # ... unless that would need a buffer larger than this

__all__ = ["MARKS", "split_leading", "mark_metrics", "mark_mask"]


def split_leading(text, name=None):
    """Split `text` into (mark name, the rest) when it opens with a maker we
    have a mark for; None when it does not.

    `name` pins the search to the one mark the EXIF already settled on, so
    all this has to do is take that maker's name back off the front of the
    caption.  Left out, every mark is tried -- which is how a hand-typed
    "FUJIFILM X-T5" still finds its logo.
    """
    stripped = (text or "").strip()
    lowered = stripped.lower()
    for key in [name] if name else sorted(MARKS, key=len, reverse=True):
        if key in MARKS and lowered.startswith(key) and (
                len(lowered) == len(key) or lowered[len(key)].isspace()):
            return key, stripped[len(key):].strip()
    return None


def mark_metrics(name):
    """(scale, drop) for a mark -- see MARKS."""
    return MARKS[name][1], MARKS[name][2]


# --------------------------------------------------------------------------
# SVG path -> polygons
# --------------------------------------------------------------------------

_TOKEN = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])"
                    r"|([-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?)")

_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7}


def _tokens(data):
    """A path's `d` attribute as a flat list of commands and numbers."""
    return [command or float(number) for command, number in _TOKEN.findall(data)]


def _chords(span):
    """How many straight chords a curve of this user-unit length deserves."""
    return max(2, min(MAX_CHORDS, int(math.ceil(span * CHORDS_PER_UNIT))))


def _cubic(p0, p1, p2, p3):
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = p0, p1, p2, p3
    steps = _chords(math.dist(p0, p1) + math.dist(p1, p2) + math.dist(p2, p3))
    out = []
    for step in range(1, steps + 1):
        t = step / steps
        u = 1.0 - t
        a, b, c, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
        out.append((a * x0 + b * x1 + c * x2 + d * x3,
                    a * y0 + b * y1 + c * y2 + d * y3))
    return out


def _quad(p0, p1, p2):
    (x0, y0), (x1, y1), (x2, y2) = p0, p1, p2
    steps = _chords(math.dist(p0, p1) + math.dist(p1, p2))
    out = []
    for step in range(1, steps + 1):
        t = step / steps
        u = 1.0 - t
        a, b, c = u * u, 2 * u * t, t * t
        out.append((a * x0 + b * x1 + c * x2, a * y0 + b * y1 + c * y2))
    return out


def _arc(p0, rx, ry, rotation, large, sweep, p1):
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

    steps = _chords(abs(span) * max(rx, ry))
    out = []
    for step in range(1, steps + 1):
        angle = start + span * step / steps
        ex, ey = rx * math.cos(angle), ry * math.sin(angle)
        out.append((cos_phi * ex - sin_phi * ey + cx,
                    sin_phi * ex + cos_phi * ey + cy))
    return out


def _outline(data):
    """Flatten one `d` attribute into closed polygons, in SVG user units."""
    tokens = _tokens(data)
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
        count = _ARGS.get(operation)
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
            curve = _cubic if operation in ("C", "S") else _quad
            current.extend(curve(here, *points))
            control, (x, y) = points[-2], points[-1]
        else:                                     # A
            end = (x + args[5], y + args[6]) if relative else (args[5], args[6])
            current.extend(_arc(here, args[0], args[1], args[2],
                                args[3], args[4], end))
            control, (x, y) = None, end

    close()
    return polygons


# --------------------------------------------------------------------------
# polygons -> mask
# --------------------------------------------------------------------------

_OUTLINES, _MASKS = {}, {}


def _mark_outline(name):
    """A mark as (ink subpaths, counter subpaths, box), worked out once.

    The box is the ink's own box, not the SVG viewport: the mark is sized and
    placed by the shape the eye sees, never by the padding the icon happened
    to be drawn with.
    """
    cached = _OUTLINES.get(name)
    if cached is None:
        entry = MARKS.get(name)
        if entry is None:
            return None
        polygons = []
        try:
            with open(os.path.join(LOGO_DIR, entry[0]), encoding="utf-8") as handle:
                svg = handle.read()
            for data in re.findall(r'\sd="([^"]+)"', svg):
                polygons.extend(_outline(data))
        except OSError:
            polygons = []
        if not polygons:
            _OUTLINES[name] = ()          # asked and answered: no mark
            return None
        boxes = [_box(polygon) for polygon in polygons]
        groups = ([], [])
        for index, polygon in enumerate(polygons):
            groups[_depth(polygons, boxes, index) % 2].append(polygon)
        points = [point for polygon in polygons for point in polygon]
        cached = _OUTLINES[name] = (groups[0], groups[1], (
            min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points)))
    return cached or None


def _box(polygon):
    return (min(p[0] for p in polygon), min(p[1] for p in polygon),
            max(p[0] for p in polygon), max(p[1] for p in polygon))


def _inside(polygon, point):
    """Ray casting: does `point` fall inside this subpath?"""
    px, py = point
    hit, (x0, y0) = False, polygon[-1]
    for x1, y1 in polygon:
        if (y1 > py) != (y0 > py) and px < x0 + (py - y0) / (y1 - y0) * (x1 - x0):
            hit = not hit
        x0, y0 = x1, y1
    return hit


def _depth(polygons, boxes, index):
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
                > (bx1 - bx0) * (by1 - by0) and _inside(polygons[other], middle):
            depth += 1
    return depth


def mark_mask(name, height):
    """A mark as an 8-bit mask: white ink on nothing, `height` pixels tall,
    cropped tight to the ink.  None when there is no mark for `name`.

    Filled at a multiple of the final size and then area-averaged down, which
    is where the antialiasing comes from.  The supersampling factor backs off
    on a very large mark so the working buffer stays bounded.
    """
    height = max(1, int(round(height)))
    key = (name, height)
    cached = _MASKS.get(key)
    if cached is not None:
        return cached
    outline = _mark_outline(name)
    if outline is None:
        return None
    letters, counters, (x0, y0, x1, y1) = outline
    ink_w, ink_h = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)
    width = max(1, int(round(height * ink_w / ink_h)))
    factor = max(2, min(SUPERSAMPLE, int(math.sqrt(PIXEL_BUDGET / (width * height)))))
    scale = height * factor / ink_h
    size = (max(1, int(round(ink_w * scale))), height * factor)

    def fill(polygons):
        """The union of these subpaths: overlaps stay ink."""
        layer = Image.new("L", size, 0)
        pen = ImageDraw.Draw(layer)
        for polygon in polygons:
            pen.polygon([((px - x0) * scale, (py - y0) * scale)
                         for px, py in polygon], fill=255)
        return layer

    ink = fill(letters)
    if counters:
        ink = ImageChops.subtract(ink, fill(counters))

    mask = ink.resize((width, height), Image.BOX)
    if len(_MASKS) > 16:                  # preview and export sizes, not more
        _MASKS.clear()
    _MASKS[key] = mask
    return mask
