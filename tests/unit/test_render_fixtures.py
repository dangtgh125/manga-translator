"""Regression fixtures for the two erase paths (tests/fixtures/render, see make_fixtures.py):

* ja_bubble    vertical Japanese dialogue in a bubble over screentone: the manga path must keep
               the outline and the screentone pixel-exact and leave a clean, flat interior;
* art_letters  outlined white lettering over coloured art, art_solid plain dark lettering: the
               lettering path must remove the ink without touching anything outside the polygon;
* noise        lettering that cannot be separated is kept (SFX policy) or boxed, by project option.
"""

import json
from pathlib import Path

import numpy as np
import pytest
from app.render import inpaint as ip
from app.render import renderer
from PIL import Image

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "render"
META = json.loads((FIXTURES / "fixtures.json").read_text())


def _load(name: str) -> tuple[Image.Image, dict]:
    return Image.open(FIXTURES / f"{name}.png").convert("RGB"), META[name]


def _region(info: dict, text: str, **kw) -> dict:
    r = {
        "id": "r",
        "polygon_json": json.dumps(info["polygon"]),
        "final_text": text,
        "translated_text": "",
        "mask_mode": "auto",
        "mask_box_json": None,
        "text_box_json": None,
        "font_size": None,
        "direction": info["direction"],
    }
    r.update(kw)
    return r


def _erase(im: Image.Image, info: dict):
    """Run only the erase stage; returns (erased image, done, glyph_px, bubble, mask box)."""
    arr = np.asarray(im)
    pb = renderer._bbox(info["polygon"])
    mb = renderer._clamp((pb[0] - 3, pb[1] - 3, pb[2] + 3, pb[3] + 3), *im.size)
    bg, flat = renderer.estimate_background(arr, mb)
    out = im.copy()
    done, glyph, bubble = renderer.erase_strokes(
        arr, out, mb, bg, info["direction"] == "vertical", info["polygon"], flat
    )
    return out, done, glyph, bubble, mb


@pytest.mark.skipif(not ip.HAS_CV2, reason="needs OpenCV")
def test_ja_bubble_erase_keeps_outline_and_tone():
    im, info = _load("ja_bubble")
    out, done, glyph, bubble, _mb = _erase(im, info)
    assert done and bubble is not None
    bx0, by0, bx1, by1 = info["bubble"]
    assert all(abs(a - b) <= 5 for a, b in zip(bubble, info["bubble"], strict=True))
    lo, hi = info["glyph_px"]
    assert glyph is not None and lo <= glyph <= hi
    src, dst = np.asarray(im), np.asarray(out)
    # Nothing outside the bubble (screentone, tail, outline) may change.
    outside = np.ones(src.shape[:2], dtype=bool)
    outside[by0 + 2 : by1 - 2, bx0 + 2 : bx1 - 2] = False
    assert np.array_equal(src[outside], dst[outside])
    # The interior is clean white: no dark remnants, no grey ghost.
    ix0, iy0, ix1, iy1 = renderer.inscribed_box((bx0, by0, bx1, by1), 0.2)
    inner = dst[iy0:iy1, ix0:ix1]
    assert (inner.min(axis=2) < 128).mean() < 0.002
    assert (inner.min(axis=2) < 250).mean() < 0.01


@pytest.mark.skipif(not ip.HAS_CV2, reason="needs OpenCV")
def test_ja_bubble_render_is_the_plain_manga_path():
    im, info = _load("ja_bubble")
    out, report = renderer.render_page(im, [_region(info, "Đây là đâu? Chạy mau!")], "vi")
    assert report.rendered == 1 and report.inpainted == 1 and report.kept == 0
    assert not [w for w in report.warnings if w["type"] == "complex_background"]
    # the screentone far from the bubble is untouched
    assert np.array_equal(np.asarray(im)[320:, :100], np.asarray(out)[320:, :100])


@pytest.mark.skipif(not ip.HAS_CV2, reason="needs OpenCV")
@pytest.mark.parametrize("name", ["art_letters", "art_solid", "art_layered"])
def test_lettering_over_art_is_separated_and_erased(name: str):
    im, info = _load(name)
    out, done, glyph, bubble, mb = _erase(im, info)
    assert done and bubble is None and glyph and glyph >= 20
    src, dst = np.asarray(im).astype(int), np.asarray(out).astype(int)
    poly = renderer._polygon_mask(info["polygon"], (0, 0, *im.size))
    ink = np.asarray(info["ink"])
    before = (np.abs(src - ink).max(axis=2) < 30) & poly
    after = (np.abs(dst - ink).max(axis=2) < 30) & poly
    assert before.sum() > 500 and after.sum() < 0.03 * before.sum()
    # Art outside the context window is pixel-exact; inside it only the hole changed noticeably.
    grow = max(12, int(min(mb[2] - mb[0], mb[3] - mb[1]) * renderer.ART_CONTEXT)) + 1
    far = np.ones(poly.shape, dtype=bool)
    far[max(0, mb[1] - grow) : mb[3] + grow, max(0, mb[0] - grow) : mb[2] + grow] = False
    assert np.array_equal(src[far], dst[far])
    changed = np.abs(src - dst).max(axis=2) > 8
    assert changed[~ip.dilate(poly, 12)].sum() == 0


@pytest.mark.skipif(not ip.HAS_CV2, reason="needs OpenCV")
@pytest.mark.skipif(not ip.HAS_CV2, reason="needs OpenCV")
def test_layered_lettering_loses_outline_and_shadow_too():
    im, info = _load("art_layered")
    out, done, _glyph, _bubble, _mb = _erase(im, info)
    assert done
    src, dst = np.asarray(im).astype(int), np.asarray(out).astype(int)
    letters = np.asarray(Image.open(FIXTURES / "art_layered_mask.png").convert("L")) > 127
    for layer in info["layers"]:
        before = (np.abs(src - np.asarray(layer)).max(axis=2) < 30) & letters
        after = (np.abs(dst - np.asarray(layer)).max(axis=2) < 30) & letters
        assert before.sum() > 300 and after.sum() < 0.1 * before.sum(), (layer, int(before.sum()), int(after.sum()))
    # The white art stripes survive except where they run through the letters themselves: stray
    # changes beyond the lettering (+6 px) stay under 6 % of the polygon, none further than 12 px.
    changed = np.abs(src - dst).max(axis=2) > 8
    poly = renderer._polygon_mask(info["polygon"], (0, 0, *im.size))
    assert changed[~ip.dilate(letters, 6)].sum() < 0.06 * poly.sum(), int(changed[~ip.dilate(letters, 6)].sum())
    assert changed[~ip.dilate(letters, 12)].sum() < 0.005 * poly.sum()


def test_art_render_reports_inpainted_with_warning():
    im, info = _load("art_letters")
    _out, report = renderer.render_page(im, [_region(info, "RẦM!!")], "vi")
    assert report.inpainted == 1 and report.kept == 0
    assert [w["action"] for w in report.warnings if w["type"] == "complex_background"] == ["inpainted"]


def _noise_page() -> tuple[Image.Image, dict]:
    rng = np.random.default_rng(7)
    arr = rng.integers(0, 256, size=(200, 260, 3), dtype=np.uint8)
    return Image.fromarray(arr), {"polygon": [[60, 60], [200, 60], [200, 120], [60, 120]], "direction": "horizontal"}


def test_unseparable_lettering_is_kept_by_default():
    im, info = _noise_page()
    out, report = renderer.render_page(im, [_region(info, "Hi")], "vi")
    assert report.kept == 1 and report.rendered == 0 and report.inpainted == 0
    assert [w["action"] for w in report.warnings if w["type"] == "complex_background"] == ["kept"]
    assert np.array_equal(np.asarray(im), np.asarray(out))  # nothing drawn at all


def test_unseparable_lettering_white_box_policy():
    im, info = _noise_page()
    out, report = renderer.render_page(im, [_region(info, "Hi")], "vi", {"sfx_policy": "white_box"})
    assert report.kept == 0 and report.rendered == 1
    assert [w["action"] for w in report.warnings if w["type"] == "complex_background"] == ["white_box"]
    assert out.getpixel((62, 62)) == (255, 255, 255)
    # an unknown policy value behaves like the default
    _out, report2 = renderer.render_page(im, [_region(info, "Hi")], "vi", {"sfx_policy": "bogus"})
    assert report2.kept == 1


@pytest.mark.skipif(not ip.HAS_CV2, reason="needs OpenCV")
def test_art_text_mask_rejects_background_patch():
    # A flat orange patch with no lettering: the only clusters are background, so nothing is returned.
    crop = np.full((80, 120, 3), (230, 120, 60), dtype=np.uint8)
    crop[:, :60] = (40, 90, 200)
    poly = np.zeros((80, 120), dtype=bool)
    poly[20:60, 30:90] = True
    assert ip.art_text_mask(crop, poly) is None
