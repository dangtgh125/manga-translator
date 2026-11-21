"""Smoke/e2e over the Compose network: real api + worker containers, mock OCR/providers, web proxy."""

import io
import os
import time
import zipfile

import httpx
import pytest

from tests.conftest import make_png

BASE = os.environ.get("MT_E2E_BASE_URL")
WEB = os.environ.get("MT_WEB_BASE_URL")
pytestmark = pytest.mark.skipif(not BASE, reason="MT_E2E_BASE_URL not set (runs inside compose.test.yaml)")

REGIONS = [
    {
        "polygon": [[300, 20], [330, 20], [330, 200], [300, 200]],
        "text": "こんにちは",
        "confidence": 0.95,
    },
    {
        "polygon": [[20, 400], [200, 400], [200, 430], [20, 430]],
        "text": "这是简体",
        "confidence": 0.9,
    },
]


@pytest.fixture(scope="module")
def http():
    with httpx.Client(base_url=BASE, timeout=60) as c:
        yield c


def _wait_job(http, job_id, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = http.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.5)
    raise AssertionError("job did not finish")


def test_health_and_worker_alive(http):
    h = http.get("/api/health").json()
    assert h["status"] == "ok" and h["ocr_engine"] == "mock"
    deadline = time.time() + 60
    while time.time() < deadline and not h["worker_alive"]:
        time.sleep(1)
        h = http.get("/api/health").json()
    assert h["worker_alive"] is True


def test_batch_pipeline_through_real_worker(http):
    prj = http.post("/api/projects", json={"name": "e2e", "target_lang": "vi"}).json()
    files = [("files", (f"p{i}.png", make_png(mock_regions=REGIONS), "image/png")) for i in range(3)]
    pages = http.post(f"/api/projects/{prj['id']}/pages", files=files).json()
    assert len(pages) == 3
    prov = http.post(
        "/api/providers",
        json={"name": "mock", "provider": "mock", "model_id": "mock-1", "secret": "k"},
    ).json()
    assert http.post("/api/providers/test", json={"profile_id": prov["id"]}).json()["ok"]
    job = http.post(f"/api/projects/{prj['id']}/jobs", json={"step": "all", "provider_profile_id": prov["id"]}).json()
    job = _wait_job(http, job["id"])
    assert job["status"] == "done" and job["progress"]["done"] == 3, job
    regs = http.get(f"/api/pages/{pages[0]['id']}/regions").json()
    assert len(regs) == 2 and all(r["final_text"].startswith("[vi]") for r in regs)
    # single-page retry of one step
    job2 = http.post(f"/api/pages/{pages[1]['id']}/retry", json={"step": "render"}).json()
    assert _wait_job(http, job2["id"])["status"] == "done"
    z = zipfile.ZipFile(io.BytesIO(http.get(f"/api/projects/{prj['id']}/export").content))
    assert "page_0003.png" in z.namelist()
    # cancel a queued job
    j3 = http.post(f"/api/projects/{prj['id']}/jobs", json={"step": "ocr"}).json()
    c = http.post(f"/api/jobs/{j3['id']}/cancel").json()
    assert c["status"] in ("cancelled", "running")
    assert _wait_job(http, j3["id"])["status"] in ("cancelled", "done")


@pytest.mark.skipif(not WEB, reason="MT_WEB_BASE_URL not set")
def test_web_serves_ui_and_proxies_api():
    with httpx.Client(base_url=WEB, timeout=30) as c:
        r = c.get("/")
        assert r.status_code == 200 and '<div id="root">' in r.text
        assert "content-security-policy" in r.headers
        assert r.headers.get("server", "nginx") == "nginx"  # server_tokens off: no version disclosed
        h = c.get("/api/health")
        assert h.status_code == 200 and h.json()["status"] == "ok"
        assert c.get("/api/projects/nope").status_code == 404
