from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException

from .. import repo, schemas, serializers
from ..deps import get_db

router = APIRouter(tags=["jobs"])
DB = Annotated[sqlite3.Connection, Depends(get_db)]


@router.post("/api/projects/{project_id}/jobs", response_model=schemas.JobOut, status_code=201)
def create_job(
    project_id: str,
    body: schemas.JobCreate,
    conn: DB,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", max_length=schemas.IDEMPOTENCY_KEY_MAX_LENGTH)
    ] = None,
) -> schemas.JobOut:
    repo.get_project(conn, project_id)
    options = dict(body.options)
    if body.provider_profile_id:
        repo.get_provider(conn, body.provider_profile_id)
        options["provider_profile_id"] = body.provider_profile_id
    if body.step in ("translate", "all") and not options.get("provider_profile_id"):
        raise HTTPException(400, "provider_profile_id is required for translation")
    page_id = None
    if body.scope == "page":
        if not body.page_id:
            raise HTTPException(400, "page_id is required when scope=page")
        page_id = repo.get_page(conn, body.page_id, project_id)["id"]
    # Key is opaque; scoped to this project inside repo.create_job (same key + same request -> same
    # job, same key + different request -> 409, same key elsewhere -> unrelated).
    key = body.idempotency_key or idempotency_key or None
    job = repo.create_job(conn, project_id, body.scope, page_id, body.step, options, key)
    return serializers.job_out(job)


@router.get("/api/projects/{project_id}/jobs", response_model=list[schemas.JobOut])
def list_jobs(project_id: str, conn: DB) -> list[schemas.JobOut]:
    repo.get_project(conn, project_id)
    return [serializers.job_out(j) for j in repo.list_jobs(conn, project_id)]


@router.get("/api/jobs/{job_id}", response_model=schemas.JobOut)
def get_job(job_id: str, conn: DB) -> schemas.JobOut:
    return serializers.job_out(repo.get_job(conn, job_id))


@router.post("/api/jobs/{job_id}/cancel", response_model=schemas.JobOut)
def cancel_job(job_id: str, conn: DB) -> schemas.JobOut:
    return serializers.job_out(repo.request_cancel(conn, job_id))


@router.post("/api/jobs/{job_id}/retry-failed", response_model=schemas.JobOut, status_code=201)
def retry_failed_pages(
    job_id: str,
    body: schemas.RetryFailedBody,
    conn: DB,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> schemas.JobOut:
    """Enqueue a job that re-runs only the pages that failed in `job_id` (PLAN.md §10 P2.1).

    The new job keeps the source job's step and options (provider included) unless overridden, and
    carries `page_ids` + `retry_of` in its options; `pipeline.run_job` restricts itself to those pages.
    """
    import json  # local import keeps this addition independent of the module header

    src = repo.get_job(conn, job_id)
    if src["status"] in ("queued", "running"):
        raise HTTPException(409, "job has not finished yet")
    progress = json.loads(src["progress_json"] or "{}")
    failed_ids = [str(fp["page_id"]) for fp in progress.get("failed_pages", [])] or [
        pid for pid, st in progress.get("pages", {}).items() if st.get("status") == "failed"
    ]
    existing = {p["id"] for p in repo.list_pages(conn, src["project_id"])}
    page_ids = [pid for pid in failed_ids if pid in existing]
    if not page_ids:
        raise HTTPException(400, "job has no failed pages to retry")
    src_options = json.loads(src["options_json"] or "{}")
    options = {k: v for k, v in src_options.items() if k not in ("page_ids", "retry_of")}
    options.update(body.options)
    if body.provider_profile_id:
        repo.get_provider(conn, body.provider_profile_id)
        options["provider_profile_id"] = body.provider_profile_id
    step = body.step or src["step"]
    if step in ("translate", "all") and not options.get("provider_profile_id"):
        raise HTTPException(400, "provider_profile_id is required for translation")
    options["page_ids"] = page_ids
    options["retry_of"] = src["id"]
    key = body.idempotency_key or idempotency_key
    job = repo.create_job(conn, src["project_id"], "all", None, step, options, key)
    if job["status"] == "queued" and not json.loads(job["progress_json"] or "{}"):
        # Let the UI show the scope ("N trang lỗi") before the worker picks the job up.
        repo.update_job(conn, job["id"], {"progress_json": json.dumps({"total": len(page_ids), "retry_of": src["id"]})})
        job = repo.get_job(conn, job["id"])
    return serializers.job_out(job)
