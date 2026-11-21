"""Per-page processing steps and job execution. One failing page never aborts the others."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any

from PIL import Image

from . import repo, storage
from .config import get_settings
from .logging_utils import safe_error
from .ocr.service import run_page_ocr
from .render.renderer import render_page
from .translate.factory import provider_from_profile
from .translate.service import CostLimitExceeded, JobCancelled, translate_page

log = logging.getLogger(__name__)
STEP_ORDER = ["ocr", "translate", "render"]


def _load_work_image(page: dict[str, Any]) -> Image.Image:
    path = storage.abs_path(page["work_path"])
    with Image.open(path) as im:
        im.load()
        return im.convert("RGB")


def step_ocr(conn: sqlite3.Connection, project: dict[str, Any], page: dict[str, Any], options: dict[str, Any]) -> None:
    image = _load_work_image(page)
    regions, page_guess = run_page_ocr(
        image,
        project["reading_direction"],
        page.get("source_lang_override"),
        force_manga_ocr=bool(options.get("force_manga_ocr")),
    )
    repo.merge_ocr_regions(conn, page["id"], project["id"], regions)
    repo.update_page(
        conn,
        page["id"],
        {
            "detected_lang": page_guess.lang,
            "lang_uncertain": 1 if page_guess.uncertain else 0,
        },
    )


def step_translate(
    conn: sqlite3.Connection,
    project: dict[str, Any],
    page: dict[str, Any],
    options: dict[str, Any],
    job_id: str,
    state: dict[str, Any],
) -> None:
    profile_id = options.get("provider_profile_id")
    if not profile_id:
        raise ValueError("provider_profile_id is required for the translate step")
    provider = state.get("provider")
    if provider is None:
        provider = provider_from_profile(repo.get_provider(conn, profile_id))
        state["provider"] = provider
    translate_page(
        conn,
        project,
        page,
        provider,
        job_id,
        state.setdefault("previous_texts", []),
        only_stale=bool(options.get("only_stale")),
        chars_budget=state.setdefault("chars", {"used": 0}),
    )


def step_render(conn: sqlite3.Connection, project: dict[str, Any], page: dict[str, Any]) -> None:
    """Render the page from its current regions. Writes only the render file, never the original."""
    # Clear the stale flag *before* reading the data: an edit that lands while we render re-marks the
    # page, so the flag can never claim a render is current when it was built from older data.
    repo.update_page(conn, page["id"], {"render_stale": 0})
    try:
        image = _load_work_image(page)
        regions = repo.list_regions(conn, page["id"])
        options = json.loads(project.get("settings_json") or "{}")
        out, report = render_page(image, regions, project.get("target_lang"), options)
        path = storage.page_render_path(project["id"], page["id"])
        tmp = path.with_suffix(".tmp.png")
        try:
            out.save(tmp, format="PNG")
            tmp.replace(path)
        finally:
            if tmp.exists():
                tmp.unlink()
    except Exception:
        repo.update_page(conn, page["id"], {"render_stale": 1})
        raise
    repo.update_page(
        conn,
        page["id"],
        {"render_path": storage.rel_path(path), "render_report_json": json.dumps(report.to_dict())},
    )


def _failed_entry(page: dict[str, Any], pstate: dict[str, Any]) -> dict[str, Any]:
    """One row of ``progress.failed_pages``: enough for the UI to name the page and offer a retry."""
    return {
        "page_id": page["id"],
        "position": page["position"],
        "display_name": page["display_name"],
        "step": pstate.get("step"),
        "error": pstate.get("error"),
    }


def job_result(progress: dict[str, Any]) -> tuple[str, str | None]:
    """Final ``(status, error)`` of a job from its per-page outcomes.

    ``done`` only when every page succeeded; ``partial`` when some pages failed but others succeeded;
    ``failed`` when every page failed. A batch with one failing page is therefore never reported as done.
    """
    total = int(progress.get("total", 0))
    failed = int(progress.get("failed", 0))
    if failed == 0:
        return "done", None
    error = f"{failed}/{total} page(s) failed"
    return ("failed" if failed >= total else "partial"), error


def run_job(conn: sqlite3.Connection, job: dict[str, Any]) -> tuple[str, str | None]:
    """Execute a claimed job. Updates progress; raises only for whole-job failures.

    Returns the final ``(status, error)`` (see :func:`job_result`). ``options.page_ids`` restricts the
    job to a subset of the project's pages (used by ``POST /api/jobs/{id}/retry-failed``).
    """
    s = get_settings()
    options = json.loads(job["options_json"] or "{}")
    project = repo.get_project(conn, job["project_id"])
    pages = repo.list_pages(conn, project["id"])
    if job["scope"] == "page":
        pages = [p for p in pages if p["id"] == job["page_id"]]
        if not pages:
            raise ValueError("page not found for job")
    wanted = options.get("page_ids")
    if wanted:
        keep = {str(pid) for pid in wanted}
        pages = [p for p in pages if p["id"] in keep]  # pages deleted since the retry was queued are skipped
        if not pages:
            raise ValueError("none of the requested pages exist any more")
    steps = STEP_ORDER if job["step"] == "all" else [job["step"]]
    progress: dict[str, Any] = {
        "total": len(pages),
        "done": 0,
        "failed": 0,
        "steps": steps,
        "pages": {},
        "failed_pages": [],
    }
    if options.get("retry_of"):
        progress["retry_of"] = str(options["retry_of"])
    state: dict[str, Any] = {}
    if "translate" in steps and options.get("provider_profile_id"):
        est_regions = 0
        est_chars = 0
        for p in pages:
            for r in repo.list_regions(conn, p["id"]):
                if r["source_text"].strip():
                    est_regions += 1
                    est_chars += len(r["source_text"])
        progress["estimate"] = {"regions": est_regions, "characters": est_chars}
    repo.update_job(conn, job["id"], {"progress_json": json.dumps(progress)})

    def save() -> None:
        repo.update_job(conn, job["id"], {"progress_json": json.dumps(progress)})

    for page in pages:
        if repo.job_cancel_requested(conn, job["id"]):
            raise JobCancelled("cancelled by user")
        pstate = progress["pages"].setdefault(page["id"], {"status": "running", "step": None, "error": None})
        page_failed = False
        for step in steps:
            if repo.job_cancel_requested(conn, job["id"]):
                raise JobCancelled("cancelled by user")
            pstate["step"] = step
            save()
            repo.set_page_step(conn, page["id"], step, "running", page_status=f"{step}_running")
            started = time.monotonic()
            try:
                fresh = repo.get_page(conn, page["id"])
                if step == "ocr":
                    step_ocr(conn, project, fresh, options)
                elif step == "translate":
                    step_translate(conn, project, fresh, options, job["id"], state)
                elif step == "render":
                    step_render(conn, project, fresh)
                else:
                    raise ValueError(f"unknown step {step}")
                elapsed = time.monotonic() - started
                if step == "ocr" and elapsed > s.ocr_page_timeout_seconds:
                    log.warning("ocr for page took %.0fs (limit %ss)", elapsed, s.ocr_page_timeout_seconds)
                repo.set_page_step(conn, page["id"], step, "done", page_status=f"{step}_done")
            except JobCancelled:
                repo.set_page_step(conn, page["id"], step, "cancelled", "cancelled", page_status="cancelled")
                raise
            except CostLimitExceeded as exc:
                repo.set_page_step(conn, page["id"], step, "failed", str(exc), page_status="failed")
                pstate.update(status="failed", error=str(exc))
                progress["failed"] += 1
                progress["failed_pages"].append(_failed_entry(page, pstate))
                save()
                raise
            except Exception as exc:  # noqa: BLE001 - per-page isolation by design
                msg = safe_error(exc)
                log.warning("page %s step %s failed: %s", page["id"], step, msg)
                repo.set_page_step(conn, page["id"], step, "failed", msg, page_status="failed")
                pstate.update(status="failed", error=msg)
                page_failed = True
                break
        if page_failed:
            progress["failed"] += 1
            progress["failed_pages"].append(_failed_entry(page, pstate))
        else:
            pstate["status"] = "done"
            progress["done"] += 1
        save()
    return job_result(progress)
