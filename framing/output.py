# SPDX-FileCopyrightText: 2026 QuincyLeo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Image encoding, output naming and complete photo rendering."""

from __future__ import annotations

import io
import os

from PIL import ExifTags

from .metadata import CaptionContent, ExifMetadata
from .rendering import FrameRenderer
from .source import ImageSource


class ImageExporter:
    """Encode framed images and allocate output filenames."""

    EXT_FOR_FORMAT = {"png": ".png", "jpeg": ".jpg", "tiff": ".tif"}

    @staticmethod
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

    @staticmethod
    def save(image, out, fmt="png", info=None, jpeg_quality=100):
        """Write the frame to a path or a file object, metadata and all."""
        info = info or {}
        fmt = (fmt or "png").lower()
        extra = {}
        if info.get("icc_profile"):
            extra["icc_profile"] = info["icc_profile"]
        exif = ImageExporter._output_exif(info, image.size)
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

    @staticmethod
    def output_name(src, fmt="png", suffix="_framed"):
        base = os.path.splitext(os.path.basename(src))[0]
        return base + suffix + ImageExporter.EXT_FOR_FORMAT.get((fmt or "png").lower(), ".png")

    @staticmethod
    def default_out_path(src, fmt="png", out_dir=None, suffix="_framed"):
        base = os.path.splitext(os.path.basename(src))[0]
        ext = ImageExporter.EXT_FOR_FORMAT.get((fmt or "png").lower(), ".png")
        folder = out_dir or os.path.join(os.path.dirname(os.path.abspath(src)), "framed")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, base + suffix + ext)
        index = 2
        while os.path.exists(path):
            path = os.path.join(folder, f"{base}{suffix}-{index}{ext}")
            index += 1
        return path


class PhotoFramer:
    """Render source files to encoded bytes or saved images."""

    @staticmethod
    def _compose(src, params, overrides=None):
        photo, info = ImageSource.load(src)
        values = ExifMetadata.read(photo, src) | (overrides or {})
        title, settings = CaptionContent.resolve_texts(params, values)
        texts = CaptionContent.bar_texts(params, values)
        mark = CaptionContent.resolve_mark(params, values, title, texts)
        return FrameRenderer.render(photo, params, title, settings, mark=mark, texts=texts), info

    @staticmethod
    def render_bytes(src, params, fmt="png", jpeg_quality=100, overrides=None):
        """Full-resolution render, encoded in memory.

        Used by the single-photo export, which hands the bytes straight to the
        browser's downloader: no intermediate file, and nothing in between
        touches the pixels.
        """
        out, info = PhotoFramer._compose(src, params, overrides)
        buffer = io.BytesIO()
        ImageExporter.save(out, buffer, fmt, info, jpeg_quality)
        return buffer.getvalue(), out.size

    @staticmethod
    def render_file(src, params, fmt="png", out_dir=None, out_path=None,
                    jpeg_quality=100, overrides=None):
        """Full-resolution render written to disk.  Returns the path and size."""
        out, info = PhotoFramer._compose(src, params, overrides)
        target = out_path or ImageExporter.default_out_path(src, fmt, out_dir)
        ImageExporter.save(out, target, fmt, info, jpeg_quality)
        return target, out.size
