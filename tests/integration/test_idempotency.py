"""Idempotency keys are scoped per project and bound to the request they first created (PLAN.md §10 P2.3)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tests.conftest import create_mock_provider, create_project_with_pages


def _jobs(client, project_id: str) -> list[dict]:  # type: ignore[no-untyped-def]
    r = client.get(f"/api/projects/{project_id}/jobs")
    assert r.status_code == 200, r.text
    return r.json()


def test_same_key_in_two_projects_creates_two_isolated_jobs(client):
    prj_a, _ = create_project_with_pages(client, 1, name="a")
    prj_b, _ = create_project_with_pages(client, 1, name="b")
    ra = client.post(f"/api/projects/{prj_a['id']}/jobs", json={"step": "ocr"}, headers={"Idempotency-Key": "shared"})
    rb = client.post(f"/api/projects/{prj_b['id']}/jobs", json={"step": "ocr"}, headers={"Idempotency-Key": "shared"})
    assert ra.status_code == 201 and rb.status_code == 201, (ra.text, rb.text)
    assert ra.json()["id"] != rb.json()["id"]
    assert ra.json()["project_id"] == prj_a["id"] and rb.json()["project_id"] == prj_b["id"]
    # Even a different request shape under the same key in another project is not a conflict.
    rb2 = client.post(
        f"/api/projects/{prj_b['id']}/jobs", json={"step": "render"}, headers={"Idempotency-Key": "other-shape"}
    )
    ra2 = client.post(
        f"/api/projects/{prj_a['id']}/jobs", json={"step": "ocr"}, headers={"Idempotency-Key": "other-shape"}
    )
    assert rb2.status_code == 201 and ra2.status_code == 201, (rb2.text, ra2.text)
    assert ra2.json()["id"] != rb2.json()["id"]
    # No leakage: each project's job list contains only its own jobs.
    assert {j["id"] for j in _jobs(client, prj_a["id"])} == {ra.json()["id"], ra2.json()["id"]}
    assert {j["id"] for j in _jobs(client, prj_b["id"])} == {rb.json()["id"], rb2.json()["id"]}


def test_same_key_same_request_replays_without_duplicate_rows(client):
    prj, _ = create_project_with_pages(client, 1)
    body = {"step": "ocr", "options": {"a": 1, "b": [1, 2]}}
    r1 = client.post(f"/api/projects/{prj['id']}/jobs", json=body, headers={"Idempotency-Key": "k1"})
    assert r1.status_code == 201, r1.text
    # key order inside options must not matter; key may also arrive in the body instead of the header
    r2 = client.post(
        f"/api/projects/{prj['id']}/jobs",
        json={"options": {"b": [1, 2], "a": 1}, "step": "ocr"},
        headers={"Idempotency-Key": "k1"},
    )
    r3 = client.post(f"/api/projects/{prj['id']}/jobs", json={**body, "idempotency_key": "k1"})
    assert r2.status_code == 201 and r3.status_code == 201, (r2.text, r3.text)
    assert r1.json()["id"] == r2.json()["id"] == r3.json()["id"]
    assert len(_jobs(client, prj["id"])) == 1


def test_same_key_different_request_conflicts(client):
    prj, pages = create_project_with_pages(client, 1)
    prov = create_mock_provider(client)
    r1 = client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "ocr"}, headers={"Idempotency-Key": "k1"})
    assert r1.status_code == 201, r1.text
    # different step
    r = client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "render"}, headers={"Idempotency-Key": "k1"})
    assert r.status_code == 409, r.text
    assert "Idempotency-Key" in r.json()["detail"]
    # different options
    r = client.post(
        f"/api/projects/{prj['id']}/jobs", json={"step": "ocr", "options": {"x": 1}}, headers={"Idempotency-Key": "k1"}
    )
    assert r.status_code == 409, r.text
    # different scope / page
    r = client.post(
        f"/api/projects/{prj['id']}/jobs",
        json={"step": "ocr", "scope": "page", "page_id": pages[0]["id"]},
        headers={"Idempotency-Key": "k1"},
    )
    assert r.status_code == 409, r.text
    # different provider profile (merged into options)
    r = client.post(
        f"/api/projects/{prj['id']}/jobs",
        json={"step": "ocr", "provider_profile_id": prov["id"]},
        headers={"Idempotency-Key": "k1"},
    )
    assert r.status_code == 409, r.text
    # the conflicting attempts created nothing
    assert [j["id"] for j in _jobs(client, prj["id"])] == [r1.json()["id"]]


def test_retry_page_key_is_scoped_and_bound_to_request(client):
    prj, pages = create_project_with_pages(client, 1)
    other, other_pages = create_project_with_pages(client, 1, name="other")
    pid, opid = pages[0]["id"], other_pages[0]["id"]
    r1 = client.post(f"/api/pages/{pid}/retry", json={"step": "ocr", "idempotency_key": "rk"})
    r2 = client.post(f"/api/pages/{pid}/retry", json={"step": "ocr", "idempotency_key": "rk"})
    assert r1.status_code == 201 and r2.status_code == 201, (r1.text, r2.text)
    assert r1.json()["id"] == r2.json()["id"]
    assert len(_jobs(client, prj["id"])) == 1
    r = client.post(f"/api/pages/{pid}/retry", json={"step": "render", "idempotency_key": "rk"})
    assert r.status_code == 409, r.text
    ro = client.post(f"/api/pages/{opid}/retry", json={"step": "ocr", "idempotency_key": "rk"})
    assert ro.status_code == 201 and ro.json()["id"] != r1.json()["id"]
    assert ro.json()["project_id"] == other["id"]


def test_key_is_opaque_and_length_limited(client):
    prj, _ = create_project_with_pages(client, 1)
    weird = "  Key/With:odd chars=+;  "
    r1 = client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "ocr"}, headers={"Idempotency-Key": weird})
    r2 = client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "ocr"}, headers={"Idempotency-Key": weird})
    assert r1.status_code == 201 and r1.json()["id"] == r2.json()["id"]
    # case-sensitive, not normalised
    r3 = client.post(
        f"/api/projects/{prj['id']}/jobs", json={"step": "ocr"}, headers={"Idempotency-Key": weird.lower()}
    )
    assert r3.status_code == 201 and r3.json()["id"] != r1.json()["id"]
    # empty key behaves as no key: every call makes a new job
    e1 = client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "ocr"}, headers={"Idempotency-Key": ""})
    e2 = client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "ocr"}, headers={"Idempotency-Key": ""})
    assert e1.status_code == 201 and e2.status_code == 201 and e1.json()["id"] != e2.json()["id"]
    too_long = "x" * 129
    r = client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "ocr"}, headers={"Idempotency-Key": too_long})
    assert r.status_code == 422, r.text
    r = client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "ocr", "idempotency_key": too_long})
    assert r.status_code == 422, r.text
    r = client.post(f"/api/projects/{prj['id']}/jobs", json={"step": "ocr"}, headers={"Idempotency-Key": "x" * 128})
    assert r.status_code == 201, r.text


def test_migration_0003_keeps_legacy_jobs_and_relaxes_global_unique(tmp_path: Path):
    """A DB created by 0001+0002 (global UNIQUE on idempotency_key) is rebuilt without losing rows."""
    from app import db

    path = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);\n"
        + (db.MIGRATIONS_DIR / "0001_init.sql").read_text()
        + (db.MIGRATIONS_DIR / "0002_stale_tracking.sql").read_text()
        + "INSERT INTO schema_migrations VALUES (1,'t'),(2,'t');\n"
        "INSERT INTO projects(id,name,target_lang,reading_direction,glossary_json,version,created_at,updated_at) "
        "VALUES ('p1','a','vi','rtl','[]',1,'t','t'),('p2','b','vi','rtl','[]',1,'t','t');\n"
        "INSERT INTO jobs(id,project_id,scope,step,status,idempotency_key,attempts,created_at,updated_at) "
        "VALUES ('job_old','p1','all','ocr','done','legacy',1,'t','t');\n"
    )
    conn.close()

    db.migrate(path)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        assert {r[0] for r in conn.execute("SELECT version FROM schema_migrations")} >= {1, 2, 3}
        old = conn.execute("SELECT * FROM jobs WHERE id='job_old'").fetchone()
        assert old is not None and old["idempotency_key"] == "legacy" and old["status"] == "done"
        assert old["request_fingerprint"] is None
        # same key is now allowed in another project ...
        conn.execute(
            "INSERT INTO jobs(id,project_id,scope,step,idempotency_key,created_at,updated_at) "
            "VALUES ('job_p2','p2','all','ocr','legacy','t','t')"
        )
        # ... but still unique within one project
        try:
            conn.execute(
                "INSERT INTO jobs(id,project_id,scope,step,idempotency_key,created_at,updated_at) "
                "VALUES ('job_dup','p1','all','ocr','legacy','t','t')"
            )
            raise AssertionError("expected UNIQUE violation")
        except sqlite3.IntegrityError:
            pass
        # NULL keys never collide
        conn.execute(
            "INSERT INTO jobs(id,project_id,scope,step,created_at,updated_at) VALUES ('n1','p1','all','ocr','t','t')"
        )
        conn.execute(
            "INSERT INTO jobs(id,project_id,scope,step,created_at,updated_at) VALUES ('n2','p1','all','ocr','t','t')"
        )
    finally:
        conn.close()
