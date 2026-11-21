"""PLAN.md §10 P1: edits survive OCR re-runs and concurrent translation jobs; stale renders are never shipped."""

import io
import zipfile

import pytest
from app import storage
from app.db import connection

from tests.conftest import create_mock_provider, create_project_with_pages, make_png, run_worker_once


def _rect(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


# Four well separated bubbles: A/B vertical at the top corners, E/C horizontal at the bottom.
FIRST_OCR = [
    {"polygon": _rect(300, 20, 330, 200), "text": "こんにちは", "confidence": 0.95},
    {"polygon": _rect(20, 20, 50, 200), "text": "元気ですか", "confidence": 0.9},
    {"polygon": _rect(220, 500, 380, 530), "text": "这是简体", "confidence": 0.9},
    {"polygon": _rect(20, 400, 200, 430), "text": "再见", "confidence": 0.9},
]
# Second run: A moved slightly with new text, E re-read with different text, B and C gone, D is new.
SECOND_OCR = [
    {"polygon": _rect(302, 22, 332, 202), "text": "さようなら", "confidence": 0.97},
    {"polygon": _rect(220, 500, 380, 530), "text": "这是新的", "confidence": 0.9},
    {"polygon": _rect(20, 250, 200, 280), "text": "新しい吹き出し", "confidence": 0.8},
]


def _set_work_image(page_id: str, png: bytes) -> None:
    with connection() as conn:
        row = conn.execute("SELECT work_path FROM pages WHERE id=?", (page_id,)).fetchone()
    storage.abs_path(row["work_path"]).write_bytes(png)


def _regions(client, pid):
    return {
        r["source_text"] if not r["source_edited"] else r["id"]: r
        for r in client.get(f"/api/pages/{pid}/regions").json()
    }


def _patch(client, pid, reg, **body):
    r = client.patch(f"/api/pages/{pid}/regions/{reg['id']}", json={"version": reg["version"], **body})
    assert r.status_code == 200, r.text
    return r.json()


def test_rerun_ocr_merges_into_existing_regions_and_keeps_edits(client):
    prj, pages = create_project_with_pages(client, 1, FIRST_OCR)
    pid = pages[0]["id"]
    prov = create_mock_provider(client)
    client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "all", "provider_profile_id": prov["id"]})
    run_worker_once()
    by_text = {r["ocr_text"]: r for r in client.get(f"/api/pages/{pid}/regions").json()}
    assert set(by_text) == {"こんにちは", "元気ですか", "这是简体", "再见"}
    a, b, e, c = by_text["こんにちは"], by_text["元気ですか"], by_text["这是简体"], by_text["再见"]
    # A: source + translation edited, approved, manual font size. B: translation edited only. E, C: untouched.
    a = _patch(
        client, pid, a, source_text="こんにちは！", final_text="Xin chào!", review_status="approved", font_size=18
    )
    b = _patch(client, pid, b, final_text="Bạn khỏe không?")
    # Swap the two top bubbles by hand; C and E keep their positions (so C stays "untouched").
    ids0 = [r["id"] for r in client.get(f"/api/pages/{pid}/regions").json()]
    assert set(ids0[:2]) == {a["id"], b["id"]}
    order = [ids0[1], ids0[0], *ids0[2:]]
    assert client.post(f"/api/pages/{pid}/regions/reorder", json={"region_ids": order}).status_code == 200

    _set_work_image(pid, make_png(mock_regions=SECOND_OCR))
    r = client.post(f"/api/pages/{pid}/retry", json={"step": "ocr"})
    run_worker_once()
    assert client.get(f"/api/jobs/{r.json()['id']}").json()["status"] == "done"

    regs = {r["id"]: r for r in client.get(f"/api/pages/{pid}/regions").json()}
    # Matched region keeps its ID and every user edit; OCR-derived fields follow the new run.
    a2 = regs[a["id"]]
    assert a2["ocr_text"] == "さようなら" and a2["ocr_confidence"] == pytest.approx(0.97)
    assert a2["polygon"][0] == [302, 22]
    assert a2["source_text"] == "こんにちは！" and a2["source_edited"]
    assert a2["final_text"] == "Xin chào!" and a2["final_edited"]
    assert a2["review_status"] == "approved" and a2["font_size"] == 18
    assert "ocr_missing" not in a2["translation_flags"]
    # Edited region no longer detected: kept and flagged for the user to decide.
    b2 = regs[b["id"]]
    assert b2["final_text"] == "Bạn khỏe không?" and "ocr_missing" in b2["translation_flags"]
    # Untouched region no longer detected: removed.
    assert c["id"] not in regs
    # Untouched matched region whose text changed: source follows OCR, translation outdated.
    e2 = regs[e["id"]]
    assert e2["ocr_text"] == e2["source_text"] == "这是新的" and e2["translation_stale"]
    assert e2["translated_text"] == "[vi] 这是简体"  # old translation kept until re-translated
    # New detection gets a new ID, appended after the user's reading order.
    new = [r for r in regs.values() if r["id"] not in {a["id"], b["id"], e["id"]}]
    assert len(new) == 1 and new[0]["ocr_text"] == new[0]["source_text"] == "新しい吹き出し"
    assert all(regs[i]["reading_order"] == order.index(i) for i in (a["id"], b["id"], e["id"]))
    assert new[0]["reading_order"] == 4
    # Re-running OCR changes what gets drawn: the page render is stale until rebuilt.
    page = client.get(f"/api/pages/{pid}").json()
    assert page["render_stale"] and not page["has_render"]

    # A later run that finds B again clears the flag instead of creating a duplicate.
    _set_work_image(pid, make_png(mock_regions=SECOND_OCR + [FIRST_OCR[1]]))
    client.post(f"/api/pages/{pid}/retry", json={"step": "ocr"})
    run_worker_once()
    regs = {r["id"]: r for r in client.get(f"/api/pages/{pid}/regions").json()}
    assert len(regs) == 4 and "ocr_missing" not in regs[b["id"]]["translation_flags"]
    assert regs[b["id"]]["final_text"] == "Bạn khỏe không?"


def test_rerun_ocr_does_not_duplicate_user_drawn_region(client):
    prj, pages = create_project_with_pages(client, 1, FIRST_OCR[:1])
    pid = pages[0]["id"]
    r = client.post(
        f"/api/pages/{pid}/regions",
        json={"polygon": _rect(20, 400, 200, 430), "source_text": "手動", "source_lang": "zh-Hans"},
    )
    user = r.json()
    _set_work_image(pid, make_png(mock_regions=FIRST_OCR[:1] + [FIRST_OCR[3]]))
    client.post(f"/api/pages/{pid}/retry", json={"step": "ocr"})
    run_worker_once()
    regs = {r["id"]: r for r in client.get(f"/api/pages/{pid}/regions").json()}
    assert len(regs) == 2
    u = regs[user["id"]]
    assert u["origin"] == "user" and u["source_text"] == "手動" and u["ocr_text"] == "再见"
    assert u["polygon"] == [[20, 400], [200, 400], [200, 430], [20, 430]]  # user's polygon kept


def test_translate_job_does_not_overwrite_edits_made_while_running(client, monkeypatch):
    prj, pages = create_project_with_pages(client, 1, FIRST_OCR)
    pid = pages[0]["id"]
    prov = create_mock_provider(client)
    client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "ocr"})
    run_worker_once()
    before = {r["ocr_text"]: r for r in client.get(f"/api/pages/{pid}/regions").json()}
    a, e, c = before["こんにちは"], before["这是简体"], before["再见"]

    from app.translate.mock import MockProvider

    original = MockProvider.translate_batch
    calls = {"n": 0}

    def slow_then_user_edits(self, items, ctx):
        # Simulates a slow provider: while it "thinks", the user edits regions through the API.
        if calls["n"] == 0:
            calls["n"] += 1
            _patch(client, pid, a, final_text="Bản sửa tay")
            _patch(client, pid, e, source_text="改过的原文")
            _patch(client, pid, c, review_status="approved")
        return original(self, items, ctx)

    monkeypatch.setattr(MockProvider, "translate_batch", slow_then_user_edits)
    r = client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "translate", "provider_profile_id": prov["id"]})
    run_worker_once()
    assert client.get(f"/api/jobs/{r.json()['id']}").json()["status"] == "done"

    after = {r["id"]: r for r in client.get(f"/api/pages/{pid}/regions").json()}
    # final_text edited during the job wins; the machine translation is still recorded.
    a2 = after[a["id"]]
    assert a2["final_text"] == "Bản sửa tay" and a2["final_edited"]
    assert a2["translated_text"] == "[vi] こんにちは" and not a2["translation_stale"]
    assert "user_edited_during_job" in a2["translation_flags"]
    # source_text changed during the job: the result is for the old text, so it stays stale.
    e2 = after[e["id"]]
    assert e2["translated_text"] == "[vi] 这是简体" and e2["source_text"] == "改过的原文" and e2["translation_stale"]
    assert "user_edited_during_job" in e2["translation_flags"]
    # an unrelated edit (review status) keeps the review but takes the translation normally
    c2 = after[c["id"]]
    assert c2["review_status"] == "approved" and c2["final_text"] == "[vi] 再见" and not c2["translation_stale"]
    # untouched region: plain result, no flag
    b2 = after[before["元気ですか"]["id"]]
    assert b2["final_text"] == "[vi] 元気ですか" and "user_edited_during_job" not in b2["translation_flags"]


def _render_bytes(client, pid):
    r = client.get(f"/api/pages/{pid}/render.png")
    assert r.status_code == 200, r.text
    return r.content


def test_stale_render_is_rebuilt_before_download_and_export(client):
    prj, pages = create_project_with_pages(client, 2, FIRST_OCR)
    pid, other = pages[0]["id"], pages[1]["id"]
    prov = create_mock_provider(client)
    client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "all", "provider_profile_id": prov["id"]})
    run_worker_once()
    original = client.get(f"/api/pages/{pid}/original").content
    page = client.get(f"/api/pages/{pid}").json()
    assert page["has_render"] and not page["render_stale"]
    first = _render_bytes(client, pid)

    reg = client.get(f"/api/pages/{pid}/regions").json()[0]
    _patch(client, pid, reg, final_text="MỘT DÒNG CHỮ HOÀN TOÀN KHÁC VÀ DÀI HƠN NHIỀU")
    page = client.get(f"/api/pages/{pid}").json()
    assert page["render_stale"] and not page["has_render"]
    assert client.get(f"/api/pages/{other}").json()["has_render"]  # other page untouched

    # Single page download rebuilds from current data and clears the flag.
    second = _render_bytes(client, pid)
    assert second != first and second.startswith(b"\x89PNG")
    page = client.get(f"/api/pages/{pid}").json()
    assert page["has_render"] and not page["render_stale"]
    assert client.get(f"/api/pages/{pid}/render.png").content == second

    # Layout edit -> stale again; ZIP export must not contain the old image.
    reg = client.get(f"/api/pages/{pid}/regions").json()[0]
    _patch(client, pid, reg, font_size=40)
    assert client.get(f"/api/pages/{pid}").json()["render_stale"]
    r = client.get(f"/api/projects/{prj['id']}/export?fmt=zip")
    assert r.status_code == 200
    exported = zipfile.ZipFile(io.BytesIO(r.content)).read("page_0001.png")
    assert exported != second and exported != first
    page = client.get(f"/api/pages/{pid}").json()
    assert page["has_render"] and not page["render_stale"]
    assert client.get(f"/api/pages/{pid}/render.png").content == exported

    # Images-only export does the same.
    _patch(client, pid, client.get(f"/api/pages/{pid}/regions").json()[0], final_text="Ngắn")
    r = client.get(f"/api/projects/{prj['id']}/export?fmt=png&pages={pid}")
    assert r.status_code == 200
    img = zipfile.ZipFile(io.BytesIO(r.content)).read("page_0001.png")
    assert img != exported and client.get(f"/api/pages/{pid}/render.png").content == img

    # The original upload is never touched by any re-render.
    assert client.get(f"/api/pages/{pid}/original").content == original == make_png(mock_regions=FIRST_OCR)


def test_stale_render_that_cannot_be_rebuilt_is_refused(client):
    prj, pages = create_project_with_pages(client, 1, FIRST_OCR)
    pid = pages[0]["id"]
    prov = create_mock_provider(client)
    client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "all", "provider_profile_id": prov["id"]})
    run_worker_once()
    reg = client.get(f"/api/pages/{pid}/regions").json()[0]
    _patch(client, pid, reg, final_text="khác")
    _set_work_image(pid, b"not an image")
    r = client.get(f"/api/pages/{pid}/render.png")
    assert r.status_code == 409 and "outdated" in r.json()["detail"]
    for fmt in ("zip", "png"):
        r = client.get(f"/api/projects/{prj['id']}/export?fmt={fmt}")
        assert r.status_code == 409 and "page 1" in r.json()["detail"], fmt
    # JSON/TXT exports carry no image and keep working.
    assert client.get(f"/api/projects/{prj['id']}/export?fmt=json").status_code == 200
    assert client.get(f"/api/pages/{pid}").json()["render_stale"]


def test_layout_and_text_edits_mark_render_stale(client):
    prj, pages = create_project_with_pages(client, 1, FIRST_OCR)
    pid = pages[0]["id"]
    prov = create_mock_provider(client)
    client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "all", "provider_profile_id": prov["id"]})
    run_worker_once()

    def rerender():
        client.post(f"/api/pages/{pid}/retry", json={"step": "render"})
        run_worker_once()
        page = client.get(f"/api/pages/{pid}").json()
        assert page["has_render"] and not page["render_stale"]

    def stale():
        return client.get(f"/api/pages/{pid}").json()["render_stale"]

    reg = lambda: client.get(f"/api/pages/{pid}/regions").json()[0]  # noqa: E731
    for body in (
        {"polygon": _rect(300, 20, 340, 210)},
        {"mask_mode": "box"},
        {"mask_box": {"x": 290, "y": 10, "w": 60, "h": 200}},
        {"text_box": {"x": 295, "y": 15, "w": 50, "h": 190}},
        {"font_size": 20},
        {"reading_order": 5},
        {"final_text": "sửa"},
    ):
        rerender()
        _patch(client, pid, reg(), **body)
        assert stale(), body
    # Review status alone does not change the drawing; a source text edit on a translated region
    # outdates the translation the render draws, so the render is outdated too.
    rerender()
    _patch(client, pid, reg(), review_status="approved")
    assert not stale()
    rerender()
    _patch(client, pid, reg(), source_text="x")
    assert stale()
    # Region create, reorder and delete do.
    rerender()
    r = client.post(f"/api/pages/{pid}/regions", json={"polygon": _rect(10, 300, 100, 330)})
    assert r.status_code == 201 and stale()
    rerender()
    ids = [x["id"] for x in client.get(f"/api/pages/{pid}/regions").json()]
    client.post(f"/api/pages/{pid}/regions/reorder", json={"region_ids": ids[::-1]})
    assert stale()
    rerender()
    assert client.delete(f"/api/pages/{pid}/regions/{r.json()['id']}").status_code == 204 and stale()
    # A translation job rewrites text -> stale; a full "all" job ends with a fresh render.
    rerender()
    client.post(f"/api/pages/{pid}/retry", json={"step": "translate", "provider_profile_id": prov["id"]})
    run_worker_once()
    assert stale()
    client.post(f"/api/pages/{pid}/retry", json={"step": "all", "provider_profile_id": prov["id"]})
    run_worker_once()
    assert not stale()


def test_language_and_glossary_changes_mark_translations_stale(client):
    prj, pages = create_project_with_pages(client, 2, FIRST_OCR)
    prov = create_mock_provider(client)
    client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "all", "provider_profile_id": prov["id"]})
    run_worker_once()
    pid = pages[0]["id"]

    def all_fresh():
        regs = [r for p in pages for r in client.get(f"/api/pages/{p['id']}/regions").json()]
        return not any(r["translation_stale"] for r in regs)

    def retranslate():
        client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "translate", "provider_profile_id": prov["id"]})
        run_worker_once()
        assert all_fresh()

    assert all_fresh()
    # project target language
    prj = client.get(f"/api/projects/{prj['id']}").json()
    r = client.patch(f"/api/projects/{prj['id']}", json={"version": prj["version"], "target_lang": "en"})
    assert r.status_code == 200
    assert all(r["translation_stale"] for p in pages for r in client.get(f"/api/pages/{p['id']}/regions").json())
    retranslate()
    # unrelated project edits (name) and an identical glossary do not
    prj = client.get(f"/api/projects/{prj['id']}").json()
    client.patch(f"/api/projects/{prj['id']}", json={"version": prj["version"], "name": "renamed", "glossary": []})
    assert all_fresh()
    # glossary content
    prj = client.get(f"/api/projects/{prj['id']}").json()
    r = client.patch(
        f"/api/projects/{prj['id']}",
        json={"version": prj["version"], "glossary": [{"source": "こんにちは", "target": "Hello"}]},
    )
    assert r.status_code == 200 and not all_fresh()
    retranslate()
    # page source language override: only that page
    page = client.get(f"/api/pages/{pid}").json()
    r = client.patch(f"/api/pages/{pid}", json={"version": page["version"], "source_lang_override": "zh-Hant"})
    assert r.status_code == 200
    assert all(r["translation_stale"] for r in client.get(f"/api/pages/{pid}/regions").json())
    assert not any(r["translation_stale"] for r in client.get(f"/api/pages/{pages[1]['id']}/regions").json())
    retranslate()
    # region source language
    reg = client.get(f"/api/pages/{pid}/regions").json()[0]
    assert _patch(client, pid, reg, source_lang="ja")["translation_stale"]
    # has no effect on untranslated regions (nothing to be stale about)
    r = client.post(f"/api/pages/{pid}/regions", json={"polygon": _rect(10, 300, 100, 330), "source_text": "a"})
    page = client.get(f"/api/pages/{pid}").json()
    client.patch(f"/api/pages/{pid}", json={"version": page["version"], "clear_source_lang_override": True})
    new = next(x for x in client.get(f"/api/pages/{pid}/regions").json() if x["id"] == r.json()["id"])
    assert not new["translation_stale"]
