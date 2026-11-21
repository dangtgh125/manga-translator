"""Group OCR lines into speech regions, estimate text direction, order for reading.

Geometry only proposes; the editor lets users reorder/merge/split (PLAN.md §3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median

from .base import OcrLine, Point, polygon_bbox


@dataclass
class Region:
    polygon: list[Point]
    direction: str
    lines: list[OcrLine] = field(default_factory=list)
    furigana: list[OcrLine] = field(default_factory=list)
    reading_order: int = 0

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return polygon_bbox(self.polygon)

    @property
    def text(self) -> str:
        return "".join(ln.text for ln in self.lines)

    @property
    def confidence(self) -> float:
        total = sum(max(1, len(ln.text)) for ln in self.lines)
        if total == 0:
            return 0.0
        return sum(ln.confidence * max(1, len(ln.text)) for ln in self.lines) / total


def line_direction(line: OcrLine) -> str:
    x0, y0, x1, y1 = polygon_bbox(line.polygon)
    w, h = max(1.0, x1 - x0), max(1.0, y1 - y0)
    if line.angle in (90, 270):
        return "vertical"
    ratio = h / w
    if ratio > 1.3:
        return "vertical"
    if ratio < 0.77:
        return "horizontal"
    return "vertical" if len(line.text) <= 1 else "unknown"


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    inter = min(a1, b1) - max(a0, b0)
    if inter <= 0:
        return 0.0
    return inter / max(1.0, min(a1 - a0, b1 - b0))


def _adjacent(a: OcrLine, b: OcrLine, da: str, db: str) -> bool:
    ax0, ay0, ax1, ay1 = polygon_bbox(a.polygon)
    bx0, by0, bx1, by1 = polygon_bbox(b.polygon)
    if "unknown" in (da, db):
        d = da if db == "unknown" else db
    elif da != db:
        return False
    else:
        d = da
    if d == "vertical":
        gap = max(0.0, max(ax0, bx0) - min(ax1, bx1))
        width = max(1.0, min(ax1 - ax0, bx1 - bx0))
        return gap < 0.9 * width and _overlap(ay0, ay1, by0, by1) > 0.25
    if d == "horizontal":
        gap = max(0.0, max(ay0, by0) - min(ay1, by1))
        height = max(1.0, min(ay1 - ay0, by1 - by0))
        return gap < 0.9 * height and _overlap(ax0, ax1, bx0, bx1) > 0.25
    # both unknown: merge only when boxes nearly touch
    gx = max(0.0, max(ax0, bx0) - min(ax1, bx1))
    gy = max(0.0, max(ay0, by0) - min(ay1, by1))
    return gx < 8 and gy < 8


def group_lines(lines: list[OcrLine]) -> list[Region]:
    n = len(lines)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    dirs = [line_direction(ln) for ln in lines]
    for i in range(n):
        for j in range(i + 1, n):
            if _adjacent(lines[i], lines[j], dirs[i], dirs[j]):
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    regions: list[Region] = []
    for idxs in groups.values():
        members = [lines[i] for i in idxs]
        votes = [dirs[i] for i in idxs if dirs[i] != "unknown"]
        if votes:
            direction = max(("vertical", "horizontal"), key=votes.count)
        else:
            direction = "unknown"
        members, furi = _split_furigana(members, direction)
        if direction == "vertical":
            members.sort(key=lambda ln: -polygon_bbox(ln.polygon)[2])  # right -> left
        else:
            members.sort(key=lambda ln: polygon_bbox(ln.polygon)[1])  # top -> bottom
        all_pts = [p for ln in members + furi for p in ln.polygon]
        x0, y0, x1, y1 = polygon_bbox(all_pts)
        regions.append(
            Region(
                polygon=[(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
                direction=direction,
                lines=members,
                furigana=furi,
            )
        )
    return regions


def _split_furigana(members: list[OcrLine], direction: str) -> tuple[list[OcrLine], list[OcrLine]]:
    if len(members) < 2:
        return members, []
    if direction == "vertical":
        sizes = [polygon_bbox(ln.polygon)[2] - polygon_bbox(ln.polygon)[0] for ln in members]
    else:
        sizes = [polygon_bbox(ln.polygon)[3] - polygon_bbox(ln.polygon)[1] for ln in members]
    med = median(sizes)
    main: list[OcrLine] = []
    furi: list[OcrLine] = []
    for ln, sz in zip(members, sizes, strict=True):
        (furi if sz < 0.55 * med and len(ln.text) <= 12 else main).append(ln)
    if not main:
        return members, []
    return main, furi


def order_regions(regions: list[Region], reading_direction: str = "rtl") -> list[Region]:
    """Band by vertical overlap, then within band right->left (rtl) or left->right (ltr)."""
    if not regions:
        return regions
    items = sorted(regions, key=lambda r: r.bbox[1])
    bands: list[list[Region]] = []
    for r in items:
        placed = False
        for band in bands:
            by0 = min(b.bbox[1] for b in band)
            by1 = max(b.bbox[3] for b in band)
            if _overlap(by0, by1, r.bbox[1], r.bbox[3]) > 0.3:
                band.append(r)
                placed = True
                break
        if not placed:
            bands.append([r])
    bands.sort(key=lambda b: min(x.bbox[1] for x in b))
    ordered: list[Region] = []
    for band in bands:
        band.sort(key=lambda r: -r.bbox[2] if reading_direction == "rtl" else r.bbox[0])
        ordered.extend(band)
    for i, r in enumerate(ordered):
        r.reading_order = i
    return ordered
