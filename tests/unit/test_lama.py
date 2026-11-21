"""LaMa wrapper: tensor conventions, windowing, write-back of masked pixels only, Telea fallback."""

import numpy as np
import pytest
from app.config import get_settings
from app.render import inpaint as ip
from app.render import lama


def fake_runner(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Mimics the ONNX graph: image in [0,1] NCHW, binary mask NCHW; output in [0,255], composited,
    hole filled with the mean of the known pixels."""
    assert image.shape == (1, 3, lama.TILE, lama.TILE) and image.dtype == np.float32
    assert mask.shape == (1, 1, lama.TILE, lama.TILE) and mask.dtype == np.float32
    assert 0.0 <= image.min() and image.max() <= 1.0
    assert set(np.unique(mask)).issubset({0.0, 1.0})
    out = image[0] * 255.0
    hole = mask[0, 0] > 0.5
    fill = out[:, ~hole].mean(axis=1) if (~hole).any() else np.zeros(3)
    out = out.copy()
    out[:, hole] = fill[:, None]
    return out[None]


def _scene() -> tuple[np.ndarray, np.ndarray]:
    crop = np.full((120, 200, 3), 240, dtype=np.uint8)
    crop[:, :, 2] = 200  # tinted background so a wrong channel order would show
    crop[50:70, 80:130] = 0  # "text"
    mask = np.zeros((120, 200), dtype=bool)
    mask[48:72, 78:132] = True
    return crop, mask


def test_window_is_square_with_context_and_clamped():
    _crop, mask = _scene()
    x0, y0, x1, y1 = lama.window_for(mask, (120, 200))
    # the minimum window is larger than the crop, so the whole crop is used (padded to a square later)
    assert (x0, y0, x1, y1) == (0, 0, 200, 120)
    big = np.zeros((900, 900), dtype=bool)
    big[400:420, 400:460] = True
    x0, y0, x1, y1 = lama.window_for(big, (900, 900))
    assert x1 - x0 == y1 - y0 == lama.MIN_WINDOW and x0 <= 400 and x1 >= 460 and y0 <= 400 and y1 >= 420


def test_inpaint_with_writes_back_only_masked_pixels():
    crop, mask = _scene()
    out = lama.inpaint_with(fake_runner, crop, mask)
    assert out.shape == crop.shape and out.dtype == np.uint8
    assert np.array_equal(out[~mask], crop[~mask])
    filled = out[mask]
    assert filled[:, 0].mean() > 200 and filled[:, 2].mean() < filled[:, 0].mean()  # tint kept


def test_lama_class_uses_injected_runner_and_reports_backend():
    crop, mask = _scene()
    s = get_settings()
    old = s.render_inpaint_model
    s.render_inpaint_model = "lama"
    try:
        lama._instance = lama.Lama(runner=fake_runner)
        out = ip.inpaint(crop, mask)
        assert ip.LAST_MODEL[0] == "lama"
        assert out[mask].mean() > 150
    finally:
        lama._instance = None
        s.render_inpaint_model = old


def test_falls_back_to_telea_when_model_missing(monkeypatch: pytest.MonkeyPatch, tmp_path):
    crop, mask = _scene()
    s = get_settings()
    monkeypatch.setattr(s, "models_dir", tmp_path)
    monkeypatch.setattr(s, "lama_auto_download", False)
    monkeypatch.setattr(s, "render_inpaint_model", "auto")
    lama._instance = None
    try:
        out = ip.inpaint(crop, mask)
        assert ip.LAST_MODEL[0] in ("telea", "diffusion")
        assert out[mask].mean() > 150
        assert not lama.get_lama().available()
    finally:
        lama._instance = None


def test_download_rejects_wrong_checksum(monkeypatch: pytest.MonkeyPatch, tmp_path):
    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        def iter_bytes(self, _n):
            yield b"not a model"

    import httpx

    monkeypatch.setattr(httpx, "stream", lambda *a, **k: Resp())
    with pytest.raises(lama.LamaUnavailable):
        lama.download(tmp_path / "lama" / "lama_fp32.onnx")
    assert not (tmp_path / "lama" / "lama_fp32.onnx").exists()


def test_tile_plan_single_window_for_small_holes_and_small_images():
    _crop, mask = _scene()
    assert lama.tile_plan(mask, mask.shape) is None  # image smaller than a tile
    big = np.zeros((1200, 1200), dtype=bool)
    big[500:540, 500:600] = True
    assert lama.tile_plan(big, big.shape) is None  # fits one native 512 window


def test_tile_plan_covers_a_wide_hole_at_native_resolution():
    mask = np.zeros((900, 1400), dtype=bool)
    mask[300:500, 200:1100] = True
    plan = lama.tile_plan(mask, mask.shape)
    assert plan is not None and 0 < len(plan) <= lama.MAX_TILES
    covered = np.zeros(mask.shape, dtype=bool)
    for x, y in plan:
        assert 0 <= x <= mask.shape[1] - lama.TILE and 0 <= y <= mask.shape[0] - lama.TILE
        covered[y : y + lama.TILE, x : x + lama.TILE] = True
    assert covered[mask].all()
    # a hole so large that the tile budget is exceeded falls back to the single (downscaled) window
    huge = np.zeros((4000, 4000), dtype=bool)
    huge[100:3900, 100:3900] = True
    assert lama.tile_plan(huge, huge.shape) is None


def test_tiled_inpaint_blends_and_writes_back_only_masked_pixels():
    h, w = 900, 1400
    crop = np.zeros((h, w, 3), dtype=np.uint8)
    crop[:, :, 0] = (np.arange(w, dtype=np.int32)[None, :] * 255 // w).astype(np.uint8)
    crop[:, :, 1] = 180
    crop[:, :, 2] = (np.arange(h, dtype=np.int32)[:, None] * 255 // h).astype(np.uint8)
    mask = np.zeros((h, w), dtype=bool)
    mask[300:500, 200:1100] = True
    crop[mask] = 0
    calls = []

    def counting_runner(image, mask_t):
        calls.append(1)
        return fake_runner(image, mask_t)

    out = lama.inpaint_with(counting_runner, crop, mask)
    assert 1 < len(calls) <= lama.MAX_TILES
    assert np.array_equal(out[~mask], crop[~mask])
    filled = out[mask]
    assert filled[:, 1].min() > 150  # every hole pixel was filled (no tile gap)
    # the fill follows the local gradient: left end of the hole is darker red than the right end
    assert out[400, 210, 0] < out[400, 1090, 0]
