"""Translate a page's regions with batching, bounded concurrency, retry/backoff, cost cap, cancel."""

from __future__ import annotations

import json
import logging
import random
import sqlite3
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .. import repo
from ..config import get_settings
from ..logging_utils import safe_error
from .base import (
    ProviderError,
    RegionText,
    TranslatedRegion,
    TranslationContext,
    TranslationProvider,
    glossary_check,
)

log = logging.getLogger(__name__)


class JobCancelled(RuntimeError):
    pass


class CostLimitExceeded(RuntimeError):
    pass


def estimate(regions: list[dict[str, Any]]) -> dict[str, int]:
    texts = [r["source_text"] for r in regions if r["source_text"].strip()]
    return {"regions": len(texts), "characters": sum(len(t) for t in texts)}


def _with_retries(fn: Callable[[], list[TranslatedRegion]], max_retries: int) -> list[TranslatedRegion]:
    attempt = 0
    while True:
        try:
            return fn()
        except ProviderError as exc:
            attempt += 1
            if not exc.retryable or attempt > max_retries:
                raise
            delay = exc.retry_after if exc.retry_after is not None else min(30.0, 0.5 * (2**attempt))
            delay += random.uniform(0, 0.25)  # noqa: S311 - jitter, not security
            log.warning(
                "provider error (attempt %s/%s, retry in %.1fs): %s",
                attempt,
                max_retries,
                delay,
                safe_error(exc),
            )
            time.sleep(delay)


def translate_page(
    conn: sqlite3.Connection,
    project: dict[str, Any],
    page: dict[str, Any],
    provider: TranslationProvider,
    job_id: str,
    previous_texts: list[str],
    only_stale: bool = False,
    chars_budget: dict[str, int] | None = None,
) -> int:
    """Translate regions of one page. Returns number of regions translated."""
    s = get_settings()
    regions = [r for r in repo.list_regions(conn, page["id"]) if r["source_text"].strip()]
    if only_stale:
        regions = [r for r in regions if r["translation_stale"] or not r["translated_text"]]
    if not regions:
        return 0
    est = estimate(regions)
    if chars_budget is not None:
        chars_budget["used"] += est["characters"]
        if chars_budget["used"] > s.max_chars_per_job:
            raise CostLimitExceeded(f"job exceeds MT_MAX_CHARS_PER_JOB ({s.max_chars_per_job} characters)")

    glossary = json.loads(project["glossary_json"] or "[]")
    ctx = TranslationContext(
        target_lang=project["target_lang"],
        glossary=glossary,
        project_name=project["name"],
        previous_texts=list(previous_texts),
        page_index=page["position"],
    )
    items = [RegionText(r["id"], r["source_text"], r["source_lang"], r["direction"]) for r in regions]
    # What the batch is translated under; apply_translation refuses to mark a result current if the
    # project/page context moved on while the provider was busy.
    context_fingerprint = repo.translation_context_fingerprint(project, page)
    batches = [items[i : i + s.translate_batch_size] for i in range(0, len(items), s.translate_batch_size)]
    write_lock = threading.Lock()
    done = 0

    def run_batch(batch: list[RegionText]) -> int:
        if repo.job_cancel_requested(conn, job_id):
            raise JobCancelled("cancelled")
        results = _with_retries(lambda: provider.translate_batch(batch, ctx), s.provider_max_retries)
        glossary_check(batch, results, glossary)
        with write_lock:
            for res in results:
                row = next(r for r in regions if r["id"] == res.region_id)
                # Conditional write: edits made while the provider was busy are never overwritten.
                repo.apply_translation(
                    conn,
                    res.region_id,
                    expected_version=row["version"],
                    sent_text=row["source_text"],
                    translated_text=res.text,
                    provider_label=f"{provider.kind}:{provider.model_id}",
                    flags=res.flags,
                    context_fingerprint=context_fingerprint,
                    sent_lang=row["source_lang"],
                    target_lang=project["target_lang"],
                )
        return len(results)

    with ThreadPoolExecutor(max_workers=max(1, s.max_ai_concurrency)) as pool:
        futures = [pool.submit(run_batch, b) for b in batches]
        errors: list[BaseException] = []
        for fut in as_completed(futures):
            try:
                done += fut.result()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
        if errors:
            cancelled = [e for e in errors if isinstance(e, JobCancelled)]
            if cancelled:
                raise cancelled[0]
            raise errors[0]
    previous_texts.extend(it.text for it in items)
    return done
