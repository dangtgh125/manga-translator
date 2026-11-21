"""A hand-written final text is a translation for one target language only.

Changing the project's target language must outdate regions whose only translation is a manual
``final_text``; re-translating afterwards must not clear the stale flag while the manual text is still
in the old language; the user resolves it by editing the text or taking the machine translation.
"""

import io
import zipfile

from app.db import connection

from tests.conftest import create_mock_provider, create_project_with_pages, run_worker_once
from tests.integration.test_edit_safety import FIRST_OCR, _patch


def _regions(client, pid):
    return client.get(f"/api/pages/{pid}/regions").json()


def _page(client, pid):
    return client.get(f"/api/pages/{pid}").json()


def _set_target(client, prj_id, lang):
    prj = client.get(f"/api/projects/{prj_id}").json()
    r = client.patch(f"/api/projects/{prj_id}", json={"version": prj["version"], "target_lang": lang})
    assert r.status_code == 200, r.text


def _run(client, prj_id, step, prov=None):
    body = {"step": step}
    if prov:
        body["provider_profile_id"] = prov["id"]
    r = client.post(f"/api/projects/{prj_id}/jobs", json=body)
    assert r.status_code == 201, r.text
    run_worker_once()


def _export_status(client, prj_id, fmt="png"):
    return client.get(f"/api/projects/{prj_id}/export?fmt={fmt}").status_code


def test_manual_only_final_text_is_outdated_by_target_lang_change(client):
    prj, pages = create_project_with_pages(client, 1, FIRST_OCR)
    pid = pages[0]["id"]
    _run(client, prj["id"], "ocr")
    regs = _regions(client, pid)
    for reg in regs:  # no provider involved: every translation is typed by hand
        reg = _patch(client, pid, reg, final_text=f"tay {reg['reading_order']}")
        assert reg["final_edited"] and reg["final_lang"] == "vi" and not reg["translated_text"]
    _run(client, prj["id"], "render")
    page = _page(client, pid)
    assert page["has_render"] and not page["translation_stale"]
    assert _export_status(client, prj["id"]) == 200

    _set_target(client, prj["id"], "en")
    page = _page(client, pid)
    assert page["translation_stale"] and page["translation_stale_count"] == len(FIRST_OCR)
    assert page["render_stale"] and not page["has_render"]
    assert all(r["translation_stale"] for r in _regions(client, pid))
    for fmt in ("png", "zip"):
        assert _export_status(client, prj["id"], fmt) == 409

    # Typing the English text resolves it; the render is rebuilt with the new text.
    for reg in _regions(client, pid):
        reg = _patch(client, pid, reg, final_text=f"hand {reg['reading_order']}")
        assert not reg["translation_stale"] and reg["final_lang"] == "en"
    assert not _page(client, pid)["translation_stale"]
    r = client.get(f"/api/projects/{prj['id']}/export?fmt=zip")
    assert r.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert "page_0001.png" in names


def test_retranslation_keeps_hand_edited_text_of_old_language_stale(client):
    prj, pages = create_project_with_pages(client, 1, FIRST_OCR)
    pid = pages[0]["id"]
    prov = create_mock_provider(client)
    _run(client, prj["id"], "all", prov)
    regs = _regions(client, pid)
    assert all(r["final_lang"] == "vi" and r["translated_text"].startswith("[vi]") for r in regs)
    edited = _patch(client, pid, regs[0], final_text="bản sửa tiếng Việt")
    assert edited["final_edited"] and edited["final_lang"] == "vi"
    untouched_id = regs[1]["id"]
    _run(client, prj["id"], "render")
    assert _export_status(client, prj["id"]) == 200

    _set_target(client, prj["id"], "en")
    assert _export_status(client, prj["id"]) == 409
    _run(client, prj["id"], "translate", prov)

    by_id = {r["id"]: r for r in _regions(client, pid)}
    hand = by_id[edited["id"]]
    assert hand["translated_text"].startswith("[en]")
    assert hand["final_text"] == "bản sửa tiếng Việt"  # never clobbered
    assert hand["translation_stale"] and "final_text_lang_mismatch" in hand["translation_flags"]
    auto = by_id[untouched_id]
    assert auto["final_text"].startswith("[en]") and auto["final_lang"] == "en" and not auto["translation_stale"]
    page = _page(client, pid)
    assert page["translation_stale_count"] == 1
    # The old Vietnamese text is never shipped as the English translation.
    assert _export_status(client, prj["id"]) == 409
    r = client.get(f"/api/projects/{prj['id']}/export?fmt=png&allow_stale_translation=1")
    assert r.status_code == 200 and "X-Export-Warning" in r.headers

    # Taking the machine translation resolves the region (same as the editor button).
    hand = _patch(client, pid, hand, final_text=hand["translated_text"])
    assert not hand["translation_stale"] and hand["final_lang"] == "en"
    assert "final_text_lang_mismatch" not in hand["translation_flags"]
    assert _page(client, pid)["translation_stale_count"] == 0
    assert _export_status(client, prj["id"]) == 200
    # A later clean translation keeps the flag away and final_edited text intact.
    _run(client, prj["id"], "translate", prov)
    hand = {r["id"]: r for r in _regions(client, pid)}[hand["id"]]
    assert not hand["translation_stale"] and hand["translation_flags"] == []


def test_switching_target_lang_back_makes_hand_text_current_again(client):
    prj, pages = create_project_with_pages(client, 1, FIRST_OCR)
    pid = pages[0]["id"]
    prov = create_mock_provider(client)
    _run(client, prj["id"], "all", prov)
    reg = _patch(client, pid, _regions(client, pid)[0], final_text="tay")
    _set_target(client, prj["id"], "en")
    _run(client, prj["id"], "translate", prov)
    reg = {r["id"]: r for r in _regions(client, pid)}[reg["id"]]
    assert reg["translation_stale"] and "final_text_lang_mismatch" in reg["translation_flags"]

    _set_target(client, prj["id"], "vi")
    _run(client, prj["id"], "translate", prov)
    reg = {r["id"]: r for r in _regions(client, pid)}[reg["id"]]
    assert reg["final_text"] == "tay" and reg["final_lang"] == "vi"
    assert not reg["translation_stale"] and "final_text_lang_mismatch" not in reg["translation_flags"]


def test_migration_0005_backfills_final_lang_from_project(client):
    prj, pages = create_project_with_pages(client, 1, FIRST_OCR)
    pid = pages[0]["id"]
    _run(client, prj["id"], "ocr")
    reg = _patch(client, pid, _regions(client, pid)[0], final_text="x")
    with connection() as conn:
        conn.execute("UPDATE regions SET final_lang='' WHERE id=?", (reg["id"],))
        conn.execute(
            "UPDATE regions SET final_lang = (SELECT target_lang FROM projects WHERE projects.id = regions.project_id)"
            " WHERE final_text <> '' AND final_lang = ''"
        )
        row = conn.execute("SELECT final_lang FROM regions WHERE id=?", (reg["id"],)).fetchone()
    assert row["final_lang"] == "vi"
