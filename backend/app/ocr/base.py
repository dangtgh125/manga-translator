"""OCR engine contract. Detection returns polygons + per-line text; grouping happens elsewhere."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from PIL import Image

Point = tuple[float, float]


@dataclass
class OcrLine:
    polygon: list[Point]
    text: str
    confidence: float
    engine: str = ""
    angle: int = 0  # textline orientation in degrees if known (0/90/180/270)


@dataclass
class OcrPageResult:
    lines: list[OcrLine] = field(default_factory=list)
    engine: str = ""


class OcrEngine(Protocol):
    name: str

    def detect_and_recognize(self, image: Image.Image) -> OcrPageResult: ...


class CropRecognizer(Protocol):
    """Secondary recognizer for difficult crops (e.g. manga-ocr for Japanese)."""

    name: str

    def recognize(self, crop: Image.Image) -> str: ...


def polygon_bbox(poly: list[Point]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)
