"""Row -> API model conversion."""

from __future__ import annotations

import json
from typing import Any

from . import schemas


def project_out(row: dict[str, Any], pages: list[dict[str, Any]]) -> schemas.ProjectOut:
    return schemas.ProjectOut(
        id=row["id"],
        name=row["name"],
        target_lang=row["target_lang"],
        reading_direction=row["reading_direction"],
        glossary=[schemas.GlossaryEntry(**g) for g in json.loads(row["glossary_json"] or "[]")],
        settings=json.loads(row["settings_json"] or "{}"),
        version=row["version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        pages=[page_out(p) for p in pages],
    )


def page_out(row: dict[str, Any]) -> schemas.PageOut:
    return schemas.PageOut(
        id=row["id"],
        project_id=row["project_id"],
        position=row["position"],
        display_name=row["display_name"],
        width=row["width"],
        height=row["height"],
        format=row["format"],
        status=row["status"],
        steps=json.loads(row["steps_json"] or "{}"),
        error=row.get("error"),
        source_lang_override=row.get("source_lang_override"),
        detected_lang=row.get("detected_lang"),
        lang_uncertain=bool(row.get("lang_uncertain")),
        # has_render means "a render exists AND reflects the current text/layout".
        has_render=bool(row.get("render_path")) and not row.get("render_stale"),
        render_stale=bool(row.get("render_stale")),
        translation_stale_count=int(row.get("translation_stale_count") or 0),
        translation_stale=bool(row.get("translation_stale_count")),
        render_report=json.loads(row["render_report_json"]) if row.get("render_report_json") else None,
        version=row["version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _box(raw: str | None) -> schemas.Box | None:
    return schemas.Box(**json.loads(raw)) if raw else None


def region_out(row: dict[str, Any]) -> schemas.RegionOut:
    return schemas.RegionOut(
        id=row["id"],
        page_id=row["page_id"],
        polygon=[tuple(p) for p in json.loads(row["polygon_json"])],
        direction=row["direction"],
        reading_order=row["reading_order"],
        source_lang=row["source_lang"],
        lang_confidence=row["lang_confidence"],
        lang_uncertain=bool(row["lang_uncertain"]),
        ocr_text=row["ocr_text"],
        ocr_confidence=row["ocr_confidence"],
        ocr_engine=row["ocr_engine"],
        source_text=row["source_text"],
        source_edited=bool(row["source_edited"]),
        polygon_edited=bool(row.get("polygon_edited")),
        direction_edited=bool(row.get("direction_edited")),
        source_lang_edited=bool(row.get("source_lang_edited")),
        translated_text=row["translated_text"],
        final_text=row["final_text"],
        final_edited=bool(row["final_edited"]),
        final_lang=row.get("final_lang") or "",
        translation_provider=row["translation_provider"],
        translation_stale=bool(row["translation_stale"]),
        translation_flags=json.loads(row["translation_flags_json"] or "[]"),
        review_status=row["review_status"],
        mask_mode=row["mask_mode"],
        mask_box=_box(row.get("mask_box_json")),
        text_box=_box(row.get("text_box_json")),
        font_size=row.get("font_size"),
        lines=json.loads(row["lines_json"] or "[]"),
        origin=row["origin"],
        version=row["version"],
    )


def job_out(row: dict[str, Any]) -> schemas.JobOut:
    return schemas.JobOut(
        id=row["id"],
        project_id=row["project_id"],
        scope=row["scope"],
        page_id=row.get("page_id"),
        step=row["step"],
        status=row["status"],
        progress=json.loads(row["progress_json"] or "{}"),
        error=row.get("error"),
        attempts=row["attempts"],
        cancel_requested=bool(row["cancel_requested"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row.get("started_at"),
        finished_at=row.get("finished_at"),
    )


def provider_out(row: dict[str, Any]) -> schemas.ProviderProfileOut:
    return schemas.ProviderProfileOut(
        id=row["id"],
        name=row["name"],
        provider=row["provider"],
        model_id=row["model_id"],
        endpoint=row.get("endpoint"),
        options=json.loads(row["options_json"] or "{}"),
        has_secret=bool(row.get("secret_enc")),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
