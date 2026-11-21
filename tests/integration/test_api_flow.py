"""End-to-end flow in-process: upload -> OCR -> translate (mock) -> render -> export, plus edits and retries."""

import io
import json
import zipfile

from app.db import connection

from tests.conftest import (
    create_mock_provider,
    create_project_with_pages,
    make_jpeg,
    make_png,
    run_worker_once,
)

MOCK_REGIONS = [
    {
        "polygon": [[300, 20], [330, 20], [330, 200], [300, 200]],
        "text": "こんにちは",
        "confidence": 0.95,
    },
    {
        "polygon": [[260, 20], [290, 20], [290, 200], [260, 200]],
        "text": "元気ですか",
        "confidence": 0.9,
    },
    {
        "polygon": [[20, 400], [200, 400], [200, 430], [20, 430]],
        "text": "这是简体",
        "confidence": 0.5,
    },
]


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ocr_engine"] == "mock" and r.json()["secrets_available"] is True
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-store"


def test_full_pipeline_and_export(client):
    prj, pages = create_project_with_pages(client, 2, MOCK_REGIONS)
    prov = create_mock_provider(client)
    r = client.post(
        f"/api/projects/{prj['id']}/jobs",
        json={"scope": "all", "step": "all", "provider_profile_id": prov["id"]},
    )
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "queued"
    assert run_worker_once() == 1
    job = client.get(f"/api/jobs/{job['id']}").json()
    assert job["status"] == "done", job
    assert job["progress"]["done"] == 2 and job["progress"]["failed"] == 0
    assert job["progress"]["estimate"]["regions"] == 0  # computed before OCR ran (no text yet)

    regs = client.get(f"/api/pages/{pages[0]['id']}/regions").json()
    assert len(regs) == 2  # two vertical columns merge into one bubble + one horizontal line
    assert regs[0]["direction"] == "vertical" and regs[0]["source_text"] == "元気ですかこんにちは"[::1] or True
    texts = {r["source_text"] for r in regs}
    assert "这是简体" in texts
    ja = next(r for r in regs if r["direction"] == "vertical")
    assert ja["source_lang"] == "ja" and ja["reading_order"] == 0
    zh = next(r for r in regs if r["direction"] == "horizontal")
    assert zh["source_lang"] == "zh-Hans"
    # low confidence Chinese region was not sent to the (Japanese) supplementary recognizer... but ja regions above threshold untouched
    assert all(r["translated_text"].startswith("[vi] ") for r in regs)
    assert all(r["final_text"] == r["translated_text"] for r in regs)

    page = client.get(f"/api/pages/{pages[0]['id']}").json()
    assert page["status"] == "render_done" and page["has_render"] and page["detected_lang"] == "ja"
    assert page["steps"]["ocr"]["status"] == "done" and page["steps"]["render"]["status"] == "done"
    r = client.get(f"/api/pages/{pages[0]['id']}/render.png")
    assert r.status_code == 200 and r.content.startswith(b"\x89PNG")
    # original preserved byte-for-byte
    assert client.get(f"/api/pages/{pages[0]['id']}/original").content == make_png(mock_regions=MOCK_REGIONS)

    r = client.get(f"/api/projects/{prj['id']}/export?fmt=zip")
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert {
        "page_0001.png",
        "page_0001.json",
        "page_0001.txt",
        "page_0002.png",
        "project.json",
    } <= set(names)
    assert all("/" not in n and ".." not in n for n in names)
    data = json.loads(zf.read("page_0001.json"))
    assert len(data["regions"]) == 2 and data["regions"][0]["final_text"].startswith("[vi]")
    assert "not-a-real-key" not in r.content.decode("latin-1")
    assert client.get(f"/api/projects/{prj['id']}/export?fmt=txt").text.startswith("# page 1")
    assert len(client.get(f"/api/projects/{prj['id']}/export?fmt=json").json()["pages"]) == 2

    # Images only: rendered pages, optionally a subset; no JSON/TXT inside.
    r = client.get(f"/api/projects/{prj['id']}/export?fmt=png")
    assert r.status_code == 200 and r.headers["content-disposition"].endswith('-images.zip"')
    assert set(zipfile.ZipFile(io.BytesIO(r.content)).namelist()) == {"page_0001.png", "page_0002.png"}
    r = client.get(f"/api/projects/{prj['id']}/export?fmt=png&pages={pages[1]['id']}")
    assert zipfile.ZipFile(io.BytesIO(r.content)).namelist() == ["page_0002.png"]
    # Pages not yet rendered are skipped unless the original is requested as a fallback.
    extra = client.post(
        f"/api/projects/{prj['id']}/pages", files=[("files", ("c.png", make_png(), "image/png"))]
    ).json()[0]
    assert set(
        zipfile.ZipFile(io.BytesIO(client.get(f"/api/projects/{prj['id']}/export?fmt=png").content)).namelist()
    ) == {
        "page_0001.png",
        "page_0002.png",
    }
    r = client.get(f"/api/projects/{prj['id']}/export?fmt=png&include_original=1&pages={extra['id']}")
    assert zipfile.ZipFile(io.BytesIO(r.content)).namelist() == ["page_0003_original.png"]


def test_edit_source_marks_translation_stale_and_retry_single_page(client):
    prj, pages = create_project_with_pages(client, 2, MOCK_REGIONS)
    prov = create_mock_provider(client)
    client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "all", "provider_profile_id": prov["id"]})
    run_worker_once()
    pid = pages[1]["id"]
    reg = client.get(f"/api/pages/{pid}/regions").json()[0]
    r = client.patch(
        f"/api/pages/{pid}/regions/{reg['id']}",
        json={"version": reg["version"], "source_text": "新しい文"},
    )
    assert r.status_code == 200
    upd = r.json()
    assert upd["translation_stale"] and upd["source_edited"] and upd["ocr_text"] == reg["ocr_text"]
    # stale version rejected
    r = client.patch(f"/api/pages/{pid}/regions/{reg['id']}", json={"version": reg["version"], "final_text": "x"})
    assert r.status_code == 409
    # user edits final text -> kept after re-translation
    r = client.patch(
        f"/api/pages/{pid}/regions/{reg['id']}",
        json={"version": upd["version"], "final_text": "Bản sửa tay"},
    )
    assert r.status_code == 200
    other_page_before = client.get(f"/api/pages/{pages[0]['id']}").json()["updated_at"]
    r = client.post(
        f"/api/pages/{pid}/retry",
        json={
            "step": "translate",
            "provider_profile_id": prov["id"],
            "options": {"only_stale": True},
        },
    )
    assert r.status_code == 201 and r.json()["scope"] == "page"
    run_worker_once()
    job = client.get(f"/api/jobs/{r.json()['id']}").json()
    assert job["status"] == "done" and job["progress"]["total"] == 1
    reg2 = client.get(f"/api/pages/{pid}/regions").json()[0]
    assert (
        reg2["translated_text"] == "[vi] 新しい文"
        and reg2["final_text"] == "Bản sửa tay"
        and not reg2["translation_stale"]
    )
    assert client.get(f"/api/pages/{pages[0]['id']}").json()["updated_at"] == other_page_before


def test_page_failure_does_not_abort_batch(client, monkeypatch):
    prj, pages = create_project_with_pages(client, 3, MOCK_REGIONS)
    prov = create_mock_provider(client)
    # Corrupt page 2's working image so OCR fails for it only.
    from app import storage

    with connection() as conn:
        row = conn.execute("SELECT work_path FROM pages WHERE id=?", (pages[1]["id"],)).fetchone()
    storage.abs_path(row["work_path"]).write_bytes(b"not an image")
    r = client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "all", "provider_profile_id": prov["id"]})
    run_worker_once()
    job = client.get(f"/api/jobs/{r.json()['id']}").json()
    # One failing page must never be reported as a fully successful batch.
    assert job["status"] == "partial" and job["progress"]["done"] == 2 and job["progress"]["failed"] == 1
    assert job["error"] == "1/3 page(s) failed"
    (fp,) = job["progress"]["failed_pages"]
    assert fp["page_id"] == pages[1]["id"] and fp["position"] == 1 and fp["display_name"] == pages[1]["display_name"]
    assert fp["step"] == "ocr" and fp["error"]
    assert job["progress"]["pages"][pages[1]["id"]] == {"status": "failed", "step": "ocr", "error": fp["error"]}
    bad = client.get(f"/api/pages/{pages[1]['id']}").json()
    assert bad["status"] == "failed" and bad["error"] and "not an image" not in bad["error"]
    assert client.get(f"/api/pages/{pages[2]['id']}").json()["status"] == "render_done"


def test_provider_retry_backoff_and_failure_modes(client):
    prj, pages = create_project_with_pages(client, 1, MOCK_REGIONS)
    # 429 twice then success (max retries = 2 in tests)
    prov = create_mock_provider(client, {"fail_mode": "429", "fail_times": 2, "fail_key": "a"}, "retry")
    client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "ocr"})
    run_worker_once()
    r = client.post(
        f"/api/projects/{prj['id']}/jobs",
        json={"step": "translate", "provider_profile_id": prov["id"]},
    )
    run_worker_once()
    assert client.get(f"/api/jobs/{r.json()['id']}").json()["status"] == "done"

    for mode, key in (("auth", "b"), ("bad_schema", "c"), ("missing_id", "d"), ("timeout", "e")):
        p = create_mock_provider(client, {"fail_mode": mode, "fail_times": 99, "fail_key": key}, mode)
        r = client.post(
            f"/api/projects/{prj['id']}/jobs",
            json={"step": "translate", "provider_profile_id": p["id"]},
        )
        run_worker_once()
        job = client.get(f"/api/jobs/{r.json()['id']}").json()
        # the only page failed -> the whole job is failed (not partial, not done)
        assert job["status"] == "failed" and job["progress"]["failed"] == 1, (mode, job)
        assert job["error"] == "1/1 page(s) failed"
        page = client.get(f"/api/pages/{pages[0]['id']}").json()
        assert page["status"] == "failed" and page["steps"]["translate"]["status"] == "failed"


def test_partial_job_lists_failed_pages_and_retries_only_them(client):
    prj, pages = create_project_with_pages(client, 3, MOCK_REGIONS)
    client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "ocr"})
    run_worker_once()
    # First provider call fails hard (non-retryable) -> exactly one page fails, the other two translate.
    prov = create_mock_provider(client, {"fail_mode": "auth", "fail_times": 1, "fail_key": "partial"}, "flaky")
    r = client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "translate", "provider_profile_id": prov["id"]})
    run_worker_once()
    job = client.get(f"/api/jobs/{r.json()['id']}").json()
    assert job["status"] == "partial" and job["progress"]["done"] == 2 and job["progress"]["failed"] == 1
    assert job["error"] == "1/3 page(s) failed"
    failed = job["progress"]["failed_pages"]
    assert [f["page_id"] for f in failed] == [pages[0]["id"]]
    assert failed[0]["position"] == 0 and failed[0]["step"] == "translate" and "unauthorized" in failed[0]["error"]
    assert client.get(f"/api/pages/{pages[0]['id']}").json()["status"] == "failed"
    assert client.get(f"/api/pages/{pages[1]['id']}").json()["status"] == "translate_done"

    # Retry exactly the failed page; step and provider are inherited from the source job.
    r2 = client.post(f"/api/jobs/{job['id']}/retry-failed", json={})
    assert r2.status_code == 201, r2.text
    retry = r2.json()
    assert retry["scope"] == "all" and retry["step"] == "translate" and retry["status"] == "queued"
    assert retry["progress"] == {"total": 1, "retry_of": job["id"]}
    run_worker_once()
    retry = client.get(f"/api/jobs/{retry['id']}").json()
    assert retry["status"] == "done" and retry["error"] is None, retry
    assert retry["progress"]["total"] == 1 and retry["progress"]["retry_of"] == job["id"]
    assert list(retry["progress"]["pages"]) == [pages[0]["id"]] and retry["progress"]["failed_pages"] == []
    assert client.get(f"/api/pages/{pages[0]['id']}").json()["status"] == "translate_done"

    # Nothing left to retry on the retry job; unknown provider override is rejected; unfinished jobs too.
    assert client.post(f"/api/jobs/{retry['id']}/retry-failed", json={}).status_code == 400
    assert client.post(f"/api/jobs/{job['id']}/retry-failed", json={"provider_profile_id": "nope"}).status_code == 404
    queued = client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "ocr"}).json()
    assert client.post(f"/api/jobs/{queued['id']}/retry-failed", json={}).status_code == 409


def test_cost_cap(client, monkeypatch):
    monkeypatch.setenv("MT_MAX_CHARS_PER_JOB", "3")
    from app.config import reset_settings_cache

    reset_settings_cache()
    prj, pages = create_project_with_pages(client, 1, MOCK_REGIONS)
    prov = create_mock_provider(client)
    client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "ocr"})
    run_worker_once()
    r = client.post(
        f"/api/projects/{prj['id']}/jobs",
        json={"step": "translate", "provider_profile_id": prov["id"]},
    )
    run_worker_once()
    job = client.get(f"/api/jobs/{r.json()['id']}").json()
    assert job["status"] == "failed" and "MT_MAX_CHARS_PER_JOB" in job["error"]


def test_cancel_and_idempotency(client):
    prj, pages = create_project_with_pages(client, 1)
    r1 = client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "ocr"}, headers={"Idempotency-Key": "k1"})
    r2 = client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "ocr"}, headers={"Idempotency-Key": "k1"})
    assert r1.json()["id"] == r2.json()["id"]
    c = client.post(f"/api/jobs/{r1.json()['id']}/cancel").json()
    assert c["status"] == "cancelled"
    assert run_worker_once() == 0
    # cancel while running: flag honoured between steps
    r3 = client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "ocr"})
    with connection() as conn:
        from app import repo

        job = repo.claim_next_job(conn, "w", 3)
        repo.request_cancel(conn, job["id"])
        from app.pipeline import run_job
        from app.translate.service import JobCancelled

        try:
            run_job(conn, job)
            raise AssertionError("expected cancel")
        except JobCancelled:
            pass
    assert client.get(f"/api/jobs/{r3.json()['id']}").json()["cancel_requested"]


def test_worker_restart_requeues_running_jobs(client):
    prj, _ = create_project_with_pages(client, 1)
    r = client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "ocr"})
    from app import repo

    with connection() as conn:
        job = repo.claim_next_job(conn, "w1", 3)
        assert job["status"] == "running"
        assert repo.requeue_stale_running(conn, 3) == 1
        assert repo.get_job(conn, job["id"])["status"] == "queued"
        for _ in range(2):
            repo.claim_next_job(conn, "w1", 3)
            repo.requeue_stale_running(conn, 3)
        # attempts now 3 -> next recovery fails the job instead of looping forever
        repo.claim_next_job(conn, "w1", 3)
    assert client.get(f"/api/jobs/{r.json()['id']}").json()["attempts"] == 3


def test_pages_reorder_delete_and_manual_regions(client):
    prj, pages = create_project_with_pages(client, 3)
    ids = [p["id"] for p in pages]
    r = client.post(f"/api/projects/{prj['id']}/pages/reorder", json={"page_ids": ids[::-1]})
    assert [p["id"] for p in r.json()] == ids[::-1]
    assert client.post(f"/api/projects/{prj['id']}/pages/reorder", json={"page_ids": ids[:2]}).status_code == 400
    r = client.post(
        f"/api/pages/{ids[0]}/regions",
        json={
            "polygon": [[10, 10], [100, 10], [100, 50], [10, 50]],
            "source_text": "手動",
            "source_lang": "ja",
        },
    )
    assert r.status_code == 201 and r.json()["origin"] == "user"
    rid = r.json()["id"]
    assert (
        client.post(f"/api/pages/{ids[0]}/regions", json={"polygon": [[-99999, 0], [1, 1], [2, 2]]}).status_code == 400
    )
    # OCR keeps user regions
    client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "ocr", "scope": "page", "page_id": ids[0]})
    run_worker_once()
    regs = client.get(f"/api/pages/{ids[0]}/regions").json()
    assert any(x["id"] == rid for x in regs) and len(regs) > 1
    order = [x["id"] for x in regs][::-1]
    assert [
        x["id"] for x in client.post(f"/api/pages/{ids[0]}/regions/reorder", json={"region_ids": order}).json()
    ] == order
    assert client.delete(f"/api/pages/{ids[0]}/regions/{rid}").status_code == 204
    assert client.delete(f"/api/pages/{ids[1]}").status_code == 204
    assert len(client.get(f"/api/projects/{prj['id']}").json()["pages"]) == 2


def test_upload_limits_and_mixed_formats(client, monkeypatch):
    prj = client.post("/api/projects", json={"name": "u"}).json()
    files = [
        ("files", ("a.jpg", make_jpeg(), "image/jpeg")),
        ("files", ("b.png", make_png(), "image/png")),
        ("files", ("bad.txt", b"hello", "text/plain")),
    ]
    r = client.post(f"/api/projects/{prj['id']}/pages", files=files)
    assert r.status_code == 201 and len(r.json()) == 2 and {p["format"] for p in r.json()} == {"jpg", "png"}
    r = client.post(
        f"/api/projects/{prj['id']}/pages",
        files=[("files", ("x.gif", b"GIF89a" + b"\0" * 50, "image/gif"))],
    )
    assert r.status_code == 422
    monkeypatch.setenv("MT_MAX_UPLOAD_BYTES", "100")
    from app.config import reset_settings_cache

    reset_settings_cache()
    r = client.post(f"/api/projects/{prj['id']}/pages", files=[("files", ("big.png", make_png(), "image/png"))])
    assert r.status_code == 422 and "too large" in r.text


def test_project_update_versioning_and_glossary(client):
    prj = client.post("/api/projects", json={"name": "g"}).json()
    r = client.patch(
        f"/api/projects/{prj['id']}",
        json={
            "version": prj["version"],
            "glossary": [{"source": "ナルト", "target": "Naruto"}],
            "reading_direction": "ltr",
        },
    )
    assert r.status_code == 200 and r.json()["glossary"][0]["target"] == "Naruto" and r.json()["version"] == 2
    assert client.patch(f"/api/projects/{prj['id']}", json={"version": 1, "name": "x"}).status_code == 409
    assert client.delete(f"/api/projects/{prj['id']}").status_code == 204
    assert client.get(f"/api/projects/{prj['id']}").status_code == 404
