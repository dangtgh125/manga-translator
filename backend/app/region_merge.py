"""Merge fresh OCR output into the regions a page already has, so user edits survive an OCR re-run.

Pure functions only (no database access) so the matching policy can be unit tested:

* ``match_polygons`` pairs new OCR polygons with existing ones by bounding-box IoU (greedy, best
  pair first) and falls back to centroid containment for shrunken/grown boxes.
* ``plan_merge`` classifies regions into update / insert / delete / keep-but-flag.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

BBox = tuple[float, float, float, float]

IOU_THRESHOLD = 0.5
OCR_MISSING_FLAG = "ocr_missing"
USER_EDITED_DURING_JOB_FLAG = "user_edited_during_job"
# The provider answered under a project/page context (target language, glossary, page source
# language) that changed before the result was written: the translation is stored but stays stale.
CONTEXT_CHANGED_DURING_JOB_FLAG = "context_changed_during_job"
# final_text was written by hand for another target language; re-translation keeps the region stale.
FINAL_LANG_MISMATCH_FLAG = "final_text_lang_mismatch"
# Region columns that record a by-hand edit of an OCR-derived field; the merge keeps those values.
MANUAL_FIELD_FLAGS = ("polygon_edited", "direction_edited", "source_lang_edited")
# Flags that describe the region's state rather than one translation result; a later translation
# must not silently drop them.
STICKY_FLAGS = frozenset({OCR_MISSING_FLAG})


def polygon_bbox(poly: list[Any]) -> BBox:
    xs = [float(p[0]) for p in poly]
    ys = [float(p[1]) for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def _area(b: BBox) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def bbox_iou(a: BBox, b: BBox) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    union = _area(a) + _area(b) - inter
    return inter / union if union > 0 else 0.0


def _centroid(b: BBox) -> tuple[float, float]:
    return (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0


def _contains(b: BBox, pt: tuple[float, float]) -> bool:
    return b[0] <= pt[0] <= b[2] and b[1] <= pt[1] <= b[3]


def match_polygons(old: list[list[Any]], new: list[list[Any]], iou_threshold: float = IOU_THRESHOLD) -> dict[int, int]:
    """Return ``{new_index: old_index}`` for the pairs considered the same region.

    Pass 1: greedy by bounding-box IoU, highest first, each side used at most once.
    Pass 2 (leftovers): one box's centroid lies inside the other box; nearest centroids first.
    """
    ob = [polygon_bbox(p) for p in old]
    nb = [polygon_bbox(p) for p in new]
    matched: dict[int, int] = {}
    used_old: set[int] = set()

    pairs = [(bbox_iou(o, n), ni, oi) for ni, n in enumerate(nb) for oi, o in enumerate(ob)]
    for iou, ni, oi in sorted(pairs, key=lambda t: (-t[0], t[1], t[2])):
        if iou < iou_threshold:
            break
        if ni in matched or oi in used_old:
            continue
        matched[ni] = oi
        used_old.add(oi)

    candidates: list[tuple[float, int, int]] = []
    for ni, n in enumerate(nb):
        if ni in matched:
            continue
        cn = _centroid(n)
        for oi, o in enumerate(ob):
            if oi in used_old:
                continue
            co = _centroid(o)
            if _contains(o, cn) or _contains(n, co):
                candidates.append((math.dist(cn, co), ni, oi))
    for _, ni, oi in sorted(candidates):
        if ni in matched or oi in used_old:
            continue
        matched[ni] = oi
        used_old.add(oi)
    return matched


def has_user_edits(row: dict[str, Any]) -> bool:
    """True when deleting the region would lose something the user did by hand."""
    return bool(
        row.get("source_edited")
        or row.get("final_edited")
        or row.get("reading_order_edited")
        or any(row.get(flag) for flag in MANUAL_FIELD_FLAGS)
        or (row.get("review_status") or "pending") != "pending"
        or (row.get("mask_mode") or "auto") != "auto"
        or row.get("mask_box_json")
        or row.get("text_box_json")
        or row.get("font_size") is not None
    )


@dataclass
class MergePlan:
    update: list[tuple[dict[str, Any], dict[str, Any]]] = field(default_factory=list)  # (existing row, ocr region)
    insert: list[dict[str, Any]] = field(default_factory=list)
    delete: list[dict[str, Any]] = field(default_factory=list)
    keep_missing: list[dict[str, Any]] = field(default_factory=list)  # edited, no longer detected


def plan_merge(existing: list[dict[str, Any]], ocr_regions: list[dict[str, Any]]) -> MergePlan:
    """Decide what happens to each existing row and each new OCR region.

    User-drawn regions take part in matching (so OCR does not duplicate them) but are never deleted.
    Auto regions that OCR no longer finds are deleted only when they carry no user edits.
    """
    matched = match_polygons([json.loads(r["polygon_json"]) for r in existing], [r["polygon"] for r in ocr_regions])
    plan = MergePlan()
    for ni, reg in enumerate(ocr_regions):
        if ni in matched:
            plan.update.append((existing[matched[ni]], reg))
        else:
            plan.insert.append(reg)
    matched_old = set(matched.values())
    for oi, row in enumerate(existing):
        if oi in matched_old or row.get("origin") != "auto":
            continue
        if has_user_edits(row):
            plan.keep_missing.append(row)
        else:
            plan.delete.append(row)
    return plan


def load_flags(raw: str | None) -> list[str]:
    try:
        flags = json.loads(raw or "[]")
    except ValueError:
        return []
    return [str(f) for f in flags] if isinstance(flags, list) else []


def with_flag(flags: list[str], flag: str) -> list[str]:
    return flags if flag in flags else [*flags, flag]


def without_flag(flags: list[str], flag: str) -> list[str]:
    return [f for f in flags if f != flag]


def merge_flags(current: list[str], fresh: list[str]) -> list[str]:
    """Flags after a new translation result: provider flags plus the sticky ones already present."""
    out = [f for f in current if f in STICKY_FLAGS]
    for f in fresh:
        if f not in out:
            out.append(f)
    return out
