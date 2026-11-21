"""manga-ocr supplementary recognizer for Japanese crops (lazy import, CPU)."""

from __future__ import annotations

import logging
import threading
from typing import Any

from PIL import Image

log = logging.getLogger(__name__)


class MangaOcrRecognizer:
    name = "manga-ocr"

    def __init__(self) -> None:
        self._model: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from manga_ocr import MangaOcr  # type: ignore[import-not-found]

                    log.info("loading manga-ocr model")
                    self._model = MangaOcr(force_cpu=True)
        return self._model

    def recognize(self, crop: Image.Image) -> str:
        model = self._load()
        return str(model(crop.convert("RGB")))
