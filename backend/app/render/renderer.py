"""Render translated text over a page: text-stroke inpainting (or flat mask) + fitted, wrapped text overlay.

The output is a new PNG; the original image is never modified. Overflow and complex-background
warnings are collected into a render report for the UI (PLAN.md §3 "Dựng ảnh").
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from ..config import get_settings
from . import inpaint as ip
from .fonts import is_cjk, load_font


@dataclass
class RenderReport:
    rendered: int = 0
    skipped: int = 0
    inpainted: int = 0
    inpainter: str = ""
    # Regions left untouched (lettering over art that could not be separated; SFX policy "keep").
    kept: int = 0
    warnings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rendered": self.rendered,
            "skipped": self.skipped,
            "inpainted": self.inpainted,
            "inpainter": self.inpainter,
            "kept": self.kept,
            "warnings": self.warnings,
        }


def _bbox(poly: list[list[float]]) -> tuple[int, int, int, int]:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))


def _box_from(box: dict[str, Any] | None, fallback: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    if box:
        return int(box["x"]), int(box["y"]), int(box["x"] + box["w"]), int(box["y"] + box["h"])
    return fallback


def _clamp(box: tuple[int, int, int, int], w: int, h: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    return max(0, x0), max(0, y0), min(w, x1), min(h, y1)


def estimate_background(
    img: np.ndarray, box: tuple[int, int, int, int], ring: int = 6
) -> tuple[tuple[int, int, int], bool]:
    """Median colour of a ring around the box; flat if ring pixels have low variance."""
    h, w = img.shape[:2]
    x0, y0, x1, y1 = box
    ox0, oy0, ox1, oy1 = max(0, x0 - ring), max(0, y0 - ring), min(w, x1 + ring), min(h, y1 + ring)
    outer = img[oy0:oy1, ox0:ox1]
    if outer.size == 0:
        return (255, 255, 255), False
    mask = np.ones(outer.shape[:2], dtype=bool)
    ix0, iy0 = x0 - ox0, y0 - oy0
    mask[iy0 : iy0 + (y1 - y0), ix0 : ix0 + (x1 - x0)] = False
    ring_px = outer[mask]
    if ring_px.size == 0:
        return (255, 255, 255), False
    med = np.median(ring_px.reshape(-1, 3), axis=0)
    std = float(np.mean(np.std(ring_px.reshape(-1, 3), axis=0)))
    return (int(med[0]), int(med[1]), int(med[2])), std < 18.0


def erase_strokes(
    src: np.ndarray,
    out: Image.Image,
    box: tuple[int, int, int, int],
    background: tuple[int, int, int],
    vertical: bool = False,
    polygon: list[list[float]] | None = None,
    flat: bool = True,
) -> tuple[bool, int | None, tuple[int, int, int, int] | None]:
    """Erase the text inside `box` of `src` into `out`. Returns (done, glyph_px, bubble): done is
    False when the box does not look like strokes on a background (e.g. a solid block), so the
    caller falls back to a flat fill; glyph_px is the typical size of the erased glyphs; bubble
    is the bounding box of the flat area around the text (speech-bubble interior) when one is found.

    Flat background: only the stroke pixels are inpainted (Telea or LaMa). Complex background
    (screentone, art) with LaMa available: the whole OCR polygon is the hole and LaMa rebuilds the
    texture, because a colour threshold cannot tell strokes from a dotted background."""
    x0, y0, x1, y1 = box
    crop = src[y0:y1, x0:x1]
    if crop.size == 0:
        return False, None, None
    raw, mask = ip.stroke_masks(crop, background)
    if not mask.any():
        return False, None, None
    # Bubble search on the source with the strokes painted in background colour: the outline and
    # any art are exactly as drawn, so the flood fill cannot escape through an inpainting artefact.
    fill_src = np.array(src, copy=True)
    fill_src[y0:y1, x0:x1][raw] = background
    found = ip.bubble_region(fill_src, box, background)
    if found is None and not flat:
        # Lettering straight over art/screentone: a threshold against one background colour cannot
        # separate it, so the strokes are found as a flat colour that exists only inside the OCR
        # polygon (see inpaint.art_text_mask). Nothing clean found: give up, the caller decides.
        return _erase_over_art(src, out, box, polygon, vertical)
    if ip.stroke_fraction(mask) > ip.MAX_STROKE_FRACTION:
        return False, None, None  # a solid block, not strokes on a background
    bubble = None
    if found is not None:
        bubble, filled = found
        # Erase only inside the bubble (plus its anti-aliased rim): screentone outside stays untouched.
        mask &= ip.dilate(filled[y0:y1, x0:x1], 1)
        if not mask.any():
            return False, None, None
    filled_crop = ip.inpaint(crop, mask)
    if flat or bubble is not None:
        # Kill the faint ghost some inpainters leave on a flat bubble, and the JPEG ringing that
        # haloes the original glyphs a few pixels beyond the hole: anything near the background
        # colour in and around the hole becomes exactly the background. A found bubble is flat by
        # construction even when the ring around the OCR box reached outside it.
        zone = ip.dilate(mask, SNAP_HALO_PX)
        if bubble is not None:
            zone &= ip.dilate(filled[y0:y1, x0:x1], 1)
        ip.snap_to_background(filled_crop, zone, background)
    out.paste(Image.fromarray(filled_crop), (x0, y0))
    return True, ip.glyph_size(raw, vertical), bubble


# How far beyond the erased strokes near-background pixels are snapped on a flat background
# (covers the JPEG ringing around the original glyphs).
SNAP_HALO_PX = 6

# Ring (fraction of the polygon's short side) added around the OCR polygon for the colour analysis
# and as inpainting context when lettering sits over art.
ART_CONTEXT = 0.75
ART_DILATE_FRACTION = 0.08
ART_MAX_DILATE_PX = 8


def _erase_over_art(
    src: np.ndarray,
    out: Image.Image,
    box: tuple[int, int, int, int],
    polygon: list[list[float]] | None,
    vertical: bool,
) -> tuple[bool, int | None, tuple[int, int, int, int] | None]:
    if not polygon:
        return False, None, None
    h, w = src.shape[:2]
    x0, y0, x1, y1 = box
    grow = max(12, int(min(x1 - x0, y1 - y0) * ART_CONTEXT))
    ext = _clamp((x0 - grow, y0 - grow, x1 + grow, y1 + grow), w, h)
    crop = src[ext[1] : ext[3], ext[0] : ext[2]]
    poly_mask = _polygon_mask(polygon, ext)
    strokes = ip.art_text_mask(crop, poly_mask)
    if strokes is None:
        return False, None, None
    glyph = ip.glyph_size(strokes, vertical)
    # Big lettering has soft (anti-aliased, JPEG-ringed) edges several pixels wide; leaving them
    # outside the hole makes the inpainter smear their mixed colour inward, so grow with the glyph.
    grow_px = int(min(ART_MAX_DILATE_PX, max(ip.DILATE_PX, round((glyph or 0) * ART_DILATE_FRACTION))))
    hole = ip.dilate(strokes, grow_px)
    out.paste(Image.fromarray(ip.inpaint(crop, hole)), (ext[0], ext[1]))
    return True, glyph, None


def _polygon_mask(polygon: list[list[float]], box: tuple[int, int, int, int]) -> np.ndarray:
    """Boolean mask of `polygon` (page coordinates) inside `box`, as a crop-sized array."""
    x0, y0, x1, y1 = box
    m = Image.new("L", (max(1, x1 - x0), max(1, y1 - y0)), 0)
    ImageDraw.Draw(m).polygon([(float(p[0]) - x0, float(p[1]) - y0) for p in polygon], fill=255)
    return np.asarray(m) > 0


def _luma(c: tuple[int, int, int]) -> float:
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def inscribed_box(box: tuple[int, int, int, int], shrink: float = 0.15) -> tuple[int, int, int, int]:
    """Rectangle inside an ellipse-ish bubble: shrink the bounding box by `shrink` on each side."""
    x0, y0, x1, y1 = box
    dx, dy = int((x1 - x0) * shrink), int((y1 - y0) * shrink)
    return x0 + dx, y0 + dy, x1 - dx, y1 - dy


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: Any, max_width: int) -> list[str]:
    lines: list[str] = []
    for para in text.replace("\r", "").split("\n"):
        words = para.split(" ")
        cur = ""
        for word in words:
            cand = word if not cur else f"{cur} {word}"
            if draw.textlength(cand, font=font) <= max_width or not cur:
                if draw.textlength(cand, font=font) > max_width and not cur:
                    # single word longer than the box: hard-break by characters
                    piece = ""
                    for ch in word:
                        if draw.textlength(piece + ch, font=font) <= max_width or not piece:
                            piece += ch
                        else:
                            lines.append(piece)
                            piece = ch
                    cur = piece
                else:
                    cur = cand
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


def wrap_vertical(text: str, max_chars: int) -> list[str]:
    """Split text into columns of at most `max_chars` characters (CJK: one glyph per cell)."""
    cols: list[str] = []
    for para in text.replace("\r", "").split("\n"):
        chars = [c for c in para if c != " "]
        if not chars:
            continue
        for i in range(0, len(chars), max(1, max_chars)):
            cols.append("".join(chars[i : i + max(1, max_chars)]))
    return cols or [""]


def fit_text_vertical(
    text: str, box_w: int, box_h: int, max_size: int, min_size: int, fixed_size: int | None, lang: str | None
) -> tuple[Any, list[str], int, bool]:
    """Vertical layout: columns read right-to-left, characters top-to-bottom."""
    sizes = [fixed_size] if fixed_size else list(range(max_size, min_size - 1, -1))
    best = None
    for size in sizes:
        font = load_font(size, lang=lang)
        cell_h, col_w = int(size * 1.08), int(size * 1.2)
        cols = wrap_vertical(text, max(1, box_h // cell_h))
        fits = len(cols) * col_w <= box_w and max(len(c) for c in cols) * cell_h <= box_h
        best = (font, cols, size, not fits)
        if fits:
            return best
    assert best is not None
    return best


def fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    box_w: int,
    box_h: int,
    max_size: int,
    min_size: int,
    fixed_size: int | None = None,
    lang: str | None = None,
) -> tuple[Any, list[str], int, bool]:
    """Return (font, lines, size, overflow). Binary search the largest size that fits."""
    sizes = [fixed_size] if fixed_size else list(range(max_size, min_size - 1, -1))
    best = None
    for size in sizes:
        font = load_font(size, lang=lang)
        lines = wrap_text(draw, text, font, box_w)
        line_h = int(size * 1.25)
        total_h = line_h * len(lines)
        widest = max((draw.textlength(ln, font=font) for ln in lines), default=0)
        fits = total_h <= box_h and widest <= box_w
        best = (font, lines, size, not fits)
        if fits:
            return best
    assert best is not None
    return best


SFX_POLICIES = ("keep", "white_box")


def render_page(
    image: Image.Image,
    regions: list[dict[str, Any]],
    target_lang: str | None = None,
    options: dict[str, Any] | None = None,
) -> tuple[Image.Image, RenderReport]:
    """`options` are the project settings; `sfx_policy` says what to do with lettering over art
    that cannot be separated: "keep" (default) leaves the region as drawn and skips the translated
    text, "white_box" paints a white box and writes the text over it (per-region mask mode "box"
    always does the latter)."""
    s = get_settings()
    sfx_policy = (options or {}).get("sfx_policy") or "keep"
    if sfx_policy not in SFX_POLICIES:
        sfx_policy = "keep"
    base = image.convert("RGB")
    arr = np.asarray(base)
    out = base.copy()
    draw = ImageDraw.Draw(out)
    report = RenderReport()
    w, h = out.size
    vertical_target = is_cjk(target_lang)
    for r in regions:
        text = (r.get("final_text") or r.get("translated_text") or "").strip()
        if not text:
            report.skipped += 1
            continue
        poly = json.loads(r["polygon_json"]) if isinstance(r.get("polygon_json"), str) else r["polygon"]
        pb = _bbox(poly)
        mask_mode = r.get("mask_mode") or "auto"
        mask_box = _clamp(_box_from(json.loads(r["mask_box_json"]) if r.get("mask_box_json") else None, pb), w, h)
        manual_text_box = json.loads(r["text_box_json"]) if r.get("text_box_json") else None
        text_box = _clamp(_box_from(manual_text_box, mask_box), w, h)
        margin = 3
        background: tuple[int, int, int] = (255, 255, 255)
        complex_bg = False
        glyph_px: int | None = None
        if mask_mode != "none":
            mb = _clamp(
                (
                    mask_box[0] - margin,
                    mask_box[1] - margin,
                    mask_box[2] + margin,
                    mask_box[3] + margin,
                ),
                w,
                h,
            )
            colour: tuple[int, int, int] | None
            if mask_mode == "auto":
                background, flat = estimate_background(arr, mb)
                done, glyph_px, bubble = (False, None, None)
                if s.render_inpaint:
                    done, glyph_px, bubble = erase_strokes(
                        arr, out, mb, background, r.get("direction") == "vertical", poly, flat
                    )
                if done:
                    report.inpainted += 1
                    report.inpainter = ip.LAST_MODEL[0]
                    colour = None
                    if not flat and bubble is None:
                        complex_bg = True
                        report.warnings.append(
                            {
                                "region_id": r["id"],
                                "type": "complex_background",
                                "action": "inpainted",
                                "message": "background is not flat; text strokes were inpainted, check the result",
                            }
                        )
                    if manual_text_box is None and bubble is not None:
                        text_box = _clamp(inscribed_box(bubble), w, h)
                elif not flat and sfx_policy == "keep" and s.render_inpaint:
                    report.kept += 1
                    report.warnings.append(
                        {
                            "region_id": r["id"],
                            "type": "complex_background",
                            "action": "kept",
                            "message": "lettering over art could not be separated; region left as is "
                            "(set the region background to 'box' or draw the mask manually)",
                        }
                    )
                    continue
                elif not flat:
                    colour = (255, 255, 255)
                    background = colour
                    report.warnings.append(
                        {
                            "region_id": r["id"],
                            "type": "complex_background",
                            "action": "white_box",
                            "message": "background is not flat; a white box was used, adjust the mask manually",
                        }
                    )
                else:
                    colour = background
            else:
                colour = (255, 255, 255)
                background = colour
            if colour is not None:
                draw.rectangle(mb, fill=colour)
        else:
            background, flat = estimate_background(arr, mask_box)
            complex_bg = not flat
        bw, bh = max(8, text_box[2] - text_box[0] - 4), max(8, text_box[3] - text_box[1] - 4)
        fixed = int(r["font_size"]) if r.get("font_size") else None
        max_size = s.render_max_font_size
        if glyph_px:
            scale = s.render_glyph_scale * (1.0 if vertical_target else s.render_glyph_scale_latin)
            max_size = max(s.render_min_font_size, min(max_size, int(round(glyph_px * scale))))
        vertical = vertical_target and (r.get("direction") == "vertical")
        if vertical:
            font, lines, size, overflow = fit_text_vertical(
                text, bw, bh, max_size, s.render_min_font_size, fixed, target_lang
            )
        else:
            font, lines, size, overflow = fit_text(
                draw, text, bw, bh, max_size, s.render_min_font_size, fixed, target_lang
            )
        if overflow:
            report.warnings.append(
                {
                    "region_id": r["id"],
                    "type": "overflow",
                    "message": f"text does not fit at {size}px; enlarge the text box or shorten",
                }
            )
        dark = _luma(background) < 110
        ink = (255, 255, 255) if dark else (0, 0, 0)
        halo_ink = (0, 0, 0) if dark else (255, 255, 255)
        halo = s.render_halo >= 2 or (s.render_halo == 1 and (complex_bg or mask_mode == "none"))
        if vertical:
            cell_h, col_w = int(size * 1.08), int(size * 1.2)
            total_w = col_w * len(lines)
            x_right = text_box[2] - 2 - max(0, (bw - total_w) // 2)
            for ci, col in enumerate(lines):
                x = x_right - (ci + 1) * col_w + (col_w - size) // 2
                y = text_box[1] + 2 + max(0, (bh - cell_h * len(col)) // 2)
                for ch in col:
                    _draw_glyph(draw, x, y, ch, font, ink, halo_ink if halo else None)
                    y += cell_h
        else:
            line_h = int(size * 1.25)
            total_h = line_h * len(lines)
            y = text_box[1] + 2 + max(0, (bh - total_h) // 2)
            for ln in lines:
                tw = draw.textlength(ln, font=font)
                x = text_box[0] + 2 + max(0, int((bw - tw) // 2))
                _draw_glyph(draw, x, y, ln, font, ink, halo_ink if halo else None)
                y += line_h
        report.rendered += 1
    return out, report


def _draw_glyph(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    text: str,
    font: Any,
    ink: tuple[int, int, int],
    halo: tuple[int, int, int] | None,
) -> None:
    if halo is not None:
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1)):
            draw.text((x + dx, y + dy), text, font=font, fill=halo)
    draw.text((x, y), text, font=font, fill=ink)
