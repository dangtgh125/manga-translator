#!/usr/bin/env python3
"""Phase-0 benchmark harness: detection recall, CER per group, reading-order and language accuracy.

Usage: run_benchmark.py manifest.json --out report.json [--det-model ..] [--rec-model ..] [--ocr-version ..]
       [--inference-engine onnxruntime|paddle_static] [--force-manga-ocr] [--mock]
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import time
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def levenshtein(a: str, b: str) -> int:
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def bbox(poly):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def iou(a, b) -> float:
    ax0, ay0, ax1, ay1 = bbox(a)
    bx0, by0, bx1, by1 = bbox(b)
    ix = max(0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0, min(ay1, by1) - max(ay0, by0))
    inter = ix * iy
    union = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / union if union else 0.0


def pair_order_accuracy(gt_orders: list[int], pred_orders: list[int]) -> float:
    n = len(gt_orders)
    if n < 2:
        return 1.0
    ok = total = 0
    for i in range(n):
        for j in range(i + 1, n):
            total += 1
            ok += (gt_orders[i] < gt_orders[j]) == (pred_orders[i] < pred_orders[j])
    return ok / total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("--out", default="report.json")
    ap.add_argument("--det-model")
    ap.add_argument("--rec-model")
    ap.add_argument("--ocr-version")
    ap.add_argument("--inference-engine")
    ap.add_argument("--force-manga-ocr", action="store_true")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--iou", type=float, default=0.5)
    args = ap.parse_args()

    if args.det_model:
        os.environ["MT_OCR_DET_MODEL"] = args.det_model
    if args.rec_model:
        os.environ["MT_OCR_REC_MODEL"] = args.rec_model
    if args.ocr_version:
        os.environ["MT_OCR_VERSION"] = args.ocr_version
    if args.inference_engine:
        os.environ["MT_OCR_INFERENCE_ENGINE"] = args.inference_engine
    if args.mock:
        os.environ["MT_OCR_ENGINE"] = "mock"

    from PIL import Image

    from app.ocr.service import run_page_ocr

    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    groups: dict[str, dict[str, float]] = {}
    per_page = []
    t_total = 0.0
    for page in manifest["pages"]:
        img = Image.open(manifest_path.parent / page["image"]).convert("RGB")
        t0 = time.monotonic()
        regions, page_lang = run_page_ocr(img, page.get("reading_direction", "rtl"), None, args.force_manga_ocr)
        dt = time.monotonic() - t0
        t_total += dt
        gt = page["regions"]
        matched = {}
        for gi, g in enumerate(gt):
            best, best_iou = None, 0.0
            for pi, r in enumerate(regions):
                v = iou(g["polygon"], r["polygon"])
                if v > best_iou and pi not in matched.values():
                    best, best_iou = pi, v
            if best is not None and best_iou >= args.iou:
                matched[gi] = best
        recall = len(matched) / len(gt) if gt else 1.0
        precision = len(matched) / len(regions) if regions else 1.0
        cer_num = cer_den = 0
        lang_ok = 0
        for gi, pi in matched.items():
            gt_text, pr_text = gt[gi]["text"], regions[pi]["ocr_text"]
            cer_num += levenshtein(gt_text, pr_text)
            cer_den += max(1, len(gt_text))
            lang_ok += gt[gi].get("lang", "unknown") == regions[pi]["source_lang"]
        order_acc = pair_order_accuracy([gt[gi].get("order", gi) for gi in matched], [regions[pi]["reading_order"] for pi in matched.values()])
        g = groups.setdefault(page["group"], {"pages": 0, "recall": 0.0, "precision": 0.0, "cer_num": 0, "cer_den": 0, "order": 0.0, "lang_ok": 0, "matched": 0})
        g["pages"] += 1
        g["recall"] += recall
        g["precision"] += precision
        g["cer_num"] += cer_num
        g["cer_den"] += cer_den
        g["order"] += order_acc
        g["lang_ok"] += lang_ok
        g["matched"] += len(matched)
        per_page.append({"image": page["image"], "group": page["group"], "recall": recall, "precision": precision,
                         "cer": cer_num / cer_den if cer_den else None, "order_accuracy": order_acc, "seconds": dt})
    summary = {}
    for name, g in groups.items():
        summary[name] = {
            "pages": g["pages"],
            "detection_recall@%.1f" % args.iou: g["recall"] / g["pages"],
            "detection_precision": g["precision"] / g["pages"],
            "cer": g["cer_num"] / g["cer_den"] if g["cer_den"] else None,
            "reading_order_accuracy": g["order"] / g["pages"],
            "lang_accuracy": g["lang_ok"] / g["matched"] if g["matched"] else None,
        }
    report = {
        "config": {k: os.environ.get(k) for k in ("MT_OCR_ENGINE", "MT_OCR_VERSION", "MT_OCR_DET_MODEL", "MT_OCR_REC_MODEL", "MT_OCR_INFERENCE_ENGINE")},
        "force_manga_ocr": args.force_manga_ocr,
        "pages": len(per_page),
        "seconds_per_page": t_total / len(per_page) if per_page else None,
        "max_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024),
        "groups": summary,
        "per_page": per_page,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"groups": summary, "seconds_per_page": report["seconds_per_page"]}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
