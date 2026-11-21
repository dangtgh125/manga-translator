"""Export rendered pages and text data (PNG/JSON/TXT/ZIP). Entry names are generated, never user input."""

from __future__ import annotations

import json
import logging
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from . import repo, storage
from .config import get_settings
from .logging_utils import safe_error
from .pipeline import step_render


class StaleRenderError(RuntimeError):
    """A page's render is outdated and could not be rebuilt; the caller must not ship the old image."""

    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.page_ids = [p["id"] for p in pages]
        names = ", ".join(f"page {p['position'] + 1} ({p['display_name']})" for p in pages)
        super().__init__(f"render is outdated and could not be rebuilt for: {names}")


class StaleTranslationError(RuntimeError):
    """Pages whose rendered image draws outdated translations; export refuses unless the caller opts in."""

    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.page_ids = [p["id"] for p in pages]
        self.names = ", ".join(f"page {p['position'] + 1} ({p['display_name']})" for p in pages)
        super().__init__(
            f"translation is outdated for: {self.names}; re-translate first, "
            "or pass allow_stale_translation=1 to export anyway"
        )


def stale_translation_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pages that would ship a rendered image drawing at least one outdated translation."""
    return [p for p in pages if p.get("render_path") and int(p.get("translation_stale_count") or 0) > 0]


def ensure_current_render(conn: sqlite3.Connection, project: dict[str, Any], page: dict[str, Any]) -> dict[str, Any]:
    """Re-render synchronously when the stored render no longer matches the page's data.

    Pages that were never rendered are returned unchanged. Raises StaleRenderError when the
    re-render fails, so an outdated image is never served or exported.
    """
    if not page.get("render_path") or not page.get("render_stale"):
        return page
    try:
        step_render(conn, project, page)
    except Exception as exc:
        raise StaleRenderError([page]) from exc
    return repo.get_page(conn, page["id"])


log = logging.getLogger(__name__)


def page_data(conn: sqlite3.Connection, project: dict[str, Any], page: dict[str, Any]) -> dict[str, Any]:
    regions = repo.list_regions(conn, page["id"])
    return {
        "page_id": page["id"],
        "position": page["position"],
        "display_name": page["display_name"],
        "width": page["width"],
        "height": page["height"],
        "source_lang": page.get("source_lang_override") or page.get("detected_lang"),
        "target_lang": project["target_lang"],
        "regions": [
            {
                "id": r["id"],
                "reading_order": r["reading_order"],
                "polygon": json.loads(r["polygon_json"]),
                "direction": r["direction"],
                "source_lang": r["source_lang"],
                "ocr_text": r["ocr_text"],
                "ocr_confidence": r["ocr_confidence"],
                "source_text": r["source_text"],
                "translated_text": r["translated_text"],
                "final_text": r["final_text"],
                "review_status": r["review_status"],
                "flags": json.loads(r["translation_flags_json"] or "[]"),
            }
            for r in regions
        ],
    }


def page_txt(data: dict[str, Any]) -> str:
    lines = [f"# page {data['position'] + 1}: {data['display_name']}"]
    for r in data["regions"]:
        lines.append(f"[{r['reading_order'] + 1}] {r['source_text']}")
        lines.append(f"    => {r['final_text'] or r['translated_text']}")
    return "\n".join(lines) + "\n"


def select_pages(conn: sqlite3.Connection, project_id: str, page_ids: list[str] | None) -> list[dict[str, Any]]:
    pages = repo.list_pages(conn, project_id)
    if page_ids:
        wanted = set(page_ids)
        pages = [p for p in pages if p["id"] in wanted]
    return pages


def _refresh_renders(
    conn: sqlite3.Connection, project: dict[str, Any], pages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Re-render every stale page before export; report all failures at once instead of the first."""
    fresh: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for p in pages:
        try:
            fresh.append(ensure_current_render(conn, project, p))
        except StaleRenderError as exc:
            log.warning("re-render before export failed for page %s: %s", p["id"], safe_error(exc.__cause__ or exc))
            failed.append(p)
    if failed:
        raise StaleRenderError(failed)
    return fresh


def _new_zip_path() -> Path:
    tmp = tempfile.NamedTemporaryFile(prefix="export-", suffix=".zip", dir=storage.tmp_dir(), delete=False)  # noqa: SIM115
    tmp.close()
    return Path(tmp.name)


def _existing(rel: str | None) -> Path | None:
    if not rel:
        return None
    path = storage.abs_path(rel)
    return path if path.exists() else None


def _pages_for_export(
    conn: sqlite3.Connection,
    project: dict[str, Any],
    page_ids: list[str] | None,
    allow_stale_translation: bool,
) -> list[dict[str, Any]]:
    """Selected pages with current renders. Outdated translations are re-rendered (so text/layout edits
    apply) but never exported silently: StaleTranslationError unless the caller opted in."""
    pages = select_pages(conn, project["id"], page_ids)
    stale = stale_translation_pages(pages)
    if stale and not allow_stale_translation:
        raise StaleTranslationError(stale)
    return _refresh_renders(conn, project, pages)


def build_png_zip(
    conn: sqlite3.Connection,
    project: dict[str, Any],
    page_ids: list[str] | None = None,
    include_original: bool = False,
    allow_stale_translation: bool = False,
) -> Path:
    """ZIP of translated page images only (no text data).

    Pages without a rendered image are skipped, or — with ``include_original`` — exported as the
    (EXIF-stripped) working image so "all images" downloads keep the page order complete.
    """
    s = get_settings()
    pages = _pages_for_export(conn, project, page_ids, allow_stale_translation)
    out = _new_zip_path()
    total = 0
    try:
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED) as zf:
            for p in pages:
                path = _existing(p.get("render_path"))
                suffix = ""
                if path is None and include_original:
                    path = _existing(p.get("work_path"))
                    suffix = "_original"
                if path is None:
                    continue
                total += path.stat().st_size
                if total > s.export_zip_max_bytes:
                    raise ValueError("export exceeds size limit")
                zf.write(path, f"page_{p['position'] + 1:04d}{suffix}.png")
    except Exception:
        out.unlink(missing_ok=True)
        raise
    return out


def build_zip(
    conn: sqlite3.Connection,
    project: dict[str, Any],
    page_ids: list[str] | None = None,
    allow_stale_translation: bool = False,
) -> Path:
    """Write a ZIP into the temp dir and return its path. Caller deletes it after sending."""
    s = get_settings()
    pages = _pages_for_export(conn, project, page_ids, allow_stale_translation)
    out = _new_zip_path()
    total = 0
    manifest: dict[str, Any] = {
        "project": {
            "id": project["id"],
            "name": project["name"],
            "target_lang": project["target_lang"],
        },
        "pages": [],
    }
    try:
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in pages:
                stem = f"page_{p['position'] + 1:04d}"
                data = page_data(conn, project, p)
                manifest["pages"].append({"file": stem, "page_id": p["id"], "display_name": p["display_name"]})
                zf.writestr(f"{stem}.json", json.dumps(data, ensure_ascii=False, indent=1))
                zf.writestr(f"{stem}.txt", page_txt(data))
                if p.get("render_path"):
                    rp = storage.abs_path(p["render_path"])
                    if rp.exists():
                        total += rp.stat().st_size
                        if total > s.export_zip_max_bytes:
                            raise ValueError("export exceeds size limit")
                        zf.write(rp, f"{stem}.png")
            zf.writestr("project.json", json.dumps(manifest, ensure_ascii=False, indent=1))
    except Exception:
        out.unlink(missing_ok=True)
        raise
    return out
