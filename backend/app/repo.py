"""Data access layer. All writes use optimistic `version` checks where the API exposes a version."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from . import region_merge
from .db import transaction, utcnow
from .ids import new_id


class NotFound(LookupError):
    pass


class VersionConflict(RuntimeError):
    pass


class IdempotencyConflict(VersionConflict):
    """Same Idempotency-Key reused within a project for a different request (maps to 409)."""


class LimitExceeded(ValueError):
    pass


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(r) if r is not None else None


# ---------------------------------------------------------------- projects
def create_project(
    conn: sqlite3.Connection,
    name: str,
    target_lang: str,
    reading_direction: str,
    glossary: list[dict[str, Any]],
) -> dict[str, Any]:
    pid = new_id("project")
    now = utcnow()
    with transaction(conn):
        conn.execute(
            "INSERT INTO projects(id,name,target_lang,reading_direction,glossary_json,settings_json,"
            "version,created_at,updated_at) VALUES (?,?,?,?,?,?,1,?,?)",
            (pid, name, target_lang, reading_direction, json.dumps(glossary), "{}", now, now),
        )
    return get_project(conn, pid)


def get_project(conn: sqlite3.Connection, project_id: str) -> dict[str, Any]:
    r = _row(conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone())
    if r is None:
        raise NotFound("project not found")
    return r


def list_projects(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT p.*, (SELECT COUNT(*) FROM pages WHERE project_id=p.id) AS page_count "
        "FROM projects p ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def update_project(conn: sqlite3.Connection, project_id: str, version: int, fields: dict[str, Any]) -> dict[str, Any]:
    if not fields:
        return get_project(conn, project_id)
    sets = ", ".join(f"{k}=?" for k in fields)
    with transaction(conn):
        cur = conn.execute(
            f"UPDATE projects SET {sets}, version=version+1, updated_at=? "  # noqa: S608
            "WHERE id=? AND version=?",
            (*fields.values(), utcnow(), project_id, version),
        )
        if cur.rowcount == 0:
            get_project(conn, project_id)
            raise VersionConflict("project was modified by someone else; reload and retry")
    return get_project(conn, project_id)


def delete_project(conn: sqlite3.Connection, project_id: str) -> None:
    with transaction(conn):
        cur = conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
        if cur.rowcount == 0:
            raise NotFound("project not found")


# ---------------------------------------------------------------- pages
def count_pages(conn: sqlite3.Connection, project_id: str) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM pages WHERE project_id=?", (project_id,)).fetchone()[0])


def add_page(
    conn: sqlite3.Connection,
    project_id: str,
    page_id: str,
    display_name: str,
    original_path: str,
    work_path: str,
    width: int,
    height: int,
    fmt: str,
    max_pages: int,
) -> dict[str, Any]:
    now = utcnow()
    with transaction(conn):
        n = count_pages(conn, project_id)
        if n >= max_pages:
            raise LimitExceeded("project page limit reached")
        conn.execute(
            "INSERT INTO pages(id,project_id,position,display_name,original_path,work_path,width,"
            "height,format,status,steps_json,version,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,'uploaded','{}',1,?,?)",
            (
                page_id,
                project_id,
                n,
                display_name,
                original_path,
                work_path,
                width,
                height,
                fmt,
                now,
                now,
            ),
        )
    return get_page(conn, page_id)


# Pages are read with the number of regions whose translation is outdated, so the API and the export
# can tell "render is current" from "the translation it draws is current".
_PAGE_SELECT = (
    "SELECT p.*, (SELECT COUNT(*) FROM regions r WHERE r.page_id=p.id AND r.translation_stale=1) "
    "AS translation_stale_count FROM pages p"
)


def get_page(conn: sqlite3.Connection, page_id: str, project_id: str | None = None) -> dict[str, Any]:
    r = _row(conn.execute(f"{_PAGE_SELECT} WHERE p.id=?", (page_id,)).fetchone())
    if r is None or (project_id is not None and r["project_id"] != project_id):
        raise NotFound("page not found")
    return r


def list_pages(conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            f"{_PAGE_SELECT} WHERE p.project_id=? ORDER BY p.position, p.created_at", (project_id,)
        ).fetchall()
    ]


def reorder_pages(conn: sqlite3.Connection, project_id: str, page_ids: list[str]) -> None:
    existing = [p["id"] for p in list_pages(conn, project_id)]
    if sorted(existing) != sorted(page_ids) or len(set(page_ids)) != len(page_ids):
        raise ValueError("page_ids must contain every page of the project exactly once")
    with transaction(conn):
        for pos, pid in enumerate(page_ids):
            conn.execute(
                "UPDATE pages SET position=?, updated_at=? WHERE id=? AND project_id=?",
                (pos, utcnow(), pid, project_id),
            )


def update_page(
    conn: sqlite3.Connection, page_id: str, fields: dict[str, Any], version: int | None = None
) -> dict[str, Any]:
    sets = ", ".join(f"{k}=?" for k in fields)
    with transaction(conn):
        if version is None:
            conn.execute(
                f"UPDATE pages SET {sets}, version=version+1, updated_at=? WHERE id=?",  # noqa: S608
                (*fields.values(), utcnow(), page_id),
            )
        else:
            cur = conn.execute(
                f"UPDATE pages SET {sets}, version=version+1, updated_at=? WHERE id=? AND version=?",  # noqa: S608
                (*fields.values(), utcnow(), page_id, version),
            )
            if cur.rowcount == 0:
                get_page(conn, page_id)
                raise VersionConflict("page was modified; reload and retry")
    return get_page(conn, page_id)


def set_page_step(
    conn: sqlite3.Connection,
    page_id: str,
    step: str,
    status: str,
    message: str | None = None,
    page_status: str | None = None,
) -> None:
    page = get_page(conn, page_id)
    steps = json.loads(page["steps_json"] or "{}")
    steps[step] = {"status": status, "message": message, "at": utcnow()}
    fields: dict[str, Any] = {"steps_json": json.dumps(steps)}
    if page_status:
        fields["status"] = page_status
    if status == "failed":
        fields["error"] = message
    elif status == "done":
        fields["error"] = None
    update_page(conn, page_id, fields)


def delete_page(conn: sqlite3.Connection, page_id: str, project_id: str) -> dict[str, Any]:
    page = get_page(conn, page_id, project_id)
    with transaction(conn):
        conn.execute("DELETE FROM pages WHERE id=?", (page_id,))
        for pos, r in enumerate(
            conn.execute("SELECT id FROM pages WHERE project_id=? ORDER BY position", (project_id,)).fetchall()
        ):
            conn.execute("UPDATE pages SET position=? WHERE id=?", (pos, r["id"]))
    return page


# ---------------------------------------------------------------- regions
def list_regions(conn: sqlite3.Connection, page_id: str) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM regions WHERE page_id=? ORDER BY reading_order, created_at", (page_id,)
        ).fetchall()
    ]


def get_region(conn: sqlite3.Connection, region_id: str, page_id: str) -> dict[str, Any]:
    r = _row(conn.execute("SELECT * FROM regions WHERE id=? AND page_id=?", (region_id, page_id)).fetchone())
    if r is None:
        raise NotFound("region not found")
    return r


def _set_region_fields(conn: sqlite3.Connection, region_id: str, fields: dict[str, Any], now: str) -> None:
    """UPDATE inside an already open transaction (column names come from code, values are bound)."""
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(
        f"UPDATE regions SET {sets}, version=version+1, updated_at=? WHERE id=?",  # noqa: S608
        (*fields.values(), now, region_id),
    )


def _mark_render_stale(conn: sqlite3.Connection, page_id: str, now: str | None = None) -> None:
    """Inside an open transaction: the page's rendered image no longer matches its data."""
    conn.execute(
        "UPDATE pages SET render_stale=1, updated_at=? WHERE id=? AND render_stale=0",
        (now or utcnow(), page_id),
    )


def mark_render_stale(conn: sqlite3.Connection, page_id: str) -> None:
    with transaction(conn):
        _mark_render_stale(conn, page_id)


def mark_project_translations_stale(conn: sqlite3.Connection, project_id: str) -> int:
    """Target language or glossary changed: every existing translation in the project is outdated.

    A hand-written ``final_text`` without a machine translation is a translation too (the renderer
    draws it), so it is outdated as well.

    The rendered image of every page that draws a translation is outdated with it (same transaction),
    so ``has_render`` never claims a render built from the old language/glossary is current.
    """
    now = utcnow()
    with transaction(conn):
        cur = conn.execute(
            "UPDATE regions SET translation_stale=1, version=version+1, updated_at=? "
            "WHERE project_id=? AND (translated_text<>'' OR final_text<>'') AND translation_stale=0",
            (now, project_id),
        )
        conn.execute(
            "UPDATE pages SET render_stale=1, updated_at=? WHERE project_id=? AND render_stale=0 "
            "AND EXISTS (SELECT 1 FROM regions r WHERE r.page_id=pages.id "
            "AND (r.translated_text<>'' OR r.final_text<>''))",
            (now, project_id),
        )
    return int(cur.rowcount)


def apply_page_source_lang(conn: sqlite3.Connection, page_id: str, lang: str, certain: bool) -> int:
    """A page-level source language applies to every region; translations made for another language
    are outdated, and so is the page render that draws them. Returns the number of regions marked stale."""
    now = utcnow()
    stale = 0
    with transaction(conn):
        for r in list_regions(conn, page_id):
            fields: dict[str, Any] = {"source_lang": lang, "lang_uncertain": 0 if certain else 1}
            has_translation = bool(r["translated_text"] or r["final_text"])
            if has_translation and r["source_lang"] != lang and not r["translation_stale"]:
                fields["translation_stale"] = 1
                stale += 1
            _set_region_fields(conn, r["id"], fields, now)
        if stale:
            _mark_render_stale(conn, page_id, now)
    return stale


def translation_context_fingerprint(project: dict[str, Any], page: dict[str, Any]) -> str:
    """Canonical hash of everything besides the region text that shapes a translation result.

    Captured when a batch is built and compared again inside :func:`apply_translation`, so a result
    produced under an older target language, glossary or page source language is never stored as current.
    """
    canonical = json.dumps(
        {
            "target_lang": project["target_lang"],
            "glossary": json.loads(project.get("glossary_json") or "[]"),
            "source_lang_override": page.get("source_lang_override"),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def merge_ocr_regions(
    conn: sqlite3.Connection,
    page_id: str,
    project_id: str,
    regions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge fresh OCR output into the page's regions (PLAN.md §10 P1: edits survive an OCR re-run).

    Matched regions keep their ID, edited source/translation, review status, reading order, mask and
    text box, and font size; OCR-derived fields are refreshed. New detections get new IDs. Auto regions
    no longer detected are deleted only when untouched; edited ones stay and are flagged ``ocr_missing``.
    """
    now = utcnow()
    with transaction(conn):
        existing = list_regions(conn, page_id)
        plan = region_merge.plan_merge(existing, regions)
        for row, reg in plan.update:
            ocr_text = reg.get("ocr_text", "")
            fields: dict[str, Any] = {
                "ocr_text": ocr_text,
                "ocr_confidence": reg.get("ocr_confidence", 0.0),
                "ocr_engine": reg.get("ocr_engine", ""),
                "lines_json": json.dumps(reg.get("lines", [])),
            }
            if row["origin"] == "auto":
                # A polygon/direction/language set by hand (user-drawn region or edited field) is the
                # user's call; the remaining auto fields follow OCR.
                if not row.get("polygon_edited"):
                    fields["polygon_json"] = json.dumps(reg["polygon"])
                if not row.get("direction_edited"):
                    fields["direction"] = reg.get("direction", "unknown")
                if not row.get("source_lang_edited"):
                    fields.update(
                        source_lang=reg.get("source_lang", "unknown"),
                        lang_confidence=reg.get("lang_confidence", 0.0),
                        lang_uncertain=1 if reg.get("lang_uncertain", True) else 0,
                    )
            if not row["source_edited"] or not row["source_text"].strip():
                if ocr_text != row["source_text"]:
                    fields["source_text"] = ocr_text
                    fields["translation_stale"] = 1
            flags = region_merge.load_flags(row["translation_flags_json"])
            if region_merge.OCR_MISSING_FLAG in flags:
                fields["translation_flags_json"] = json.dumps(
                    region_merge.without_flag(flags, region_merge.OCR_MISSING_FLAG)
                )
            _set_region_fields(conn, row["id"], fields, now)
        for row in plan.keep_missing:
            flags = region_merge.load_flags(row["translation_flags_json"])
            if region_merge.OCR_MISSING_FLAG not in flags:
                flagged = region_merge.with_flag(flags, region_merge.OCR_MISSING_FLAG)
                _set_region_fields(conn, row["id"], {"translation_flags_json": json.dumps(flagged)}, now)
        for row in plan.delete:
            conn.execute("DELETE FROM regions WHERE id=?", (row["id"],))
        mx = conn.execute("SELECT COALESCE(MAX(reading_order),-1) FROM regions WHERE page_id=?", (page_id,)).fetchone()
        next_order = int(mx[0]) + 1
        for _, reg in sorted(enumerate(plan.insert), key=lambda t: (t[1].get("reading_order", t[0]), t[0])):
            rid = new_id("region")
            conn.execute(
                "INSERT INTO regions(id,page_id,project_id,polygon_json,direction,reading_order,source_lang,"
                "lang_confidence,lang_uncertain,ocr_text,ocr_confidence,ocr_engine,source_text,lines_json,"
                "origin,version,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'auto',1,?,?)",
                (
                    rid,
                    page_id,
                    project_id,
                    json.dumps(reg["polygon"]),
                    reg.get("direction", "unknown"),
                    next_order,
                    reg.get("source_lang", "unknown"),
                    reg.get("lang_confidence", 0.0),
                    1 if reg.get("lang_uncertain", True) else 0,
                    reg.get("ocr_text", ""),
                    reg.get("ocr_confidence", 0.0),
                    reg.get("ocr_engine", ""),
                    reg.get("ocr_text", ""),
                    json.dumps(reg.get("lines", [])),
                    now,
                    now,
                ),
            )
            next_order += 1
        _mark_render_stale(conn, page_id, now)
    return list_regions(conn, page_id)


def apply_translation(
    conn: sqlite3.Connection,
    region_id: str,
    expected_version: int,
    sent_text: str,
    translated_text: str,
    provider_label: str,
    flags: list[str],
    context_fingerprint: str | None = None,
    sent_lang: str | None = None,
    target_lang: str | None = None,
) -> dict[str, Any] | None:
    """Store a provider result without clobbering edits made while the job was running.

    The row is re-read inside the write transaction. If its version moved since the job read it,
    the user's ``final_text``/``final_edited`` win, ``translated_text`` is still stored, the
    translation is current only if the source text (and language) sent to the provider is still the
    region's, and the region is flagged ``user_edited_during_job``.

    ``context_fingerprint`` (see :func:`translation_context_fingerprint`) is what the batch was built
    under; if the project/page context differs now, the result is stored but stays ``translation_stale``
    and the region is flagged ``context_changed_during_job``.

    A hand-edited ``final_text`` is kept, but it only counts as the current translation while
    ``final_lang`` equals the project's target language; otherwise the region stays stale and is flagged
    ``final_text_lang_mismatch`` so the user decides (re-edit, or take the machine translation).
    ``target_lang`` is the language the batch was translated into (recorded as ``final_lang`` when the
    machine translation becomes the final text). Returns None if the region was deleted.
    """
    with transaction(conn):
        row = _row(conn.execute("SELECT * FROM regions WHERE id=?", (region_id,)).fetchone())
        if row is None:
            return None
        fresh = list(flags)
        fields: dict[str, Any] = {"translated_text": translated_text, "translation_provider": provider_label}
        if row["version"] == expected_version:
            fields["translation_stale"] = 0
        else:
            fresh.append(region_merge.USER_EDITED_DURING_JOB_FLAG)
            same_lang = sent_lang is None or row["source_lang"] == sent_lang
            fields["translation_stale"] = 0 if row["source_text"] == sent_text and same_lang else 1
        project = _row(conn.execute("SELECT * FROM projects WHERE id=?", (row["project_id"],)).fetchone())
        if context_fingerprint is not None:
            page = _row(conn.execute("SELECT * FROM pages WHERE id=?", (row["page_id"],)).fetchone())
            if project is None or page is None or translation_context_fingerprint(project, page) != context_fingerprint:
                # Never report a result produced under an outdated context as current.
                fresh.append(region_merge.CONTEXT_CHANGED_DURING_JOB_FLAG)
                fields["translation_stale"] = 1
        current_target = project["target_lang"] if project else target_lang
        if row["final_edited"] and row["final_text"]:
            if row.get("final_lang") and current_target and row["final_lang"] != current_target:
                # The hand-written text is in another language than the project now targets: it must
                # not be drawn as the translation, and the region needs the user's decision.
                fresh.append(region_merge.FINAL_LANG_MISMATCH_FLAG)
                fields["translation_stale"] = 1
        else:
            fields["final_text"] = translated_text
            fields["final_lang"] = target_lang or current_target or ""
        fields["translation_flags_json"] = json.dumps(
            region_merge.merge_flags(region_merge.load_flags(row["translation_flags_json"]), fresh)
        )
        now = utcnow()
        _set_region_fields(conn, region_id, fields, now)
        _mark_render_stale(conn, row["page_id"], now)
        return _row(conn.execute("SELECT * FROM regions WHERE id=?", (region_id,)).fetchone())


def create_region(
    conn: sqlite3.Connection,
    page_id: str,
    project_id: str,
    polygon: list[Any],
    direction: str,
    source_lang: str,
    source_text: str,
    reading_order: int | None,
) -> dict[str, Any]:
    rid = new_id("region")
    now = utcnow()
    with transaction(conn):
        if reading_order is None:
            mx = conn.execute(
                "SELECT COALESCE(MAX(reading_order),-1) FROM regions WHERE page_id=?", (page_id,)
            ).fetchone()[0]
            reading_order = int(mx) + 1
        conn.execute(
            "INSERT INTO regions(id,page_id,project_id,polygon_json,direction,reading_order,source_lang,"
            "lang_confidence,lang_uncertain,ocr_text,source_text,source_edited,origin,version,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,1.0,0,'',?,1,'user',1,?,?)",
            (
                rid,
                page_id,
                project_id,
                json.dumps(polygon),
                direction,
                reading_order,
                source_lang,
                source_text,
                now,
                now,
            ),
        )
        _mark_render_stale(conn, page_id, now)
    return get_region(conn, rid, page_id)


def update_region(
    conn: sqlite3.Connection,
    region_id: str,
    page_id: str,
    version: int,
    fields: dict[str, Any],
    render_stale: bool = False,
) -> dict[str, Any]:
    if not fields:
        return get_region(conn, region_id, page_id)
    sets = ", ".join(f"{k}=?" for k in fields)
    with transaction(conn):
        now = utcnow()
        cur = conn.execute(
            f"UPDATE regions SET {sets}, version=version+1, updated_at=? WHERE id=? AND page_id=? AND version=?",  # noqa: S608
            (*fields.values(), now, region_id, page_id, version),
        )
        if cur.rowcount == 0:
            get_region(conn, region_id, page_id)
            raise VersionConflict("region was modified; reload and retry")
        if render_stale:
            _mark_render_stale(conn, page_id, now)
    return get_region(conn, region_id, page_id)


def update_region_unchecked(conn: sqlite3.Connection, region_id: str, fields: dict[str, Any]) -> None:
    sets = ", ".join(f"{k}=?" for k in fields)
    with transaction(conn):
        conn.execute(
            f"UPDATE regions SET {sets}, version=version+1, updated_at=? WHERE id=?",  # noqa: S608
            (*fields.values(), utcnow(), region_id),
        )


def delete_region(conn: sqlite3.Connection, region_id: str, page_id: str) -> None:
    with transaction(conn):
        cur = conn.execute("DELETE FROM regions WHERE id=? AND page_id=?", (region_id, page_id))
        if cur.rowcount == 0:
            raise NotFound("region not found")
        _mark_render_stale(conn, page_id)


def reorder_regions(conn: sqlite3.Connection, page_id: str, region_ids: list[str]) -> None:
    rows = list_regions(conn, page_id)
    existing = [r["id"] for r in rows]
    if sorted(existing) != sorted(region_ids) or len(set(region_ids)) != len(region_ids):
        raise ValueError("region_ids must contain every region of the page exactly once")
    old_pos = {r["id"]: r["reading_order"] for r in rows}
    with transaction(conn):
        now = utcnow()
        for pos, rid in enumerate(region_ids):
            # Only regions whose position actually moved count as "reading order edited by hand".
            moved = 1 if old_pos[rid] != pos else 0
            conn.execute(
                "UPDATE regions SET reading_order=?, reading_order_edited=MAX(reading_order_edited, ?), updated_at=? "
                "WHERE id=? AND page_id=?",
                (pos, moved, now, rid, page_id),
            )
        _mark_render_stale(conn, page_id, now)


# ---------------------------------------------------------------- jobs
def create_job(
    conn: sqlite3.Connection,
    project_id: str,
    scope: str,
    page_id: str | None,
    step: str,
    options: dict[str, Any],
    idempotency_key: str | None,
) -> dict[str, Any]:
    """Enqueue a job. `idempotency_key` is opaque and scoped to `project_id`: a replay with the same
    key and the same request (scope/page/step/options) returns the existing job; the same key with a
    different request raises IdempotencyConflict. Keys never match jobs of other projects."""
    key = idempotency_key or None
    fingerprint = request_fingerprint(scope, page_id, step, options) if key else None
    jid = new_id("job")
    now = utcnow()
    # Lookup and insert share one IMMEDIATE transaction so concurrent replays cannot both insert;
    # the partial unique index on (project_id, idempotency_key) is the backstop.
    with transaction(conn):
        if key:
            existing = _row(
                conn.execute(
                    "SELECT * FROM jobs WHERE project_id=? AND idempotency_key=?", (project_id, key)
                ).fetchone()
            )
            if existing:
                if existing.get("request_fingerprint") != fingerprint:
                    raise IdempotencyConflict(
                        "Idempotency-Key was already used in this project for a different request"
                    )
                return existing
        conn.execute(
            "INSERT INTO jobs(id,project_id,scope,page_id,step,options_json,status,progress_json,"
            "idempotency_key,request_fingerprint,attempts,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,'queued','{}',?,?,0,?,?)",
            (jid, project_id, scope, page_id, step, json.dumps(options), key, fingerprint, now, now),
        )
    return get_job(conn, jid)


def request_fingerprint(scope: str, page_id: str | None, step: str, options: dict[str, Any]) -> str:
    """Canonical hash of what a job request asks for, used to detect idempotency-key reuse."""
    canonical = json.dumps(
        {"scope": scope, "page_id": page_id, "step": step, "options": options},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_job(conn: sqlite3.Connection, job_id: str) -> dict[str, Any]:
    r = _row(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
    if r is None:
        raise NotFound("job not found")
    return r


def list_jobs(conn: sqlite3.Connection, project_id: str, limit: int = 50) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM jobs WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
    ]


def request_cancel(conn: sqlite3.Connection, job_id: str) -> dict[str, Any]:
    with transaction(conn):
        job = get_job(conn, job_id)
        if job["status"] == "queued":
            conn.execute(
                "UPDATE jobs SET status='cancelled', cancel_requested=1, finished_at=?, updated_at=? WHERE id=?",
                (utcnow(), utcnow(), job_id),
            )
        elif job["status"] == "running":
            conn.execute("UPDATE jobs SET cancel_requested=1, updated_at=? WHERE id=?", (utcnow(), job_id))
    return get_job(conn, job_id)


def claim_next_job(conn: sqlite3.Connection, worker_id: str, max_attempts: int) -> dict[str, Any] | None:
    with transaction(conn):
        r = conn.execute(
            "SELECT id FROM jobs WHERE status='queued' AND attempts<? ORDER BY created_at LIMIT 1",
            (max_attempts,),
        ).fetchone()
        if r is None:
            return None
        now = utcnow()
        conn.execute(
            "UPDATE jobs SET status='running', locked_by=?, locked_at=?, started_at=COALESCE(started_at,?), "
            "attempts=attempts+1, updated_at=? WHERE id=?",
            (worker_id, now, now, now, r["id"]),
        )
    return get_job(conn, r["id"])


def requeue_stale_running(conn: sqlite3.Connection, max_attempts: int) -> int:
    """On worker start, running jobs are orphans (single worker). Requeue or fail them."""
    with transaction(conn):
        now = utcnow()
        failed = conn.execute(
            "UPDATE jobs SET status='failed', error='worker restarted too many times', finished_at=?, updated_at=? "
            "WHERE status='running' AND attempts>=?",
            (now, now, max_attempts),
        ).rowcount
        requeued = conn.execute(
            "UPDATE jobs SET status='queued', locked_by=NULL, locked_at=NULL, updated_at=? WHERE status='running'",
            (now,),
        ).rowcount
    return int(failed) + int(requeued)


def update_job(conn: sqlite3.Connection, job_id: str, fields: dict[str, Any]) -> None:
    sets = ", ".join(f"{k}=?" for k in fields)
    with transaction(conn):
        # column names come from code, values are bound
        conn.execute(f"UPDATE jobs SET {sets}, updated_at=? WHERE id=?", (*fields.values(), utcnow(), job_id))  # noqa: S608


def job_cancel_requested(conn: sqlite3.Connection, job_id: str) -> bool:
    r = conn.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()
    return bool(r and r[0])


# ---------------------------------------------------------------- providers
def create_provider(
    conn: sqlite3.Connection,
    name: str,
    provider: str,
    model_id: str,
    endpoint: str | None,
    options: dict[str, Any],
    secret_enc: bytes | None,
) -> dict[str, Any]:
    pid = new_id("provider")
    now = utcnow()
    with transaction(conn):
        conn.execute(
            "INSERT INTO provider_profiles(id,name,provider,model_id,endpoint,options_json,secret_enc,"
            "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (pid, name, provider, model_id, endpoint, json.dumps(options), secret_enc, now, now),
        )
    return get_provider(conn, pid)


def get_provider(conn: sqlite3.Connection, provider_id: str) -> dict[str, Any]:
    r = _row(conn.execute("SELECT * FROM provider_profiles WHERE id=?", (provider_id,)).fetchone())
    if r is None:
        raise NotFound("provider profile not found")
    return r


def list_providers(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM provider_profiles ORDER BY created_at").fetchall()]


def update_provider(conn: sqlite3.Connection, provider_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    if fields:
        sets = ", ".join(f"{k}=?" for k in fields)
        with transaction(conn):
            cur = conn.execute(
                f"UPDATE provider_profiles SET {sets}, updated_at=? WHERE id=?",  # noqa: S608
                (*fields.values(), utcnow(), provider_id),
            )
            if cur.rowcount == 0:
                raise NotFound("provider profile not found")
    return get_provider(conn, provider_id)


def delete_provider(conn: sqlite3.Connection, provider_id: str) -> None:
    with transaction(conn):
        cur = conn.execute("DELETE FROM provider_profiles WHERE id=?", (provider_id,))
        if cur.rowcount == 0:
            raise NotFound("provider profile not found")
