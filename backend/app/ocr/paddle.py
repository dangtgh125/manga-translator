"""PaddleOCR 3.x engine (PP-OCRv5/v6) via onnxruntime or paddle_static inference engines.

Imports are lazy so the core image (and tests) never need the ML stack.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import numpy as np
from PIL import Image

from ..config import get_settings
from .base import OcrLine, OcrPageResult, polygon_bbox

log = logging.getLogger(__name__)


class PaddleOcrEngine:
    name = "paddleocr"

    def __init__(self) -> None:
        self._ocr: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        if self._ocr is not None:
            return self._ocr
        with self._lock:
            if self._ocr is not None:
                return self._ocr
            s = get_settings()
            from paddleocr import PaddleOCR  # type: ignore[import-not-found]

            kwargs: dict[str, Any] = {
                "ocr_version": s.ocr_version,
                "text_detection_model_name": s.ocr_det_model,
                "text_recognition_model_name": s.ocr_rec_model,
                "use_doc_orientation_classify": False,
                "use_doc_unwarping": False,
                "use_textline_orientation": s.ocr_textline_orientation,
                "engine": s.ocr_inference_engine,
                "text_det_limit_side_len": s.ocr_det_limit_side_len,
                "text_det_limit_type": s.ocr_det_limit_type,
            }
            for key, value in (
                ("text_det_thresh", s.ocr_det_thresh),
                ("text_det_box_thresh", s.ocr_det_box_thresh),
                ("text_det_unclip_ratio", s.ocr_det_unclip_ratio),
            ):
                if value is not None:
                    kwargs[key] = value
            if s.ocr_inference_engine == "onnxruntime":
                kwargs["engine_config"] = {
                    "providers": ["CPUExecutionProvider"],
                    "intra_op_num_threads": s.ocr_cpu_threads,
                    "inter_op_num_threads": 1,
                }
            else:
                kwargs["device"] = "cpu"
                kwargs["cpu_threads"] = s.ocr_cpu_threads
            log.info(
                "loading PaddleOCR det=%s rec=%s engine=%s",
                s.ocr_det_model,
                s.ocr_rec_model,
                s.ocr_inference_engine,
            )
            self._ocr = PaddleOCR(**kwargs)
            self.name = f"paddleocr:{s.ocr_version}:{s.ocr_inference_engine}"
            return self._ocr

    def detect_and_recognize(self, image: Image.Image) -> OcrPageResult:
        s = get_settings()
        ocr = self._load()
        arr = np.asarray(image.convert("RGB"))[:, :, ::-1]  # BGR for paddle
        lines = self._predict_lines(ocr, arr, None)
        secondary = s.ocr_det_secondary_side_len
        if secondary and secondary != s.ocr_det_limit_side_len:
            # Multi-scale detection: bold/large text is sometimes only found at a smaller scale and small
            # kana only at the larger one. Keep primary lines, add secondary lines that overlap nothing.
            extra = self._predict_lines(ocr, arr, secondary)
            lines.extend(merge_new_lines(lines, extra))
        return OcrPageResult(lines=lines, engine=self.name)

    def _predict_lines(self, ocr: Any, arr: np.ndarray, side_len: int | None) -> list[OcrLine]:
        s = get_settings()
        kwargs: dict[str, Any] = {}
        if side_len is not None:
            kwargs = {"text_det_limit_side_len": side_len, "text_det_limit_type": s.ocr_det_limit_type}
        results = ocr.predict(arr, **kwargs)
        lines: list[OcrLine] = []
        for res in results:
            data = res.json if hasattr(res, "json") else res
            data = data.get("res", data) if isinstance(data, dict) else data
            polys = data.get("rec_polys") or data.get("dt_polys") or []
            texts = data.get("rec_texts") or []
            scores = data.get("rec_scores") or []
            angles = data.get("textline_orientation_angles") or []
            for i, poly in enumerate(polys):
                text = str(texts[i]) if i < len(texts) else ""
                score = float(scores[i]) if i < len(scores) else 0.0
                angle = int(angles[i]) if i < len(angles) and angles[i] is not None else 0
                pts = [(float(p[0]), float(p[1])) for p in np.asarray(poly).tolist()]
                # Keep boxes whose recognition came back empty (bold/stylised text): they still shape the
                # speech region and trigger the supplementary recognizer.
                lines.append(
                    OcrLine(
                        polygon=pts,
                        text=text.strip(),
                        confidence=score if text.strip() else 0.0,
                        engine=self.name,
                        angle=angle,
                    )
                )
        return lines


def _bbox_iou(a: OcrLine, b: OcrLine) -> float:
    ax0, ay0, ax1, ay1 = polygon_bbox(a.polygon)
    bx0, by0, bx1, by1 = polygon_bbox(b.polygon)
    iw = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    ih = max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / union if union > 0 else 0.0


def merge_new_lines(primary: list[OcrLine], extra: list[OcrLine], iou_threshold: float = 0.3) -> list[OcrLine]:
    """Lines from a secondary pass that do not overlap any primary line (or each other)."""
    accepted: list[OcrLine] = []
    for ln in extra:
        if any(_bbox_iou(ln, p) >= iou_threshold for p in primary) or any(
            _bbox_iou(ln, a) >= iou_threshold for a in accepted
        ):
            continue
        accepted.append(ln)
    return accepted
