# SPDX-FileCopyrightText: 2026 QuincyLeo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Image decoding, orientation and preview scaling."""

from __future__ import annotations

import os

from PIL import Image, ImageOps, UnidentifiedImageError

Image.MAX_IMAGE_PIXELS = None


class ImageSource:
    """Read source images and prepare preview copies."""

    try:  # Register the optional HEIC/HEIF reader once for the process.
        import pillow_heif

        pillow_heif.register_heif_opener()
        READABLE_HEIF = {".heic", ".heif"}
    except Exception:
        READABLE_HEIF = set()

    READABLE_EXT = {".jpg", ".jpeg", ".jpe", ".png", ".tif", ".tiff", ".webp", ".bmp",
                    ".avif", ".jp2"} | READABLE_HEIF

    @staticmethod
    def load(path):
        """Open an image, apply EXIF orientation, and return (photo, info).

        Orientation is applied with lossless 90-degree rotations / flips.  No
        colour conversion is performed on an already-RGB image, and the ICC
        profile is handed back untouched so the caller can re-embed it.
        """
        try:
            img = Image.open(path)
            img.load()
        except UnidentifiedImageError:
            hint = ""
            if os.path.splitext(path)[1].lower() in (".heic", ".heif"):
                hint = "：HEIC/HEIF 需要先安装 pillow-heif（pip3 install pillow-heif）"
            raise ValueError(f"无法识别的图片格式{hint}") from None

        original_mode = img.mode
        source_format = img.format
        bit_depth = 8
        try:  # TIFF can carry more than 8 bits per channel; Pillow truncates it
            sample = getattr(img, "tag_v2", {}).get(258)
            if sample:
                bit_depth = max(sample) if isinstance(sample, (tuple, list)) else int(sample)
        except Exception:
            pass
        icc = img.info.get("icc_profile")
        dpi = img.info.get("dpi")
        exif_bytes = img.info.get("exif")

        exif_raw = img.getexif()
        orientation = exif_raw.get(0x0112, 1)
        if orientation != 1:
            img = ImageOps.exif_transpose(img)

        converted = False
        if img.mode == "RGBA" or (img.mode == "P" and "transparency" in img.info):
            flat = Image.new("RGB", img.size, (255, 255, 255))
            rgba = img.convert("RGBA")
            flat.paste(rgba, (0, 0), rgba.getchannel("A"))
            img, converted = flat, True
        elif img.mode != "RGB":
            img, converted = img.convert("RGB"), True

        info = {
            "icc_profile": icc,
            "exif": exif_bytes,
            "dpi": dpi,
            "exif_raw": exif_raw,
            "orientation": orientation,
            "source_mode": original_mode,
            "bit_depth": bit_depth,
            "converted": converted,
            "source_format": source_format,
        }
        return img, info

    @staticmethod
    def preview(photo, long_side=1024):
        """A cheap proxy for the live preview.  Geometry is percentage based, so
        the proxy render is proportionally identical to the full-size one."""
        if max(photo.size) <= long_side:
            return photo
        scale = long_side / float(max(photo.size))
        size = (max(1, round(photo.width * scale)), max(1, round(photo.height * scale)))
        return photo.resize(size, Image.BILINEAR, reducing_gap=2.0)
