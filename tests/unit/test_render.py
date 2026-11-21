import json

from app.render.renderer import render_page
from PIL import Image


def _region(rid, poly, text, **kw):
    r = {
        "id": rid,
        "polygon_json": json.dumps(poly),
        "final_text": text,
        "translated_text": "",
        "mask_mode": "auto",
        "mask_box_json": None,
        "text_box_json": None,
        "font_size": None,
    }
    r.update(kw)
    return r


def test_render_flat_bubble_and_overflow_warning():
    im = Image.new("RGB", (400, 300), (255, 255, 255))
    # a dark block that the region covers: ring is white -> flat background
    for x in range(100, 200):
        for y in range(100, 180):
            im.putpixel((x, y), (0, 0, 0))
    regions = [
        _region("a", [[100, 100], [200, 100], [200, 180], [100, 180]], "Xin chào"),
        _region(
            "b",
            [[10, 10], [40, 10], [40, 30], [10, 30]],
            "Một câu rất dài không thể nào vừa trong hộp bé tí này " * 3,
        ),
        _region("c", [[300, 250], [380, 250], [380, 290], [300, 290]], ""),
    ]
    out, report = render_page(im, regions)
    assert out.size == im.size and im.getpixel((150, 140)) == (0, 0, 0)  # original untouched
    assert report.rendered == 2 and report.skipped == 1
    assert any(w["type"] == "overflow" and w["region_id"] == "b" for w in report.warnings)
    # the mask painted over the black block with the (white) ring colour
    assert out.getpixel((102, 102)) == (255, 255, 255)


def test_complex_background_warning_and_manual_boxes():
    im = Image.new("RGB", (200, 200))
    px = im.load()
    for x in range(200):
        for y in range(200):
            px[x, y] = ((x * 7) % 256, (y * 13) % 256, (x * y) % 256)
    regions = [
        _region("a", [[50, 50], [150, 50], [150, 100], [50, 100]], "Hi", mask_mode="auto"),
        _region(
            "b",
            [[20, 120], [60, 120], [60, 140], [20, 140]],
            "Hộp",
            mask_mode="box",
            mask_box_json=json.dumps({"x": 10, "y": 110, "w": 100, "h": 60}),
            text_box_json=json.dumps({"x": 12, "y": 112, "w": 96, "h": 56}),
            font_size=12,
        ),
    ]
    out, report = render_page(im, regions)
    assert any(w["type"] == "complex_background" for w in report.warnings)
    assert out.getpixel((15, 115)) == (255, 255, 255)
