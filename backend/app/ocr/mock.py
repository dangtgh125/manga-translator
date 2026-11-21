"""Deterministic mock OCR engine for tests and CI.

Regions are read from a sidecar JSON embedded as a PNG text chunk (`mt_mock_ocr`) when present;
otherwise a fixed layout is produced based on image size. Never used in production builds.
"""

from __future__ import annotations

import json

from PIL import Image

from .base import OcrLine, OcrPageResult


class MockOcrEngine:
    name = "mock"

    def detect_and_recognize(self, image: Image.Image) -> OcrPageResult:
        meta = image.info.get("mt_mock_ocr") if hasattr(image, "info") else None
        lines: list[OcrLine] = []
        if meta:
            for item in json.loads(meta):
                lines.append(
                    OcrLine(
                        polygon=[tuple(p) for p in item["polygon"]],
                        text=item["text"],
                        confidence=float(item.get("confidence", 0.95)),
                        engine=self.name,
                    )
                )
            return OcrPageResult(lines=lines, engine=self.name)
        w, h = image.size
        # Two vertical columns (Japanese-looking) at the right, one horizontal line at bottom-left.
        col_w = max(10, w // 20)
        x0 = w - col_w * 2
        lines.append(
            OcrLine(
                [
                    (x0 + col_w, 10),
                    (x0 + col_w * 2, 10),
                    (x0 + col_w * 2, h // 3),
                    (x0 + col_w, h // 3),
                ],
                "こんにちは",
                0.92,
                self.name,
            )
        )
        lines.append(
            OcrLine(
                [(x0, 10), (x0 + col_w, 10), (x0 + col_w, h // 3), (x0, h // 3)],
                "元気ですか",
                0.88,
                self.name,
            )
        )
        lines.append(
            OcrLine(
                [(10, h - 40), (w // 2, h - 40), (w // 2, h - 10), (10, h - 10)],
                "你好世界",
                0.9,
                self.name,
            )
        )
        return OcrPageResult(lines=lines, engine=self.name)


class MockCropRecognizer:
    name = "mock-crop"

    def recognize(self, crop: Image.Image) -> str:
        return "補助OCR"
