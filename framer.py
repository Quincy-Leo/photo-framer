# SPDX-FileCopyrightText: 2026 QuincyLeo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Public photo-framing API.

Implementations live in the framing package. Existing function names remain
as method aliases so scripts importing framer keep the same interface.
"""

from framing.metadata import CaptionContent, ExifMetadata, number
from framing.output import ImageExporter, PhotoFramer
from framing.params import BarStyle, BlurStyle, Params
from framing.rendering import (
    BarPlan, BarRenderer, Caption, FrameEffects, FrameRenderer, _BarMeasure,
)
from framing.source import ImageSource
from framing.typography import FontBook, NikonZ, TextLayout

__all__ = [
    "Params", "TEXT_VARS", "READABLE_EXT", "load_source", "exif_values",
    "resolve_texts", "resolve_mark", "render", "render_file", "render_bytes",
    "save_render", "default_out_path", "output_name", "downscale_for_preview",
    "is_bar", "bar_texts", "auto_bar_params", "auto_bar_lens",
    "auto_bar_model", "auto_bar_date",
    "BlurStyle", "BarStyle", "ImageSource", "ExifMetadata", "CaptionContent",
    "FontBook", "NikonZ", "TextLayout", "Caption", "FrameEffects", "BarPlan",
    "BarRenderer", "FrameRenderer", "ImageExporter", "PhotoFramer",
]

# Compatibility aliases share the implementations and caches used by the classes.
READABLE_HEIF = ImageSource.READABLE_HEIF
READABLE_EXT = ImageSource.READABLE_EXT
TEXT_VARS = ExifMetadata.TEXT_VARS
MARK_HEIGHT = TextLayout.MARK_HEIGHT
Z_MARK = NikonZ.PLACEHOLDER
FONTS = FontBook.shared
BAR_SLOTS = CaptionContent.bar_slots()
_num = number
_shutter = ExifMetadata._shutter
tidy_model = ExifMetadata.tidy_model
exif_values = ExifMetadata.read
load_source = ImageSource.load
downscale_for_preview = ImageSource.preview
auto_title = CaptionContent.auto_title
auto_params = CaptionContent.auto_params
resolve_texts = CaptionContent.resolve_texts
auto_bar_params = CaptionContent.auto_bar_params
auto_bar_lens = CaptionContent.auto_bar_lens
auto_bar_model = CaptionContent.auto_bar_model
auto_bar_date = CaptionContent.auto_bar_date
bar_texts = CaptionContent.bar_texts
resolve_mark = CaptionContent.resolve_mark
tracked_length = TextLayout.tracked_length
plan_text = TextLayout.plan_text
cap_height = TextLayout.cap_height
plan_title = TextLayout.plan_title
draw_plan = TextLayout.draw_plan
paste_mark = TextLayout.paste_mark
_ink_span = NikonZ.ink_span
rounded_mask = FrameEffects.rounded_mask
make_backdrop = FrameEffects.make_backdrop
auto_text_light = FrameEffects.auto_text_light
_shadow_mask = FrameEffects.shadow_mask
_bar_fit = BarRenderer._fit
bar_plan = BarRenderer.plan
render_bar = BarRenderer.render
layout = FrameRenderer.layout
render = FrameRenderer.render
EXT_FOR_FORMAT = ImageExporter.EXT_FOR_FORMAT
_output_exif = ImageExporter._output_exif
save_render = ImageExporter.save
output_name = ImageExporter.output_name
default_out_path = ImageExporter.default_out_path
_compose = PhotoFramer._compose
render_bytes = PhotoFramer.render_bytes
render_file = PhotoFramer.render_file
is_bar = Params.is_bar
