"""A polygon, text direction or source language set by hand survives an OCR re-run.

Matched regions keep the edited field while the other OCR-derived fields refresh; an edited region
that OCR no longer finds is kept and flagged ``ocr_missing`` instead of being deleted.
"""

import pytest

from tests.conftest import create_project_with_pages, make_png, run_worker_once
from tests.integration.test_edit_safety import FIRST_OCR, SECOND_OCR, _patch, _rect, _set_work_image

EDITS = {
    "polygon": {"polygon": _rect(300, 20, 340, 210)},
    "direction": {"direction": "horizontal"},
    "source_lang": {"source_lang": "zh-Hant"},
}
FLAG = {"polygon": "polygon_edited", "direction": "direction_edited", "source_lang": "source_lang_edited"}


def _ocr_once(client):
    prj, pages = create_project_with_pages(client, 1, FIRST_OCR)
    pid = pages[0]["id"]
    client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "ocr"})
    run_worker_once()
    return pid, {r["ocr_text"]: r for r in client.get(f"/api/pages/{pid}/regions").json()}


def _rerun_ocr(client, pid, ocr):
    _set_work_image(pid, make_png(mock_regions=ocr))
    r = client.post(f"/api/pages/{pid}/retry", json={"step": "ocr"})
    run_worker_once()
    assert client.get(f"/api/jobs/{r.json()['id']}").json()["status"] == "done"
    return {r["id"]: r for r in client.get(f"/api/pages/{pid}/regions").json()}


@pytest.mark.parametrize("kind", sorted(EDITS))
def test_edited_field_survives_rerun_on_matched_region(client, kind):
    pid, by_text = _ocr_once(client)
    a = by_text["こんにちは"]
    assert not any(a[f] for f in FLAG.values())
    a = _patch(client, pid, a, **EDITS[kind])
    assert a[FLAG[kind]] and not any(a[f] for f in FLAG.values() if f != FLAG[kind])

    regs = _rerun_ocr(client, pid, SECOND_OCR)
    a2 = regs[a["id"]]
    # OCR-derived text follows the new run; the user's field does not.
    assert a2["ocr_text"] == a2["source_text"] == "さようなら" and a2[FLAG[kind]]
    if kind == "polygon":
        assert a2["polygon"] == EDITS["polygon"]["polygon"]
        assert a2["direction"] == "vertical"  # not edited: follows OCR
    elif kind == "direction":
        assert a2["direction"] == "horizontal"
        assert a2["polygon"][0] == [302, 22]  # not edited: follows OCR
    else:
        assert a2["source_lang"] == "zh-Hant" and not a2["lang_uncertain"] and a2["lang_confidence"] == 1.0
        assert a2["polygon"][0] == [302, 22]
    assert "ocr_missing" not in a2["translation_flags"]


@pytest.mark.parametrize("kind", sorted(EDITS))
def test_edited_region_no_longer_detected_is_kept_and_flagged(client, kind):
    pid, by_text = _ocr_once(client)
    b, c = by_text["元気ですか"], by_text["再见"]
    edit = {"polygon": _rect(20, 20, 60, 210)} if kind == "polygon" else EDITS[kind]
    b = _patch(client, pid, b, **edit)
    assert b[FLAG[kind]]

    regs = _rerun_ocr(client, pid, SECOND_OCR)
    assert b["id"] in regs and c["id"] not in regs  # B edited -> kept; C untouched -> deleted
    b2 = regs[b["id"]]
    assert "ocr_missing" in b2["translation_flags"] and b2["origin"] == "auto"
    assert b2[kind] == b[kind]
    assert len(regs) == 4  # A, E (matched), B (kept), D (new)


def test_unchanged_values_do_not_set_manual_edit_flags(client):
    pid, by_text = _ocr_once(client)
    a = by_text["こんにちは"]
    a = _patch(client, pid, a, direction=a["direction"], source_lang=a["source_lang"], polygon=a["polygon"])
    assert not a["polygon_edited"] and not a["direction_edited"] and not a["source_lang_edited"]
    # ... so the region still follows OCR and is deleted when untouched and no longer detected.
    regs = _rerun_ocr(client, pid, SECOND_OCR)
    assert regs[a["id"]]["polygon"][0] == [302, 22]
    assert by_text["再见"]["id"] not in regs
