"""OCR orchestration for a page: engine -> grouping -> supplementary recognizer -> language."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from PIL import Image

from ..config import get_settings
from .base import CropRecognizer, OcrEngine, polygon_bbox
from .grouping import group_lines, order_regions
from .langdetect import LangGuess, aggregate_page_language, detect_language

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_engine() -> OcrEngine:
    s = get_settings()
    if s.ocr_engine == "mock":
        from .mock import MockOcrEngine

        return MockOcrEngine()
    from .paddle import PaddleOcrEngine

    return PaddleOcrEngine()


@lru_cache(maxsize=1)
def get_crop_recognizer() -> CropRecognizer | None:
    s = get_settings()
    if not s.manga_ocr_enabled:
        return None
    if s.ocr_engine == "mock":
        from .mock import MockCropRecognizer

        return MockCropRecognizer()
    from .mangaocr import MangaOcrRecognizer

    return MangaOcrRecognizer()


def reset_engine_cache() -> None:
    get_engine.cache_clear()
    get_crop_recognizer.cache_clear()


def _crop(image: Image.Image, polygon: list[tuple[float, float]], pad: int = 4) -> Image.Image:
    x0, y0, x1, y1 = polygon_bbox(polygon)
    w, h = image.size
    box = (
        max(0, int(x0) - pad),
        max(0, int(y0) - pad),
        min(w, int(x1) + pad),
        min(h, int(y1) + pad),
    )
    return image.crop(box)


def run_page_ocr(
    image: Image.Image,
    reading_direction: str,
    page_lang_override: str | None,
    force_manga_ocr: bool = False,
) -> tuple[list[dict[str, Any]], LangGuess]:
    s = get_settings()
    engine = get_engine()
    result = engine.detect_and_recognize(image)
    regions = order_regions(group_lines(result.lines), reading_direction)
    recognizer = get_crop_recognizer()

    out: list[dict[str, Any]] = []
    guesses: list[LangGuess] = []
    for reg in regions:
        text = reg.text
        conf = reg.confidence
        eng = result.engine
        guess = detect_language(text) if not page_lang_override else LangGuess(page_lang_override, 1.0, False)
        # Supplementary recognizer for difficult Japanese regions (low score or on request).
        is_ja_like = guess.lang in ("ja", "unknown")
        # A line the primary recognizer could not read (empty/very low score) marks the region as weak.
        has_weak_line = any(not ln.text or ln.confidence < 0.5 for ln in reg.lines)
        wants_supplement = recognizer is not None and (
            force_manga_ocr
            or (s.manga_ocr_mode == "always_ja" and is_ja_like)
            or (s.manga_ocr_mode == "auto" and is_ja_like and (conf < s.manga_ocr_min_confidence or has_weak_line))
        )
        if wants_supplement and recognizer is not None:
            try:
                alt = recognizer.recognize(_crop(image, reg.polygon)).strip()
                if alt:
                    text, eng = alt, f"{result.engine}+{recognizer.name}"
                    conf = max(conf, s.manga_ocr_min_confidence)
                    if not page_lang_override:
                        guess = detect_language(text)
            except Exception as exc:  # noqa: BLE001 - supplementary path must not break OCR
                log.warning("supplementary recognizer failed: %s", exc.__class__.__name__)
        guesses.append(guess)
        out.append(
            {
                "polygon": [list(p) for p in reg.polygon],
                "direction": reg.direction,
                "reading_order": reg.reading_order,
                "source_lang": guess.lang,
                "lang_confidence": guess.confidence,
                "lang_uncertain": guess.uncertain,
                "ocr_text": text,
                "ocr_confidence": conf,
                "ocr_engine": eng,
                "lines": [
                    {
                        "polygon": [list(p) for p in ln.polygon],
                        "text": ln.text,
                        "confidence": ln.confidence,
                        "furigana": False,
                    }
                    for ln in reg.lines
                ]
                + [
                    {
                        "polygon": [list(p) for p in ln.polygon],
                        "text": ln.text,
                        "confidence": ln.confidence,
                        "furigana": True,
                    }
                    for ln in reg.furigana
                ],
            }
        )
    page_guess = LangGuess(page_lang_override, 1.0, False) if page_lang_override else aggregate_page_language(guesses)
    if not page_lang_override and page_guess.lang == "ja" and not page_guess.uncertain:
        # Kana elsewhere on the page: kanji-only regions are most likely Japanese too (still flagged).
        for item in out:
            if item["lang_uncertain"] and item["source_lang"] != "ja":
                item["source_lang"] = "ja"
                item["lang_confidence"] = page_guess.confidence
    return out, page_guess
