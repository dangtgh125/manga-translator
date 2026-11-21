"""A translation produced under a context that changed while the provider was busy is never
written as current: target language, glossary, page source language override or a region's
source language changed mid-job keep ``translation_stale`` set and flag the region."""

import pytest

from tests.conftest import create_mock_provider, create_project_with_pages, run_worker_once
from tests.integration.test_edit_safety import FIRST_OCR, _patch

CONTEXT_FLAG = "context_changed_during_job"


def _ocr(client):
    prj, pages = create_project_with_pages(client, 1, FIRST_OCR)
    prov = create_mock_provider(client)
    client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "ocr"})
    run_worker_once()
    return prj, pages[0]["id"], prov


def _translate_with_hook(client, monkeypatch, prj, prov, hook):
    """Run a translate job; ``hook`` runs once, while the provider is "thinking" about the first batch."""
    from app.translate.mock import MockProvider

    original = MockProvider.translate_batch
    calls = {"n": 0}

    def patched(self, items, ctx):
        if calls["n"] == 0:
            calls["n"] += 1
            hook()
        return original(self, items, ctx)

    monkeypatch.setattr(MockProvider, "translate_batch", patched)
    r = client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "translate", "provider_profile_id": prov["id"]})
    run_worker_once()
    assert client.get(f"/api/jobs/{r.json()['id']}").json()["status"] == "done"


def _regions(client, pid):
    return client.get(f"/api/pages/{pid}/regions").json()


@pytest.mark.parametrize("change", ["target_lang", "glossary", "page_source_lang"])
def test_context_change_during_job_keeps_translation_stale(client, monkeypatch, change):
    prj, pid, prov = _ocr(client)

    def hook():
        if change == "page_source_lang":
            page = client.get(f"/api/pages/{pid}").json()
            r = client.patch(f"/api/pages/{pid}", json={"version": page["version"], "source_lang_override": "zh-Hant"})
        else:
            p = client.get(f"/api/projects/{prj['id']}").json()
            body = {"target_lang": "en"} if change == "target_lang" else {"glossary": [{"source": "a", "target": "b"}]}
            r = client.patch(f"/api/projects/{prj['id']}", json={"version": p["version"], **body})
        assert r.status_code == 200, r.text

    _translate_with_hook(client, monkeypatch, prj, prov, hook)
    regs = _regions(client, pid)
    assert len(regs) == len(FIRST_OCR)
    for r in regs:
        # The result is recorded (it was paid for) but reported as outdated.
        assert r["translated_text"] == f"[vi] {r['source_text']}", r
        assert r["translation_stale"] and CONTEXT_FLAG in r["translation_flags"], r
    page = client.get(f"/api/pages/{pid}").json()
    assert page["translation_stale"] and page["translation_stale_count"] == len(FIRST_OCR)

    # Re-translating under the new context clears it (the hook only fires once).
    client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "translate", "provider_profile_id": prov["id"]})
    run_worker_once()
    prefix = "[en]" if change == "target_lang" else "[vi]"
    for r in _regions(client, pid):
        assert not r["translation_stale"] and CONTEXT_FLAG not in r["translation_flags"], r
        assert r["translated_text"].startswith(prefix)
    assert not client.get(f"/api/pages/{pid}").json()["translation_stale"]


def test_unrelated_project_edit_during_job_keeps_translation_current(client, monkeypatch):
    prj, pid, prov = _ocr(client)

    def hook():
        p = client.get(f"/api/projects/{prj['id']}").json()
        r = client.patch(
            f"/api/projects/{prj['id']}", json={"version": p["version"], "name": "renamed", "glossary": []}
        )
        assert r.status_code == 200

    _translate_with_hook(client, monkeypatch, prj, prov, hook)
    for r in _regions(client, pid):
        assert not r["translation_stale"] and CONTEXT_FLAG not in r["translation_flags"], r
        assert r["final_text"] == f"[vi] {r['source_text']}"


def test_region_source_lang_edit_during_job_keeps_that_region_stale(client, monkeypatch):
    prj, pid, prov = _ocr(client)
    target = _regions(client, pid)[0]

    def hook():
        _patch(client, pid, target, source_lang="zh-Hant")

    _translate_with_hook(client, monkeypatch, prj, prov, hook)
    after = {r["id"]: r for r in _regions(client, pid)}
    t = after[target["id"]]
    # Translated as Japanese, but the region is now Traditional Chinese: outdated, user edit flagged.
    assert t["translation_stale"] and "user_edited_during_job" in t["translation_flags"]
    assert t["source_lang"] == "zh-Hant" and t["source_lang_edited"] and CONTEXT_FLAG not in t["translation_flags"]
    others = [r for rid, r in after.items() if rid != target["id"]]
    assert others and all(not r["translation_stale"] for r in others)
