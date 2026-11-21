"""Shared fixtures. Unit/integration tests run the API in-process with mock OCR + mock providers.

E2E tests (tests/e2e) need MT_E2E_BASE_URL and run against the Compose network.
"""

from __future__ import annotations

import io
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image, PngImagePlugin

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

TEST_KEY = "test-master-key-0123456789abcdef0123456789abcdef0123456789"


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("MT_TEST_MODE", "1")
    monkeypatch.setenv("MT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MT_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("MT_TMP_DIR", str(tmp_path / "tmp"))
    monkeypatch.setenv("MT_WORKER_HEARTBEAT_FILE", str(tmp_path / "hb"))
    monkeypatch.setenv("MT_OCR_ENGINE", "mock")
    monkeypatch.setenv("MT_MOCK_PROVIDERS", "1")
    monkeypatch.setenv("MT_MASTER_KEY", TEST_KEY)
    monkeypatch.delenv("MT_MASTER_KEY_FILE", raising=False)
    monkeypatch.setenv("MT_ENDPOINT_ALLOWLIST", "")
    monkeypatch.setenv("MT_PROVIDER_MAX_RETRIES", "2")
    monkeypatch.setenv("MT_FONT_DIR", os.environ.get("MT_FONT_DIR", "/usr/share/fonts"))
    from app.config import reset_settings_cache
    from app.ocr.service import reset_engine_cache
    from app.security.secrets import reset_secret_cache
    from app.translate.mock import reset_mock_counters

    reset_settings_cache()
    reset_secret_cache()
    reset_engine_cache()
    reset_mock_counters()
    yield
    reset_settings_cache()
    reset_secret_cache()
    reset_engine_cache()


@pytest.fixture
def client():  # type: ignore[no-untyped-def]
    from app.main import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app()) as c:
        yield c


def make_png(
    width: int = 400,
    height: int = 600,
    mock_regions: list[dict] | None = None,
    color=(255, 255, 255),
) -> bytes:
    im = Image.new("RGB", (width, height), color)
    # a dark "bubble" outline so the renderer has a flat interior
    buf = io.BytesIO()
    info = PngImagePlugin.PngInfo()
    if mock_regions is not None:
        info.add_text("mt_mock_ocr", json.dumps(mock_regions))
    im.save(buf, format="PNG", pnginfo=info)
    return buf.getvalue()


def make_jpeg(width: int = 300, height: int = 300) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 200, 200)).save(buf, format="JPEG")
    return buf.getvalue()


def make_webp(width: int = 300, height: int = 300) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (10, 200, 10)).save(buf, format="WEBP")
    return buf.getvalue()


def run_worker_once(max_jobs: int = 10) -> int:
    """Drain the queue synchronously (used by integration tests instead of the worker process)."""
    from app import repo
    from app.config import get_settings
    from app.db import connection, utcnow
    from app.pipeline import run_job
    from app.translate.service import JobCancelled

    done = 0
    with connection() as conn:
        for _ in range(max_jobs):
            job = repo.claim_next_job(conn, "test-worker", get_settings().job_max_attempts)
            if job is None:
                break
            try:
                status, error = run_job(conn, job)  # done | partial | failed, same as the worker
            except JobCancelled:
                status, error = "cancelled", None
            except Exception as exc:  # noqa: BLE001
                status, error = "failed", f"{exc.__class__.__name__}: {exc}"[:300]
            repo.update_job(conn, job["id"], {"status": status, "error": error, "finished_at": utcnow()})
            done += 1
    return done


def create_project_with_pages(client, n: int = 2, mock_regions=None, name="t"):  # type: ignore[no-untyped-def]
    prj = client.post("/api/projects", json={"name": name, "target_lang": "vi"}).json()
    files = [("files", (f"p{i}.png", make_png(mock_regions=mock_regions), "image/png")) for i in range(n)]
    r = client.post(f"/api/projects/{prj['id']}/pages", files=files)
    assert r.status_code == 201, r.text
    return prj, r.json()


def create_mock_provider(client, options=None, name="mock"):  # type: ignore[no-untyped-def]
    r = client.post(
        "/api/providers",
        json={
            "name": name,
            "provider": "mock",
            "model_id": "mock-1",
            "options": options or {},
            "secret": "not-a-real-key",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()
