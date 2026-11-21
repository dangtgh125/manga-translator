"""Erase text strokes instead of painting a box over the bubble.

The old renderer filled the whole mask box with the ring colour, which also wiped the bubble
outline, screentones and any art under the text. Here only the pixels that look like text
(colour far from the estimated background) are selected, dilated a little to swallow the
anti-aliased edges, and filled in from their surroundings with OpenCV's Telea inpainting.
Without OpenCV a small diffusion-based fill is used so the renderer never hard-fails.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any

import numpy as np

from ..config import get_settings
from . import lama

log = logging.getLogger(__name__)
# Name of the backend used by the most recent `inpaint` call (for the render report).
LAST_MODEL = ["telea"]
_warned_lama = [False]

cv2: Any
try:  # opencv-contrib ships with PaddleOCR; opencv-python-headless is used in the test image.
    cv2 = importlib.import_module("cv2")
except ImportError:  # pragma: no cover - exercised only in images without OpenCV
    cv2 = None

HAS_CV2 = cv2 is not None

# Pixels whose max channel distance from the background colour exceeds this are text strokes.
TEXT_THRESHOLD = 70
# Dilation in pixels around detected strokes (covers anti-aliasing and thin halos).
DILATE_PX = 2
# When more than this fraction of the crop is "text" the mask is not a set of strokes
# (e.g. a solid block) and the caller should fall back to a flat fill.
MAX_STROKE_FRACTION = 0.6
INPAINT_RADIUS = 3


# A connected blob that touches the crop border and spans at least this fraction of the crop's
# width or height is treated as art (bubble outline, panel border) rather than a glyph.
BORDER_ART_SPAN = 0.6


def threshold_mask(crop: np.ndarray, background: tuple[int, int, int]) -> np.ndarray:
    """Pixels of `crop` (H×W×3 uint8) whose colour is far from the background: strokes and art."""
    diff = np.abs(crop.astype(np.int16) - np.asarray(background, dtype=np.int16)).max(axis=2)
    return diff > TEXT_THRESHOLD


def text_mask(crop: np.ndarray, background: tuple[int, int, int], grow: int = DILATE_PX) -> np.ndarray:
    """Boolean mask of stroke pixels in `crop` given the background colour (art blobs removed)."""
    return dilate(drop_border_art(threshold_mask(crop, background)), grow)


def stroke_masks(crop: np.ndarray, background: tuple[int, int, int]) -> tuple[np.ndarray, np.ndarray]:
    """(raw strokes, erase mask): the erase mask is the dilated strokes minus the art blobs, so a
    glyph that touches the bubble outline never takes a bite out of the outline."""
    thr = threshold_mask(crop, background)
    raw = drop_border_art(thr)
    art = thr & ~raw
    return raw, dilate(raw, DILATE_PX) & ~art


GLYPH_MERGE_PX = 3


def glyph_size(mask: np.ndarray, vertical: bool = False) -> int | None:
    """Typical glyph size in px from the (undilated) stroke mask. Strokes are first merged so a
    glyph (or a whole line/column of glyphs) forms one blob; the blob height gives the glyph size
    for horizontal text, the blob width for vertical columns. None without OpenCV or when nothing
    glyph-like is found."""
    if not HAS_CV2 or not mask.any():
        return None
    h, w = mask.shape
    merged = dilate(mask, GLYPH_MERGE_PX)
    n, _labels, stats, _ = cv2.connectedComponentsWithStats(merged.astype(np.uint8), connectivity=8)
    sizes = []
    for i in range(1, n):
        bw, bh, area = int(stats[i][2]), int(stats[i][3]), int(stats[i][4])
        dim = bw if vertical else bh
        limit = 0.9 * (w if vertical else h)
        if area >= 12 and 6 <= dim < limit:
            sizes.append(dim - 2 * GLYPH_MERGE_PX)
    if not sizes:
        return None
    return max(4, int(np.median(sizes)))


def bubble_region(
    img: np.ndarray, box: tuple[int, int, int, int], background: tuple[int, int, int], tolerance: int = 40
) -> tuple[tuple[int, int, int, int], np.ndarray] | None:
    """Flat area (speech-bubble interior) around `box`, found by flood-filling `img` from the box
    centre: (bounding box, filled mask of the whole image). None when the fill runs off the page
    or is not bubble-sized."""
    if not HAS_CV2:
        return None
    h, w = img.shape[:2]
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    if not (0 <= cx < w and 0 <= cy < h):
        return None
    seed = img[cy, cx].astype(np.int16)
    if np.abs(seed - np.asarray(background, dtype=np.int16)).max() > tolerance:
        return None
    mask = np.zeros((h + 2, w + 2), dtype=np.uint8)
    # 4-connectivity so a thin diagonal outline still contains the fill
    flags = 4 | (255 << 8) | cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE
    cv2.floodFill(np.array(img, copy=True), mask, (cx, cy), 0, (tolerance,) * 3, (tolerance,) * 3, flags)
    filled = mask[1:-1, 1:-1] > 0
    ys, xs = np.nonzero(filled)
    if ys.size == 0:
        return None
    bx0, by0, bx1, by1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    if bx0 == 0 or by0 == 0 or bx1 >= w or by1 >= h:
        return None  # open background, not a bubble
    box_area = max(1, (x1 - x0) * (y1 - y0))
    if (bx1 - bx0) * (by1 - by0) > 12 * box_area:
        return None
    if bx1 - bx0 < x1 - x0 or by1 - by0 < y1 - y0:
        return None  # smaller than the text itself: the fill hit a stroke remnant
    return (bx0, by0, bx1, by1), filled


def bubble_box(
    img: np.ndarray, box: tuple[int, int, int, int], background: tuple[int, int, int], tolerance: int = 40
) -> tuple[int, int, int, int] | None:
    found = bubble_region(img, box, background, tolerance)
    return found[0] if found else None


# --- text over art (no bubble, textured background) ---------------------------------------
# Lettering in webtoons/3D comics is a flat colour (fill, sometimes plus an outline) that exists
# only inside the OCR polygon, while the art's colours continue outside it. Clusters of the pixel
# colours are therefore scored by (a) how tight the cluster is, (b) how much denser it is inside
# the polygon than in a ring around it and (c) whether its blobs are stroke-shaped.
ART_CLUSTERS = 8
# Mean per-channel std of a cluster must stay below this for it to count as flat lettering.
ART_CLUSTER_STD = 28.0
# Density inside the polygon divided by density in the ring must reach this.
ART_INSIDE_RATIO = 3.0
# Fraction of the polygon a cluster may cover: less is noise, more is background.
ART_MIN_COVER, ART_MAX_COVER = 0.02, 0.55
# Stroke width (2 × median distance-to-edge) must stay below this fraction of the polygon's short side.
ART_MAX_STROKE = 0.35
ART_RING_FRACTION = 0.5


def art_text_mask(crop: np.ndarray, poly: np.ndarray) -> np.ndarray | None:
    """Stroke pixels of lettering drawn straight over art, or None when no clean lettering colour
    can be told apart. `crop` is uint8 H×W×3 including a ring around the polygon `poly` (bool H×W).
    The returned mask is the raw (undilated) union of the accepted colour clusters, inside `poly`
    plus the parts of those glyph blobs that the polygon cuts through."""
    if not HAS_CV2 or not poly.any():
        return None
    ys, xs = np.nonzero(poly)
    ph, pw = int(ys.max() - ys.min() + 1), int(xs.max() - xs.min() + 1)
    ring_px = max(4, int(min(ph, pw) * ART_RING_FRACTION))
    ring = dilate(poly, ring_px) & ~poly
    if ring.sum() < 50:
        return None
    sample = crop[poly | ring].reshape(-1, 3).astype(np.float32)
    k = min(ART_CLUSTERS, max(2, len(np.unique(sample, axis=0))))
    cv2.setRNGSeed(0)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    _compact, labels, centres = cv2.kmeans(sample, k, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
    # Assign every crop pixel to its nearest centre so the clusters form proper 2-D masks.
    flat = crop.reshape(-1, 3).astype(np.float32)
    dist = ((flat[:, None, :] - centres[None, :, :]) ** 2).sum(axis=2)
    assign = dist.argmin(axis=1).reshape(crop.shape[:2])
    inside_area, ring_area = float(poly.sum()), float(ring.sum())
    accepted = np.zeros(crop.shape[:2], dtype=bool)
    for c in range(k):
        m = assign == c
        m_in = m & poly
        cover = m_in.sum() / inside_area
        if not ART_MIN_COVER <= cover <= ART_MAX_COVER:
            continue
        ratio = cover / max(1e-6, m[ring].sum() / ring_area)
        if ratio < ART_INSIDE_RATIO:
            continue
        if float(np.mean(np.std(crop[m_in].reshape(-1, 3).astype(np.float32), axis=0))) > ART_CLUSTER_STD:
            continue
        if stroke_width(m_in) > ART_MAX_STROKE * min(ph, pw):
            continue
        if _is_patch(m_in):
            continue
        accepted |= m_in
    if not accepted.any():
        return None
    # Lettering is made of several blobs; a single big blob is a patch of background.
    n, _l, stats, _c = cv2.connectedComponentsWithStats(accepted.astype(np.uint8), connectivity=8)
    if n <= 2 and stats[1:, 4].max() > 0.5 * accepted.sum():
        return None
    # Fill first (completed past the polygon edge), then the outline/shadow layers hugging it.
    accepted, reach = _complete_cut_glyphs(poly, assign, accepted)
    return _absorb_outline_layers(crop, reach, ring, assign, k, accepted)


# OCR polygons are tight and often cut through the glyphs; a glyph blob of an accepted colour that
# continues past the polygon edge is taken whole, as long as most of it is inside the polygon.
GLYPH_GROW_FRACTION = 0.6


def _complete_cut_glyphs(poly: np.ndarray, assign: np.ndarray, accepted: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(completed mask, reach): reach is the polygon grown by a fraction of the glyph size, the
    area in which lettering may extend past the OCR polygon."""
    glyph = glyph_size(accepted) or 20
    reach_px = max(8, int(glyph * GLYPH_GROW_FRACTION))
    reach = dilate(poly, reach_px)
    colours = {int(c) for c in np.unique(assign[accepted])}
    if not colours:
        return accepted, reach
    full = np.isin(assign, list(colours)) & reach
    n, labels, _stats, _c = cv2.connectedComponentsWithStats(full.astype(np.uint8), connectivity=8)
    out = accepted.copy()
    rim = reach & ~dilate(poly, reach_px - 2)
    for i in range(1, n):
        comp = labels == i
        inside = (comp & accepted).sum()
        if inside == 0:
            continue
        outside = comp & ~poly
        if not outside.any() or outside.sum() > inside:
            continue
        # The continuation of a cut glyph ends on its own within reach; a streak of the same colour
        # that runs on to the rim of the reach (a highlight, a speed line, a stripe) is art.
        if not (comp & rim).any():
            out |= comp
    return out, reach


# SFX lettering is often layered: a fill plus an outline and/or a drop shadow in other colours.
# Those layers are rejected above when their colour also occurs in the art (a white outline on a
# page with white highlights), so they are picked up afterwards as clusters that concentrate in a
# thin band around the accepted strokes far more than in the ring outside the polygon.
OUTLINE_BAND_FRACTION = 0.25
OUTLINE_BAND_RATIO = 2.5
OUTLINE_MIN_PX, OUTLINE_MAX_PX = 3, 16


def _absorb_outline_layers(
    crop: np.ndarray, reach: np.ndarray, ring: np.ndarray, assign: np.ndarray, k: int, accepted: np.ndarray
) -> np.ndarray:
    glyph = glyph_size(accepted) or 20
    band_px = int(min(OUTLINE_MAX_PX, max(OUTLINE_MIN_PX, glyph * 0.3)))
    ring_area = float(ring.sum())
    for _pass in range(2):  # outline first, then a shadow beyond the outline
        dist = cv2.distanceTransform((~accepted).astype(np.uint8), cv2.DIST_L2, 3)
        band = (dist <= band_px) & ~accepted & reach
        band_area = float(band.sum())
        if band_area < 20:
            break
        added = False
        for c in range(k):
            m = assign == c
            if (m & accepted).sum() > 0.5 * m.sum():
                continue  # already part of the lettering
            frac_band = (m & band).sum() / band_area
            frac_ring = m[ring].sum() / ring_area
            if frac_band < OUTLINE_BAND_FRACTION or frac_band < OUTLINE_BAND_RATIO * frac_ring:
                continue
            px = crop[m & band].reshape(-1, 3).astype(np.float32)
            if float(np.mean(np.std(px, axis=0))) > ART_CLUSTER_STD:
                continue
            width = _layer_width(m & reach, dist, band_px)
            if width == 0:
                continue
            accepted = accepted | (m & reach & (dist <= width + 1))
            added = True
        if not added:
            break
    return accepted


# A layer (outline, shadow) covers nearly every pixel of the distance rings it spans around the
# strokes; art of the same colour that merely passes by (a white stripe) covers only a sliver.
LAYER_RING_COVERAGE = 0.35


def _layer_width(m: np.ndarray, dist: np.ndarray, max_px: int) -> int:
    """Width in px of the layer `m` hugging the strokes: the last distance ring still mostly covered."""
    width = 0
    for d in range(1, max_px + 1):
        ring_d = (dist > d - 1) & (dist <= d)
        n = ring_d.sum()
        if n == 0 or (m & ring_d).sum() / n < LAYER_RING_COVERAGE:
            break
        width = d
    return width


# A cluster whose largest blob holds this share of its pixels and fills its bounding box this
# densely is a patch (a glow, a flat area of art), not lettering, which is sparse even when joined.
PATCH_BLOB_SHARE = 0.5
PATCH_BLOB_FILL = 0.45


def _is_patch(mask: np.ndarray) -> bool:
    n, _l, stats, _c = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    if n <= 1:
        return False
    i = 1 + int(np.argmax(stats[1:, 4]))
    _x, _y, bw, bh, area = (int(v) for v in stats[i])
    return area >= PATCH_BLOB_SHARE * mask.sum() and area >= PATCH_BLOB_FILL * bw * bh


def stroke_width(mask: np.ndarray) -> float:
    """Typical stroke width of `mask` (2 × median distance from a mask pixel to the nearest edge)."""
    if not mask.any():
        return 0.0
    if HAS_CV2:
        dt = cv2.distanceTransform(np.pad(mask, 1).astype(np.uint8), cv2.DIST_L2, 3)[1:-1, 1:-1]
        return float(np.median(dt[mask])) * 2.0
    return 1.0


def drop_border_art(mask: np.ndarray) -> np.ndarray:
    """Remove long blobs that cross the crop border: a bubble outline or panel line that the mask
    box overlaps must survive the erase, while glyphs (small blobs) are kept for inpainting."""
    if not HAS_CV2 or not mask.any():
        return mask
    h, w = mask.shape
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    keep = mask.copy()
    for i in range(1, n):
        x, y, bw, bh, _area = stats[i]
        touches = x == 0 or y == 0 or x + bw >= w or y + bh >= h
        if touches and (bw >= BORDER_ART_SPAN * w or bh >= BORDER_ART_SPAN * h):
            keep[labels == i] = False
    return keep


def dilate(mask: np.ndarray, px: int) -> np.ndarray:
    if px <= 0 or not mask.any():
        return mask
    if HAS_CV2:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * px + 1, 2 * px + 1))
        return cv2.dilate(mask.astype(np.uint8), kernel) > 0
    out = mask.copy()
    for _ in range(px):
        grown = out.copy()
        grown[1:, :] |= out[:-1, :]
        grown[:-1, :] |= out[1:, :]
        grown[:, 1:] |= out[:, :-1]
        grown[:, :-1] |= out[:, 1:]
        out = grown
    return out


SNAP_TOLERANCE = 40


def snap_to_background(crop: np.ndarray, mask: np.ndarray, background: tuple[int, int, int]) -> None:
    """In place: pixels of `mask` within SNAP_TOLERANCE of `background` are set to it exactly."""
    diff = np.abs(crop.astype(np.int16) - np.asarray(background, dtype=np.int16)).max(axis=2)
    crop[mask & (diff <= SNAP_TOLERANCE)] = background


def stroke_fraction(mask: np.ndarray) -> float:
    return float(mask.mean()) if mask.size else 0.0


def inpaint(crop: np.ndarray, mask: np.ndarray, model: str | None = None) -> np.ndarray:
    """Fill `mask` pixels of `crop` from their surroundings; returns a new RGB uint8 array.
    `model` is "lama", "telea" or "auto" (settings default); LaMa falls back to Telea on failure."""
    if not mask.any():
        return crop.copy()
    model = model or get_settings().render_inpaint_model
    if model in ("lama", "auto"):
        try:
            result = lama.get_lama().inpaint(crop, mask)
            LAST_MODEL[0] = "lama"
            return result
        except lama.LamaUnavailable as exc:
            if model == "lama" or not _warned_lama[0]:
                _warned_lama[0] = True
                log.warning("LaMa inpainting unavailable, using Telea: %s", exc)
        except Exception as exc:  # noqa: BLE001 - never fail a render because of the model
            log.warning("LaMa inpainting failed, using Telea: %s", exc.__class__.__name__)
    LAST_MODEL[0] = "telea" if HAS_CV2 else "diffusion"
    if HAS_CV2:
        bgr = cv2.cvtColor(np.ascontiguousarray(crop), cv2.COLOR_RGB2BGR)
        filled = cv2.inpaint(bgr, mask.astype(np.uint8) * 255, INPAINT_RADIUS, cv2.INPAINT_TELEA)
        return cv2.cvtColor(filled, cv2.COLOR_BGR2RGB)
    return _inpaint_diffusion(crop, mask)


def _inpaint_diffusion(crop: np.ndarray, mask: np.ndarray, iterations: int = 60) -> np.ndarray:
    """Pure-numpy fallback: repeatedly replace masked pixels by the mean of their 4 neighbours."""
    img = crop.astype(np.float32)
    known = ~mask
    if known.any():
        img[mask] = img[known].mean(axis=0)
    padded = np.pad(img, ((1, 1), (1, 1), (0, 0)), mode="edge")
    for _ in range(iterations):
        padded[1:-1, 1:-1][mask] = (
            padded[:-2, 1:-1][mask] + padded[2:, 1:-1][mask] + padded[1:-1, :-2][mask] + padded[1:-1, 2:][mask]
        ) / 4.0
        padded[0, 1:-1] = padded[1, 1:-1]
        padded[-1, 1:-1] = padded[-2, 1:-1]
        padded[:, 0] = padded[:, 1]
        padded[:, -1] = padded[:, -2]
    return np.clip(padded[1:-1, 1:-1], 0, 255).astype(np.uint8)
