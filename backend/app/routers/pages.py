from __future__ import annotations

import json
import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from .. import export, region_merge, repo, schemas, serializers, storage
from ..deps import get_db

router = APIRouter(prefix="/api/pages", tags=["pages"])
DB = Annotated[sqlite3.Connection, Depends(get_db)]


@router.get("/{page_id}", response_model=schemas.PageOut)
def get_page(page_id: str, conn: DB) -> schemas.PageOut:
    return serializers.page_out(repo.get_page(conn, page_id))


@router.patch("/{page_id}", response_model=schemas.PageOut)
def update_page(page_id: str, body: schemas.PageUpdate, conn: DB) -> schemas.PageOut:
    fields: dict[str, Any] = {}
    if body.clear_source_lang_override:
        fields["source_lang_override"] = None
    elif body.source_lang_override is not None:
        fields["source_lang_override"] = body.source_lang_override
    if not fields:
        return serializers.page_out(repo.get_page(conn, page_id))
    row = repo.update_page(conn, page_id, fields, version=body.version)
    if "source_lang_override" in fields:
        # Language override applies to every region on the page; translations made for another
        # language (and the render that draws them) are outdated.
        lang = fields["source_lang_override"] or row.get("detected_lang") or "unknown"
        repo.apply_page_source_lang(conn, page_id, lang, certain=bool(fields["source_lang_override"]))
        row = repo.get_page(conn, page_id)
    return serializers.page_out(row)


@router.delete("/{page_id}", status_code=204)
def delete_page(page_id: str, conn: DB) -> None:
    page = repo.get_page(conn, page_id)
    repo.delete_page(conn, page_id, page["project_id"])
    for rel in (page["original_path"], page["work_path"], page.get("render_path")):
        if rel:
            try:
                storage.abs_path(rel).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001, S110
                pass


def _file(page: dict[str, Any], key: str, media: str) -> FileResponse:
    rel = page.get(key)
    if not rel:
        raise HTTPException(404, "file not available")
    path = storage.abs_path(rel)
    if not path.exists():
        raise HTTPException(404, "file not available")
    return FileResponse(
        path,
        media_type=media,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/{page_id}/image")
def page_image(page_id: str, conn: DB) -> FileResponse:
    return _file(repo.get_page(conn, page_id), "work_path", "image/png")


@router.get("/{page_id}/original")
def page_original(page_id: str, conn: DB) -> FileResponse:
    page = repo.get_page(conn, page_id)
    media = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}.get(
        page["format"], "application/octet-stream"
    )
    return _file(page, "original_path", media)


@router.get("/{page_id}/render.png")
def page_render(page_id: str, conn: DB) -> FileResponse:
    page = repo.get_page(conn, page_id)
    if page.get("render_path") and page.get("render_stale"):
        # Never serve an image that no longer matches the page's text/layout: rebuild it first.
        try:
            page = export.ensure_current_render(conn, repo.get_project(conn, page["project_id"]), page)
        except export.StaleRenderError as exc:
            raise HTTPException(409, str(exc)) from exc
    return _file(page, "render_path", "image/png")


@router.get("/{page_id}/regions", response_model=list[schemas.RegionOut])
def list_regions(page_id: str, conn: DB) -> list[schemas.RegionOut]:
    repo.get_page(conn, page_id)
    return [serializers.region_out(r) for r in repo.list_regions(conn, page_id)]


@router.post("/{page_id}/regions", response_model=schemas.RegionOut, status_code=201)
def create_region(page_id: str, body: schemas.RegionCreate, conn: DB) -> schemas.RegionOut:
    page = repo.get_page(conn, page_id)
    poly = _validate_polygon(body.polygon, page)
    row = repo.create_region(
        conn,
        page_id,
        page["project_id"],
        poly,
        body.direction,
        body.source_lang,
        body.source_text,
        body.reading_order,
    )
    return serializers.region_out(row)


@router.post("/{page_id}/regions/reorder", response_model=list[schemas.RegionOut])
def reorder_regions(page_id: str, body: schemas.RegionReorder, conn: DB) -> list[schemas.RegionOut]:
    repo.get_page(conn, page_id)
    try:
        repo.reorder_regions(conn, page_id, body.region_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return [serializers.region_out(r) for r in repo.list_regions(conn, page_id)]


@router.patch("/{page_id}/regions/{region_id}", response_model=schemas.RegionOut)
def update_region(page_id: str, region_id: str, body: schemas.RegionUpdate, conn: DB) -> schemas.RegionOut:
    page = repo.get_page(conn, page_id)
    current = repo.get_region(conn, region_id, page_id)
    fields: dict[str, Any] = {}
    # Any change to what the renderer draws (text, geometry, mask, font) outdates the page render, and
    # so does outdating the translation it draws (translation_stale on a translated region).
    render_stale = False
    # Fields that OCR would otherwise refresh get a manual-edit flag when the user actually changed them.
    if body.polygon is not None:
        poly = _validate_polygon(body.polygon, page)
        fields["polygon_json"] = json.dumps(poly)
        if poly != [[float(x), float(y)] for x, y in json.loads(current["polygon_json"])]:
            fields["polygon_edited"] = 1
        render_stale = True
    if body.direction is not None:
        fields["direction"] = body.direction
        if body.direction != current["direction"]:
            fields["direction_edited"] = 1
    if body.reading_order is not None:
        fields["reading_order"] = body.reading_order
        fields["reading_order_edited"] = 1
        render_stale = True
    if body.source_lang is not None:
        fields["source_lang"] = body.source_lang
        fields["lang_uncertain"] = 0
        fields["lang_confidence"] = 1.0
        if body.source_lang != current["source_lang"]:
            fields["source_lang_edited"] = 1
            if current["translated_text"]:
                fields["translation_stale"] = 1
                render_stale = True
    if body.source_text is not None and body.source_text != current["source_text"]:
        fields["source_text"] = body.source_text
        fields["source_edited"] = 1
        # OCR text is kept; the translation must be redone.
        fields["translation_stale"] = 1
        if current["translated_text"]:
            render_stale = True
    if body.final_text is not None and body.final_text != current["final_text"]:
        fields["final_text"] = body.final_text
        fields["final_edited"] = 1
        render_stale = True
        # The text is written for the language the project targets now. If the previous final text
        # (or the pending flag) belonged to another target language, this edit resolves that.
        target_lang = repo.get_project(conn, page["project_id"])["target_lang"]
        fields["final_lang"] = target_lang if body.final_text else ""
        flags = region_merge.load_flags(current["translation_flags_json"])
        lang_mismatch = region_merge.FINAL_LANG_MISMATCH_FLAG in flags or (
            bool(current["final_text"]) and current.get("final_lang") != target_lang
        )
        if body.final_text and lang_mismatch:
            fields["translation_stale"] = 0
            fields["translation_flags_json"] = json.dumps(
                region_merge.without_flag(flags, region_merge.FINAL_LANG_MISMATCH_FLAG)
            )
    if body.review_status is not None:
        fields["review_status"] = body.review_status
    if body.mask_mode is not None:
        fields["mask_mode"] = body.mask_mode
        render_stale = True
    if body.mask_box is not None:
        fields["mask_box_json"] = json.dumps(_validate_box(body.mask_box, page))
        render_stale = True
    if body.text_box is not None:
        fields["text_box_json"] = json.dumps(_validate_box(body.text_box, page))
        render_stale = True
    if body.font_size is not None:
        fields["font_size"] = body.font_size
        render_stale = True
    row = repo.update_region(conn, region_id, page_id, body.version, fields, render_stale=render_stale)
    return serializers.region_out(row)


@router.delete("/{page_id}/regions/{region_id}", status_code=204)
def delete_region(page_id: str, region_id: str, conn: DB) -> None:
    repo.get_page(conn, page_id)
    repo.delete_region(conn, region_id, page_id)


@router.post("/{page_id}/retry", response_model=schemas.JobOut, status_code=201)
def retry_page(page_id: str, body: schemas.JobCreate, conn: DB) -> schemas.JobOut:
    """Re-run one step (or all) for a single page without touching the rest of the batch."""
    page = repo.get_page(conn, page_id)
    options = dict(body.options)
    if body.provider_profile_id:
        options["provider_profile_id"] = body.provider_profile_id
    if body.step in ("translate", "all") and not options.get("provider_profile_id"):
        raise HTTPException(400, "provider_profile_id is required for translation")
    job = repo.create_job(conn, page["project_id"], "page", page_id, body.step, options, body.idempotency_key or None)
    return serializers.job_out(job)


def _validate_polygon(poly: list[tuple[float, float]], page: dict[str, Any]) -> list[list[float]]:
    w, h = page["width"], page["height"]
    out = []
    for x, y in poly:
        if not (-w <= x <= 2 * w and -h <= y <= 2 * h):
            raise HTTPException(400, "polygon out of bounds")
        out.append([float(max(0, min(w, x))), float(max(0, min(h, y)))])
    return out


def _validate_box(box: schemas.Box, page: dict[str, Any]) -> dict[str, float]:
    w, h = page["width"], page["height"]
    x = max(0.0, min(float(w), box.x))
    y = max(0.0, min(float(h), box.y))
    return {
        "x": x,
        "y": y,
        "w": max(0.0, min(float(w) - x, box.w)),
        "h": max(0.0, min(float(h) - y, box.h)),
    }
