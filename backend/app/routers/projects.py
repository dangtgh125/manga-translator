from __future__ import annotations

import json
import sqlite3
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from starlette.datastructures import FormData
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import export, repo, schemas, serializers, storage
from ..config import get_settings
from ..deps import get_db
from ..ids import new_id
from ..security.images import ImageValidationError, validate_image_bytes
from ..security.paths import sanitize_display_name

router = APIRouter(prefix="/api/projects", tags=["projects"])
DB = Annotated[sqlite3.Connection, Depends(get_db)]


@router.post("", response_model=schemas.ProjectOut, status_code=201)
def create_project(body: schemas.ProjectCreate, conn: DB) -> schemas.ProjectOut:
    row = repo.create_project(
        conn,
        body.name,
        body.target_lang,
        body.reading_direction,
        [g.model_dump() for g in body.glossary],
    )
    return serializers.project_out(row, [])


@router.get("", response_model=list[schemas.ProjectSummary])
def list_projects(conn: DB) -> list[schemas.ProjectSummary]:
    return [
        schemas.ProjectSummary(
            id=r["id"],
            name=r["name"],
            target_lang=r["target_lang"],
            page_count=r["page_count"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
        for r in repo.list_projects(conn)
    ]


@router.get("/{project_id}", response_model=schemas.ProjectOut)
def get_project(project_id: str, conn: DB) -> schemas.ProjectOut:
    row = repo.get_project(conn, project_id)
    return serializers.project_out(row, repo.list_pages(conn, project_id))


@router.patch("/{project_id}", response_model=schemas.ProjectOut)
def update_project(project_id: str, body: schemas.ProjectUpdate, conn: DB) -> schemas.ProjectOut:
    fields: dict[str, object] = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.target_lang is not None:
        fields["target_lang"] = body.target_lang
    if body.reading_direction is not None:
        fields["reading_direction"] = body.reading_direction
    if body.glossary is not None:
        fields["glossary_json"] = json.dumps([g.model_dump() for g in body.glossary])
    if body.settings is not None:
        fields["settings_json"] = json.dumps(body.settings)[:20000]
    before = repo.get_project(conn, project_id)
    row = repo.update_project(conn, project_id, body.version, fields)
    # A new target language or glossary outdates every translation in the project.
    lang_changed = "target_lang" in fields and fields["target_lang"] != before["target_lang"]
    glossary_changed = "glossary_json" in fields and json.loads(str(fields["glossary_json"])) != json.loads(
        before["glossary_json"] or "[]"
    )
    if lang_changed or glossary_changed:
        repo.mark_project_translations_stale(conn, project_id)
    return serializers.project_out(row, repo.list_pages(conn, project_id))


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: str, conn: DB, background: BackgroundTasks) -> None:
    repo.get_project(conn, project_id)
    repo.delete_project(conn, project_id)
    pdir = storage.project_dir(project_id)

    def _rm() -> None:
        import shutil

        shutil.rmtree(pdir, ignore_errors=True)

    background.add_task(_rm)


async def _parse_upload_form(request: Request, max_files: int) -> FormData:
    """Parse the multipart body ourselves so the parser stops at part ``max_files + 1`` instead of spooling
    up to Starlette's default of 1000 parts; total bytes are bounded earlier by BodyLimitMiddleware."""
    try:
        return await request.form(max_files=max_files, max_fields=16)
    except StarletteHTTPException as exc:
        # Starlette maps MultiPartException to its own HTTPException(400); keep the 413 contract for the count limit.
        if exc.status_code == 400 and str(exc.detail).startswith("Too many files"):
            raise HTTPException(413, f"too many files (max {max_files})") from exc
        raise


@router.post("/{project_id}/pages", response_model=list[schemas.PageOut], status_code=201)
async def upload_pages(project_id: str, request: Request, conn: DB) -> list[schemas.PageOut]:
    s = get_settings()
    repo.get_project(conn, project_id)
    form = await _parse_upload_form(request, s.max_pages_per_upload)
    try:
        files = [f for f in form.getlist("files") if isinstance(f, StarletteUploadFile)]
        if not files:
            raise HTTPException(400, "no files")
        if len(files) > s.max_pages_per_upload:
            raise HTTPException(413, f"too many files (max {s.max_pages_per_upload})")
        if repo.count_pages(conn, project_id) + len(files) > s.max_pages_per_project:
            raise HTTPException(413, "project page limit reached")
        created: list[schemas.PageOut] = []
        errors: list[str] = []
        total = 0
        for f in files:
            try:
                data = await f.read(s.max_upload_bytes + 1)
            finally:
                await f.close()  # release the spooled temp file as soon as it is consumed
            total += len(data)
            if total > s.max_upload_total_bytes:
                raise HTTPException(413, "upload too large")
            if len(data) > s.max_upload_bytes:
                errors.append(f"{sanitize_display_name(f.filename or '')}: file too large")
                continue
            try:
                img = validate_image_bytes(data)
            except ImageValidationError as exc:
                errors.append(f"{sanitize_display_name(f.filename or '')}: {exc}")
                continue
            page_id = new_id("page")
            orig = storage.page_original_path(project_id, page_id, img.fmt)
            work = storage.page_work_path(project_id, page_id)
            orig.write_bytes(data)
            work.write_bytes(img.normalized_png)
            try:
                row = repo.add_page(
                    conn,
                    project_id,
                    page_id,
                    sanitize_display_name(f.filename or "page"),
                    storage.rel_path(orig),
                    storage.rel_path(work),
                    img.width,
                    img.height,
                    img.fmt,
                    s.max_pages_per_project,
                )
            except repo.LimitExceeded as exc:
                orig.unlink(missing_ok=True)
                work.unlink(missing_ok=True)
                raise HTTPException(413, str(exc)) from exc
            created.append(serializers.page_out(row))
        if not created and errors:
            raise HTTPException(422, {"message": "no valid images", "errors": errors})
        return created
    finally:
        await form.close()


@router.post("/{project_id}/pages/reorder", response_model=list[schemas.PageOut])
def reorder_pages(project_id: str, body: schemas.PageReorder, conn: DB) -> list[schemas.PageOut]:
    repo.get_project(conn, project_id)
    try:
        repo.reorder_pages(conn, project_id, body.page_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return [serializers.page_out(p) for p in repo.list_pages(conn, project_id)]


@router.get("/{project_id}/export")
def export_project(
    project_id: str,
    conn: DB,
    background: BackgroundTasks,
    fmt: str = Query("zip", pattern="^(zip|json|txt|png)$"),
    pages: str | None = Query(None, max_length=20000),
    include_original: bool = Query(False),
    allow_stale_translation: bool = Query(False),
) -> object:
    """ZIP/PNG exports re-render outdated pages first. A page whose render draws an outdated
    translation is refused (409) unless ``allow_stale_translation=1``; the response then carries an
    ``X-Export-Warning`` header naming those pages."""
    project = repo.get_project(conn, project_id)
    page_ids = [p for p in pages.split(",") if p] if pages else None
    if fmt in ("zip", "png"):
        stale = export.stale_translation_pages(export.select_pages(conn, project_id, page_ids))
        try:
            if fmt == "png":
                path = export.build_png_zip(
                    conn,
                    project,
                    page_ids,
                    include_original=include_original,
                    allow_stale_translation=allow_stale_translation,
                )
            else:
                path = export.build_zip(conn, project, page_ids, allow_stale_translation=allow_stale_translation)
        except (export.StaleRenderError, export.StaleTranslationError) as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(413, str(exc)) from exc
        background.add_task(lambda: path.unlink(missing_ok=True))
        suffix = "-images" if fmt == "png" else ""
        headers: dict[str, str] = {}
        if stale:
            # Header values must be latin-1 safe; page names are user supplied, so ids go in the header.
            headers["X-Export-Warning"] = (
                f"translation outdated on {len(stale)} page(s): {','.join(p['id'] for p in stale)}"
            )
        return FileResponse(path, media_type="application/zip", filename=f"{project_id}{suffix}.zip", headers=headers)
    rows = repo.list_pages(conn, project_id)
    if page_ids:
        rows = [p for p in rows if p["id"] in set(page_ids)]
    datas = [export.page_data(conn, project, p) for p in rows]
    if fmt == "json":
        return {
            "project": {
                "id": project["id"],
                "name": project["name"],
                "target_lang": project["target_lang"],
            },
            "pages": datas,
        }
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse("\n".join(export.page_txt(d) for d in datas))
