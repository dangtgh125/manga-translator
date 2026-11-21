"""Image intake validation and normalisation.

Rules (see PLAN.md §5.1): accept only PNG/JPEG/WebP detected from *content*, decode for real,
cap dimensions/pixels, reject decompression bombs, strip EXIF/metadata from working copies.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageFile, ImageOps, PngImagePlugin, UnidentifiedImageError

from ..config import get_settings, is_test_env

ImageFile.LOAD_TRUNCATED_IMAGES = False

ALLOWED_FORMATS = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}
ALLOWED_MIME = {"image/png", "image/jpeg", "image/webp"}


class ImageValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedImage:
    fmt: str  # png | jpg | webp
    width: int
    height: int
    normalized_png: bytes


def _sniff(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if data.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    return None


def validate_image_bytes(data: bytes) -> ValidatedImage:
    s = get_settings()
    if not data:
        raise ImageValidationError("empty file")
    if len(data) > s.max_upload_bytes:
        raise ImageValidationError("file too large")
    sniffed = _sniff(data)
    if sniffed is None:
        raise ImageValidationError("unsupported file type (only PNG, JPEG, WebP)")

    # Bound Pillow's bomb check to our own pixel budget.
    Image.MAX_IMAGE_PIXELS = s.max_image_pixels
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in ALLOWED_FORMATS or im.format != sniffed:
                raise ImageValidationError("file content does not match a supported image format")
            w, h = im.size
            if w < s.min_image_side or h < s.min_image_side:
                raise ImageValidationError("image too small")
            if w > s.max_image_side or h > s.max_image_side or w * h > s.max_image_pixels:
                raise ImageValidationError("image dimensions exceed limits")
            if getattr(im, "is_animated", False) and getattr(im, "n_frames", 1) > 1:
                im.seek(0)
            # Full decode (raises on corrupt/bomb data).
            im.load()
            mock_sidecar = im.info.get("mt_mock_ocr") if is_test_env() else None
            # Apply EXIF orientation, then drop all metadata by re-encoding.
            normalized: Image.Image = ImageOps.exif_transpose(im)
            if normalized.mode not in ("RGB", "L"):
                normalized = normalized.convert("RGB")
            w, h = normalized.size
            buf = io.BytesIO()
            pnginfo = None
            if mock_sidecar:
                # Test-only channel for the mock OCR engine (MT_TEST_MODE=1); never set in production.
                pnginfo = PngImagePlugin.PngInfo()
                pnginfo.add_text("mt_mock_ocr", str(mock_sidecar))
            normalized.save(buf, format="PNG", optimize=False, pnginfo=pnginfo)
            return ValidatedImage(fmt=ALLOWED_FORMATS[sniffed], width=w, height=h, normalized_png=buf.getvalue())
    except Image.DecompressionBombError as exc:
        raise ImageValidationError("image rejected: decompression bomb") from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        if isinstance(exc, ImageValidationError):
            raise
        raise ImageValidationError("image could not be decoded") from exc
