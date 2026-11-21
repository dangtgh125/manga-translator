"""LaMa (large-mask inpainting) through onnxruntime for erasing text over art and screentones.

Model: big-lama exported to ONNX by Carve (Apache-2.0, like upstream LaMa), fixed 512×512 input.
  image  float32 [N,3,512,512] in [0,1] RGB      mask  float32 [N,1,512,512] {0,1}, 1 = hole
  output float32 [N,3,512,512] already in [0,255], composited (outside the mask == input)

The file lives in `MT_MODELS_DIR/lama/lama_fp32.onnx` and is checksum-pinned here as well as in
`models.lock.json`. It is fetched with `python -m app.render.lama download` (or automatically when
`MT_LAMA_AUTO_DOWNLOAD=1`). Everything here degrades to Telea inpainting on any failure.
"""

from __future__ import annotations

import hashlib
import importlib
import logging
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np

from ..config import get_settings

log = logging.getLogger(__name__)

MODEL_URL = "https://huggingface.co/Carve/LaMa-ONNX/resolve/main/lama_fp32.onnx"
MODEL_SHA256 = "1faef5301d78db7dda502fe59966957ec4b79dd64e16f03ed96913c7a4eb68d6"
MODEL_SIZE = 208_044_816
MODEL_REL = Path("lama") / "lama_fp32.onnx"
TILE = 512
# Context around the hole fed to the network, relative to the hole's bounding box.
CONTEXT = 2.5
# Prefer feeding native pixels: a window this big maps 1:1 onto the 512 input.
MIN_WINDOW = 512

Runner = Callable[[np.ndarray, np.ndarray], np.ndarray]


class LamaUnavailable(RuntimeError):
    pass


def model_path() -> Path:
    return get_settings().models_dir / MODEL_REL


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(dest: Path | None = None, url: str = MODEL_URL) -> Path:
    """Fetch the model to `dest` (default: the models dir) and verify its checksum."""
    import httpx

    dest = dest or model_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    log.info("downloading LaMa model (%d MB)", MODEL_SIZE >> 20)
    with httpx.stream("GET", url, follow_redirects=True, timeout=120.0) as resp, tmp.open("wb") as f:
        resp.raise_for_status()
        total = 0
        for chunk in resp.iter_bytes(1 << 20):
            total += len(chunk)
            if total > MODEL_SIZE + (1 << 20):
                raise LamaUnavailable("LaMa download larger than expected; aborting")
            f.write(chunk)
    digest = sha256_file(tmp)
    if digest != MODEL_SHA256:
        tmp.unlink(missing_ok=True)
        raise LamaUnavailable(f"LaMa model checksum mismatch ({digest[:12]}…)")
    tmp.replace(dest)
    return dest


def _load_session(path: Path, threads: int) -> Runner:
    if sha256_file(path) != MODEL_SHA256:
        raise LamaUnavailable("LaMa model checksum mismatch; delete the file to re-download")
    try:
        ort = importlib.import_module("onnxruntime")
    except ImportError as exc:  # pragma: no cover - image without onnxruntime
        raise LamaUnavailable("onnxruntime is not installed") from exc
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = max(1, threads)
    opts.inter_op_num_threads = 1
    session = ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])
    names = [i.name for i in session.get_inputs()]
    out_name = session.get_outputs()[0].name

    def run(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
        return session.run([out_name], {names[0]: image, names[1]: mask})[0]

    return run


class Lama:
    """Lazy, process-wide session; `runner` can be injected for tests."""

    def __init__(self, runner: Runner | None = None) -> None:
        self._runner = runner
        self._lock = threading.Lock()
        self._failed: str | None = None

    def available(self) -> bool:
        try:
            self._ensure()
            return True
        except LamaUnavailable:
            return False

    def _ensure(self) -> Runner:
        if self._runner is not None:
            return self._runner
        if self._failed:
            raise LamaUnavailable(self._failed)
        with self._lock:
            if self._runner is not None:
                return self._runner
            s = get_settings()
            path = model_path()
            try:
                if not path.exists():
                    if not s.lama_auto_download:
                        raise LamaUnavailable("LaMa model not downloaded (run: python -m app.render.lama download)")
                    download(path)
                self._runner = _load_session(path, s.ocr_cpu_threads)
            except LamaUnavailable as exc:
                self._failed = str(exc)
                raise
            except Exception as exc:  # noqa: BLE001 - any loader/network error disables LaMa for this process
                self._failed = f"LaMa unavailable: {exc.__class__.__name__}"
                raise LamaUnavailable(self._failed) from exc
            return self._runner

    def inpaint(self, crop: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """Fill `mask` (bool H×W) in `crop` (uint8 H×W×3). The hole and its context are resampled
        to the fixed 512×512 network input and only the masked pixels are written back."""
        run = self._ensure()
        return inpaint_with(run, crop, mask)


def window_for(mask: np.ndarray, shape: tuple[int, int]) -> tuple[int, int, int, int]:
    """Square window around the hole with context, clamped to the image; at most the image itself."""
    h, w = shape
    ys, xs = np.nonzero(mask)
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
    side = int(max(MIN_WINDOW, (max(y1 - y0, x1 - x0)) * CONTEXT))
    side = min(side, max(h, w))
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    wx0 = max(0, min(cx - side // 2, w - side))
    wy0 = max(0, min(cy - side // 2, h - side))
    return wx0, wy0, min(w, wx0 + side), min(h, wy0 + side)


# Native-resolution tiling for holes too big for one 512 window: tiles of TILE px with this
# overlap are blended with a raised-cosine weight so the seams vanish. Beyond MAX_TILES the hole is
# handled by the (downscaled) single window instead, bounding the cost of one region.
TILE_OVERLAP = 128
TILE_CONTEXT_PX = 96
MAX_TILES = 12


def tile_plan(mask: np.ndarray, shape: tuple[int, int]) -> list[tuple[int, int]] | None:
    """Top-left corners of the TILE×TILE tiles covering the hole with context at native resolution,
    or None when a single window (which may downscale) should be used instead."""
    h, w = shape
    if h < TILE or w < TILE:
        return None
    ys, xs = np.nonzero(mask)
    y0, y1 = max(0, int(ys.min()) - TILE_CONTEXT_PX), min(h, int(ys.max()) + 1 + TILE_CONTEXT_PX)
    x0, x1 = max(0, int(xs.min()) - TILE_CONTEXT_PX), min(w, int(xs.max()) + 1 + TILE_CONTEXT_PX)
    if y1 - y0 <= TILE and x1 - x0 <= TILE:
        return None  # fits one native window: window_for() handles it
    stride = TILE - TILE_OVERLAP

    def starts(a0: int, a1: int, limit: int) -> list[int]:
        out = [a0]
        while out[-1] + TILE < a1:
            out.append(out[-1] + stride)
        return sorted({min(v, limit - TILE) for v in out})

    plan = [(x, y) for y in starts(y0, y1, h) for x in starts(x0, x1, w)]
    # Skip tiles that hold no hole pixels: they would only cost time.
    plan = [(x, y) for x, y in plan if mask[y : y + TILE, x : x + TILE].any()]
    return plan if 0 < len(plan) <= MAX_TILES else None


def _tile_weight() -> np.ndarray:
    ramp = 0.5 - 0.5 * np.cos(np.linspace(0, 2 * np.pi, TILE, dtype=np.float32))
    return np.maximum(np.outer(ramp, ramp), 0.02)


def inpaint_with(run: Runner, crop: np.ndarray, mask: np.ndarray) -> np.ndarray:
    if not mask.any():
        return crop.copy()
    plan = tile_plan(mask, mask.shape)
    if plan is not None:
        return _inpaint_tiled(run, crop, mask, plan)
    return _inpaint_window(run, crop, mask)


def _run_tile(run: Runner, img: np.ndarray, hole: np.ndarray) -> np.ndarray:
    """One TILE×TILE pass; returns uint8 RGB."""
    image_t = img.astype(np.float32).transpose(2, 0, 1)[None] / 255.0
    mask_t = hole.astype(np.float32)[None, None]
    out = run(image_t, mask_t)[0].transpose(1, 2, 0)
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def _inpaint_tiled(run: Runner, crop: np.ndarray, mask: np.ndarray, plan: list[tuple[int, int]]) -> np.ndarray:
    h, w = mask.shape
    acc = np.zeros((h, w, 3), dtype=np.float32)
    wsum = np.zeros((h, w), dtype=np.float32)
    weight = _tile_weight()
    for x, y in plan:
        out = _run_tile(run, crop[y : y + TILE, x : x + TILE], mask[y : y + TILE, x : x + TILE])
        acc[y : y + TILE, x : x + TILE] += out.astype(np.float32) * weight[:, :, None]
        wsum[y : y + TILE, x : x + TILE] += weight
    result = crop.copy()
    covered = mask & (wsum > 0)
    result[covered] = np.clip(np.rint(acc[covered] / wsum[covered][:, None]), 0, 255).astype(np.uint8)
    return result


def _inpaint_window(run: Runner, crop: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """The hole and its context are resampled to one 512×512 input; only the masked pixels are
    written back."""
    from PIL import Image

    h, w = mask.shape
    x0, y0, x1, y1 = window_for(mask, (h, w))
    win = crop[y0:y1, x0:x1]
    win_mask = mask[y0:y1, x0:x1]
    wh, ww = win.shape[:2]
    # Pad to a square (edge replicate) before resampling so the aspect ratio is kept.
    side = max(wh, ww)
    sq = np.pad(win, ((0, side - wh), (0, side - ww), (0, 0)), mode="edge")
    sq_mask = np.pad(win_mask, ((0, side - wh), (0, side - ww)), mode="constant")
    if side == TILE:
        out8 = _run_tile(run, sq, sq_mask)
        back = out8
    else:
        img_in = np.asarray(Image.fromarray(sq).resize((TILE, TILE), Image.Resampling.BICUBIC))
        mask_in = (
            np.asarray(Image.fromarray(sq_mask.astype(np.uint8) * 255).resize((TILE, TILE), Image.Resampling.BILINEAR))
            > 64
        )
        out8 = _run_tile(run, img_in, mask_in)
        back = np.asarray(Image.fromarray(out8).resize((side, side), Image.Resampling.BICUBIC))
    back = back[:wh, :ww]
    result = crop.copy()
    region = result[y0:y1, x0:x1]
    region[win_mask] = back[win_mask]
    return result


_instance: Lama | None = None


def get_lama() -> Lama:
    global _instance
    if _instance is None:
        _instance = Lama()
    return _instance


def main(argv: list[str]) -> int:
    if argv[1:2] == ["download"]:
        path = download()
        print(f"ok {path} sha256={MODEL_SHA256[:12]}…")
        return 0
    print("usage: python -m app.render.lama download", file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main(sys.argv))
