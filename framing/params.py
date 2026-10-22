# SPDX-FileCopyrightText: 2026 QuincyLeo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Frame styles and user-adjustable settings."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields

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


@dataclass
class Params:
    """User-adjustable settings for both frame styles."""

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

    def is_bar(self):
        """Whether this is the info-bar style rather than the blurred backdrop."""
        return str(getattr(self, "style", "blur") or "blur").lower() == "bar"
