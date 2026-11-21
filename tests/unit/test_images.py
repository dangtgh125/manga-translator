import io
import zlib

import pytest
from app.security.images import ImageValidationError, validate_image_bytes
from PIL import Image

from tests.conftest import make_jpeg, make_png, make_webp


def test_accepts_png_jpeg_webp():
    for data, fmt in ((make_png(), "png"), (make_jpeg(), "jpg"), (make_webp(), "webp")):
        v = validate_image_bytes(data)
        assert v.fmt == fmt and v.width > 0 and v.normalized_png.startswith(b"\x89PNG")


def test_rejects_fake_mime_and_other_formats():
    with pytest.raises(ImageValidationError):
        validate_image_bytes(b"GIF89a" + b"\x00" * 100)
    # PNG header but JPEG body
    with pytest.raises(ImageValidationError):
        validate_image_bytes(b"\x89PNG\r\n\x1a\n" + make_jpeg()[8:])
    buf = io.BytesIO()
    Image.new("RGB", (50, 50)).save(buf, format="BMP")
    with pytest.raises(ImageValidationError):
        validate_image_bytes(buf.getvalue())
    with pytest.raises(ImageValidationError):
        validate_image_bytes(b"")


def test_rejects_truncated_image():
    data = make_jpeg(800, 800)
    with pytest.raises(ImageValidationError):
        validate_image_bytes(data[: len(data) // 2])


def test_rejects_decompression_bomb(monkeypatch):
    monkeypatch.setenv("MT_MAX_IMAGE_PIXELS", "1000000")
    from app.config import reset_settings_cache

    reset_settings_cache()
    # Hand-build a PNG claiming 20000x20000 (zlib-compressed zeros are tiny).
    w = h = 20000
    raw = zlib.compress(b"\x00" * (1 + w) * 2, 9)  # intentionally short; header is what matters

    def chunk(t: bytes, d: bytes) -> bytes:
        return len(d).to_bytes(4, "big") + t + d + zlib.crc32(t + d).to_bytes(4, "big")

    ihdr = w.to_bytes(4, "big") + h.to_bytes(4, "big") + bytes([8, 0, 0, 0, 0])
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")
    with pytest.raises(ImageValidationError):
        validate_image_bytes(png)


def test_strips_exif_and_applies_orientation():
    im = Image.new("RGB", (80, 40), (255, 0, 0))
    exif = im.getexif()
    exif[0x0112] = 6  # rotate 90
    exif[0x010E] = "secret description"
    buf = io.BytesIO()
    im.save(buf, format="JPEG", exif=exif.tobytes())
    v = validate_image_bytes(buf.getvalue())
    assert (v.width, v.height) == (40, 80)
    out = Image.open(io.BytesIO(v.normalized_png))
    assert not out.getexif() and b"secret description" not in v.normalized_png
