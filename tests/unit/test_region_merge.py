"""Pure matching/merge policy used when OCR is re-run on a page that already has regions."""

import json

from app.region_merge import (
    OCR_MISSING_FLAG,
    bbox_iou,
    has_user_edits,
    match_polygons,
    merge_flags,
    plan_merge,
    with_flag,
    without_flag,
)


def _rect(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _row(rid, poly, **kw):
    r = {
        "id": rid,
        "polygon_json": json.dumps(poly),
        "origin": "auto",
        "source_edited": 0,
        "final_edited": 0,
        "reading_order_edited": 0,
        "review_status": "pending",
        "mask_mode": "auto",
        "mask_box_json": None,
        "text_box_json": None,
        "font_size": None,
        "translation_flags_json": "[]",
    }
    r.update(kw)
    return r


def test_bbox_iou():
    assert bbox_iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert bbox_iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
    assert abs(bbox_iou((0, 0, 10, 10), (5, 0, 15, 10)) - 1 / 3) < 1e-9


def test_match_polygons_greedy_best_iou_first():
    old = [_rect(0, 0, 100, 100), _rect(0, 0, 60, 100)]
    new = [_rect(2, 2, 98, 98)]
    # old[0] overlaps better than old[1]; each side is used at most once.
    assert match_polygons(old, new) == {0: 0}
    # below the IoU threshold and no centroid containment -> no match
    assert match_polygons([_rect(0, 0, 100, 100)], [_rect(90, 90, 300, 300)]) == {}


def test_match_polygons_centroid_fallback_for_shrunk_or_grown_boxes():
    # New detection is a small box inside the old one (IoU far below 0.5) -> still the same region.
    assert match_polygons([_rect(0, 0, 100, 100)], [_rect(40, 40, 60, 60)]) == {0: 0}
    # Old small box inside a new big one.
    assert match_polygons([_rect(40, 40, 60, 60)], [_rect(0, 0, 100, 100)]) == {0: 0}
    # Two candidates: nearest centroid wins, the other new region stays unmatched.
    old = [_rect(0, 0, 100, 100)]
    new = [_rect(45, 45, 55, 55), _rect(5, 5, 15, 15)]
    assert match_polygons(old, new) == {0: 0}


def test_has_user_edits_detects_every_edit_kind():
    assert not has_user_edits(_row("a", _rect(0, 0, 1, 1)))
    for kw in (
        {"source_edited": 1},
        {"final_edited": 1},
        {"reading_order_edited": 1},
        {"review_status": "approved"},
        {"mask_mode": "box"},
        {"mask_box_json": "{}"},
        {"text_box_json": "{}"},
        {"font_size": 12.0},
        {"polygon_edited": 1},
        {"direction_edited": 1},
        {"source_lang_edited": 1},
    ):
        assert has_user_edits(_row("a", _rect(0, 0, 1, 1), **kw)), kw


def test_has_user_edits_ignores_unset_manual_field_flags():
    # Rows from before migration 0004 (no columns) and rows with the flags at 0 are untouched.
    assert not has_user_edits(_row("a", _rect(0, 0, 1, 1), polygon_edited=0, direction_edited=0, source_lang_edited=0))
    row = _row("a", _rect(0, 0, 1, 1))
    for flag in ("polygon_edited", "direction_edited", "source_lang_edited"):
        row.pop(flag, None)
    assert not has_user_edits(row)


def test_plan_merge_keeps_undetected_region_with_a_manual_field_edit():
    for flag in ("polygon_edited", "direction_edited", "source_lang_edited"):
        existing = [_row("edited", _rect(20, 20, 50, 200), **{flag: 1}), _row("plain", _rect(20, 400, 200, 430))]
        plan = plan_merge(existing, [{"polygon": _rect(300, 20, 330, 200), "ocr_text": "A"}])
        assert [r["id"] for r in plan.keep_missing] == ["edited"], flag
        assert [r["id"] for r in plan.delete] == ["plain"], flag


def test_plan_merge_classifies_matched_new_missing_and_deleted():
    existing = [
        _row("matched", _rect(300, 20, 330, 200)),
        _row("edited-missing", _rect(20, 20, 50, 200), final_edited=1),
        _row("untouched-missing", _rect(20, 400, 200, 430)),
        _row("user-drawn", _rect(200, 300, 260, 330), origin="user", source_edited=1),
    ]
    ocr = [
        {"polygon": _rect(302, 22, 332, 202), "ocr_text": "A"},
        {"polygon": _rect(20, 250, 200, 280), "ocr_text": "new"},
    ]
    plan = plan_merge(existing, ocr)
    assert [(row["id"], reg["ocr_text"]) for row, reg in plan.update] == [("matched", "A")]
    assert [reg["ocr_text"] for reg in plan.insert] == ["new"]
    assert [r["id"] for r in plan.keep_missing] == ["edited-missing"]
    assert [r["id"] for r in plan.delete] == ["untouched-missing"]  # user-drawn regions are never deleted


def test_plan_merge_matches_user_drawn_region_instead_of_duplicating():
    existing = [_row("user", _rect(10, 10, 100, 50), origin="user", source_edited=1)]
    plan = plan_merge(existing, [{"polygon": _rect(12, 12, 98, 48), "ocr_text": "手動"}])
    assert [row["id"] for row, _ in plan.update] == ["user"] and not plan.insert and not plan.delete


def test_flag_helpers_keep_sticky_flags_across_translations():
    assert with_flag([], OCR_MISSING_FLAG) == [OCR_MISSING_FLAG]
    assert with_flag([OCR_MISSING_FLAG], OCR_MISSING_FLAG) == [OCR_MISSING_FLAG]
    assert without_flag(["needs_context", OCR_MISSING_FLAG], OCR_MISSING_FLAG) == ["needs_context"]
    assert merge_flags(["needs_context", OCR_MISSING_FLAG], ["glossary_miss"]) == [OCR_MISSING_FLAG, "glossary_miss"]
    assert merge_flags(["needs_context"], []) == []
