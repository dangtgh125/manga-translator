"""Outdated translations are never exported silently.

Whatever outdates a translation (target language, glossary, page/region source language, source text)
also outdates the page render in the same transaction; ZIP/PNG export answers 409 for such pages
unless ``allow_stale_translation=1`` is passed, in which case it re-renders and warns via a header.
"""

import io
import zipfile

from app import storage
from app.db import connection

from tests.conftest import create_mock_provider, create_project_with_pages, make_png, run_worker_once
from tests.integration.test_edit_safety import FIRST_OCR, _patch, _rect


def _setup(client, n=2):
    prj, pages = create_project_with_pages(client, n, FIRST_OCR)
    prov = create_mock_provider(client)
    client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "all", "provider_profile_id": prov["id"]})
    run_worker_once()
    return prj, pages, prov


def _page(client, pid):
    return client.get(f"/api/pages/{pid}").json()


def _retranslate(client, prj, prov):
    client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "translate", "provider_profile_id": prov["id"]})
    run_worker_once()


def _corrupt_render(pid):
    with connection() as conn:
        row = conn.execute("SELECT render_path FROM pages WHERE id=?", (pid,)).fetchone()
    storage.abs_path(row["render_path"]).write_bytes(b"old render")


def test_target_lang_change_outdates_render_and_export_refuses_until_retranslated(client):
    prj, pages, prov = _setup(client)
    pid, other = pages[0]["id"], pages[1]["id"]
    extra = client.post(f"/api/projects/{prj['id']}/pages", files=[("files", ("c.png", make_png(), "image/png"))])
    extra_id = extra.json()[0]["id"]
    for p in (pid, other):
        page = _page(client, p)
        assert page["has_render"] and not page["translation_stale"] and page["translation_stale_count"] == 0

    prj = client.get(f"/api/projects/{prj['id']}").json()
    r = client.patch(f"/api/projects/{prj['id']}", json={"version": prj["version"], "target_lang": "en"})
    assert r.status_code == 200
    for p in (pid, other):
        page = _page(client, p)
        assert page["render_stale"] and not page["has_render"]
        assert page["translation_stale"] and page["translation_stale_count"] == len(FIRST_OCR)
    assert not _page(client, extra_id)["render_stale"]  # nothing translated there

    for fmt in ("zip", "png"):
        r = client.get(f"/api/projects/{prj['id']}/export?fmt={fmt}")
        assert r.status_code == 409, fmt
        detail = r.json()["detail"]
        assert "page 1" in detail and "page 2" in detail and "allow_stale_translation" in detail
    # Only the selected pages are reported.
    r = client.get(f"/api/projects/{prj['id']}/export?fmt=png&pages={other}")
    assert r.status_code == 409 and "page 2" in r.json()["detail"] and "page 1" not in r.json()["detail"]
    # Text exports carry no image and keep working.
    assert client.get(f"/api/projects/{prj['id']}/export?fmt=json").status_code == 200
    assert client.get(f"/api/projects/{prj['id']}/export?fmt=txt").status_code == 200

    # Opting in re-renders (the stored file is not shipped) and warns.
    _corrupt_render(pid)
    r = client.get(f"/api/projects/{prj['id']}/export?fmt=png&allow_stale_translation=1")
    assert r.status_code == 200
    assert pid in r.headers["x-export-warning"] and other in r.headers["x-export-warning"]
    img = zipfile.ZipFile(io.BytesIO(r.content)).read("page_0001.png")
    assert img.startswith(b"\x89PNG") and img != b"old render"
    page = _page(client, pid)
    assert page["has_render"] and not page["render_stale"] and page["translation_stale"]
    r = client.get(f"/api/projects/{prj['id']}/export?fmt=zip&allow_stale_translation=1")
    assert r.status_code == 200 and "x-export-warning" in r.headers
    assert zipfile.ZipFile(io.BytesIO(r.content)).read("page_0001.png") == img
    # Still refused without the flag: the translation is as outdated as before.
    assert client.get(f"/api/projects/{prj['id']}/export?fmt=zip").status_code == 409

    _retranslate(client, prj, prov)
    page = _page(client, pid)
    assert not page["translation_stale"] and page["translation_stale_count"] == 0
    r = client.get(f"/api/projects/{prj['id']}/export?fmt=zip")
    assert r.status_code == 200 and "x-export-warning" not in r.headers
    exported = zipfile.ZipFile(io.BytesIO(r.content)).read("page_0001.png")
    assert exported != img  # "[en]" text was drawn instead of "[vi]"
    assert client.get(f"/api/projects/{prj['id']}/export?fmt=png").status_code == 200
    assert _page(client, pid)["has_render"]


def test_render_png_preview_still_serves_outdated_translation(client):
    prj, pages, _ = _setup(client, 1)
    pid = pages[0]["id"]
    prj = client.get(f"/api/projects/{prj['id']}").json()
    client.patch(f"/api/projects/{prj['id']}", json={"version": prj["version"], "target_lang": "en"})
    _corrupt_render(pid)
    r = client.get(f"/api/pages/{pid}/render.png")
    assert r.status_code == 200 and r.content.startswith(b"\x89PNG")
    page = _page(client, pid)
    assert page["has_render"] and page["translation_stale"]


def test_every_translation_outdating_change_outdates_the_render(client):
    prj, pages, prov = _setup(client, 1)
    pid = pages[0]["id"]

    def rerender():
        client.post(f"/api/pages/{pid}/retry", json={"step": "render"})
        run_worker_once()
        page = _page(client, pid)
        assert page["has_render"] and not page["render_stale"]

    def fresh():
        _retranslate(client, prj, prov)
        rerender()
        page = _page(client, pid)
        assert not page["translation_stale"]

    # glossary
    p = client.get(f"/api/projects/{prj['id']}").json()
    r = client.patch(
        f"/api/projects/{prj['id']}",
        json={"version": p["version"], "glossary": [{"source": "こんにちは", "target": "Hello"}]},
    )
    assert r.status_code == 200
    page = _page(client, pid)
    assert page["render_stale"] and page["translation_stale"]
    rerender()  # a render alone does not make the translation current
    assert _page(client, pid)["translation_stale"]
    fresh()
    # unrelated project edits do not
    p = client.get(f"/api/projects/{prj['id']}").json()
    client.patch(f"/api/projects/{prj['id']}", json={"version": p["version"], "name": "renamed"})
    assert not _page(client, pid)["render_stale"]
    # page source language override
    page = _page(client, pid)
    r = client.patch(f"/api/pages/{pid}", json={"version": page["version"], "source_lang_override": "zh-Hant"})
    assert r.status_code == 200 and r.json()["render_stale"] and r.json()["translation_stale"]
    fresh()
    # region source language
    reg = client.get(f"/api/pages/{pid}/regions").json()[0]
    assert _patch(client, pid, reg, source_lang="ja")["translation_stale"]
    assert _page(client, pid)["render_stale"]
    fresh()
    # region source text
    reg = client.get(f"/api/pages/{pid}/regions").json()[0]
    assert _patch(client, pid, reg, source_text="sửa")["translation_stale"]
    assert _page(client, pid)["render_stale"] and _page(client, pid)["translation_stale_count"] == 1
    fresh()
    # review status alone does not
    reg = client.get(f"/api/pages/{pid}/regions").json()[0]
    _patch(client, pid, reg, review_status="approved")
    assert not _page(client, pid)["render_stale"]
    # source edits on a region that was never translated do not outdate the render
    r = client.post(f"/api/pages/{pid}/regions", json={"polygon": _rect(10, 300, 100, 330), "source_text": "a"})
    assert r.status_code == 201
    rerender()
    _patch(client, pid, r.json(), source_text="b", source_lang="ja")
    assert not _page(client, pid)["render_stale"]
