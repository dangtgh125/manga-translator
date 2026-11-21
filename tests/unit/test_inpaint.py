"""Text-stroke inpainting: erase glyphs, keep the bubble outline, fall back to a box when needed."""

import json

import numpy as np
import pytest
from app.config import get_settings
from app.render import inpaint as ip
from app.render.renderer import render_page
from PIL import Image, ImageDraw, ImageFont


def _bubble_page() -> tuple[Image.Image, list[list[int]]]:
    im = Image.new("RGB", (300, 200), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.ellipse((20, 20, 280, 180), fill=(255, 255, 255), outline=(0, 0, 0), width=4)
    font = ImageFont.load_default(size=22)
    d.text((80, 70), "HELLO", font=font, fill=(0, 0, 0))
    d.text((80, 100), "WORLD", font=font, fill=(0, 0, 0))
    # polygon overlaps the left outline on purpose, like a loose OCR box
    return im, [[18, 60], [220, 60], [220, 140], [18, 140]]


def _region(poly, **kw):
    r = {
        "id": "r",
        "polygon_json": json.dumps(poly),
        "final_text": "xin chào",
        "translated_text": "",
        "mask_mode": "auto",
        "mask_box_json": None,
        "text_box_json": None,
        "font_size": 12,
    }
    r.update(kw)
    return r


def test_strokes_erased_but_outline_kept():
    im, poly = _bubble_page()
    arr = np.asarray(im)
    out, report = render_page(im, [_region(poly, final_text="")])  # empty text: region skipped, nothing drawn
    assert report.skipped == 1
    # draw the translation below the erased glyphs so the glyph zone can be checked on its own;
    # the ring around the box crosses the outline, so a complex-background note is expected
    out, report = render_page(
        im, [_region(poly, font_size=10, text_box_json=json.dumps({"x": 150, "y": 150, "w": 120, "h": 28}))]
    )
    assert report.inpainted == 1 and all(w["type"] != "overflow" for w in report.warnings)
    o = np.asarray(out)
    glyph_zone = o[62:138, 60:220]
    assert glyph_zone.min() > 200, "glyph pixels should be filled with the white bubble interior"
    # the bubble outline (x≈20..24) inside the mask box survives
    assert (arr[60:140, 18:26].min(axis=2) < 50).sum() > 0
    assert (o[60:140, 18:26].min(axis=2) < 50).sum() >= 0.8 * (arr[60:140, 18:26].min(axis=2) < 50).sum()


def test_solid_block_falls_back_to_flat_fill():
    im = Image.new("RGB", (120, 80), (255, 255, 255))
    ImageDraw.Draw(im).rectangle((20, 20, 100, 60), fill=(0, 0, 0))
    out, report = render_page(im, [_region([[20, 20], [100, 20], [100, 60], [20, 60]])])
    assert report.inpainted == 0 and report.rendered == 1
    assert out.getpixel((22, 22)) == (255, 255, 255)


def test_inpaint_disabled_restores_box_behaviour():
    s = get_settings()
    im, poly = _bubble_page()
    s.render_inpaint = False
    try:
        out, report = render_page(im, [_region(poly)])
    finally:
        s.render_inpaint = True
    assert report.inpainted == 0
    assert out.getpixel((21, 100)) == (255, 255, 255)  # outline wiped by the box


def test_drop_border_art_keeps_glyphs():
    mask = np.zeros((40, 100), dtype=bool)
    mask[:, 0:3] = True  # a line spanning the full height on the left border = outline
    mask[10:20, 40:50] = True  # a glyph-sized blob
    mask[0:5, 60:70] = True  # small blob touching the top border = part of a glyph
    kept = ip.drop_border_art(mask)
    if ip.HAS_CV2:
        assert not kept[:, 0:3].any()
    assert kept[10:20, 40:50].all() and kept[0:5, 60:70].all()


def test_numpy_fallback_inpaints_without_cv2(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ip, "HAS_CV2", False)
    crop = np.full((30, 30, 3), 240, dtype=np.uint8)
    crop[10:20, 10:20] = 0
    mask = ip.text_mask(crop, (240, 240, 240))
    assert mask[12:18, 12:18].all() and not mask[0, 0]
    filled = ip.inpaint(crop, mask)
    assert filled[10:20, 10:20].min() > 200


def test_snap_to_background_removes_ghost():
    crop = np.full((10, 10, 3), 255, dtype=np.uint8)
    crop[4:6, 4:6] = 240  # faint ghost
    crop[0, 0] = 0  # real ink outside the mask stays
    mask = np.zeros((10, 10), dtype=bool)
    mask[3:7, 3:7] = True
    ip.snap_to_background(crop, mask, (255, 255, 255))
    assert crop[4:6, 4:6].min() == 255 and crop[0, 0, 0] == 0
