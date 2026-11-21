# Giai đoạn 0 — Khảo sát mẫu và benchmark OCR

Mục tiêu (PLAN.md §6, giai đoạn 0): ít nhất 100 trang có quyền sử dụng gồm Nhật / Trung Giản thể / Trung
Phồn thể, chữ dọc/ngang, scan mờ, furigana; gán nhãn vùng + văn bản chuẩn; đo recall phát hiện vùng,
CER theo nhóm, tỷ lệ đúng thứ tự đọc; chọn cấu hình OCR bằng số liệu.

## Trạng thái

**Chưa có bộ dữ liệu.** Repo không chứa ảnh manga (bản quyền). Thư mục `benchmarks/data/` được git-ignore;
hãy đặt ảnh có quyền sử dụng và nhãn vào đó theo định dạng dưới. Harness `run_benchmark.py` đã sẵn sàng.

## Định dạng dữ liệu

```
benchmarks/data/
  manifest.json          # danh sách trang + nhãn
  images/<file>.png
```

`manifest.json` tuân theo `manifest.schema.json`:

```json
{
  "pages": [
    {
      "image": "images/0001.png",
      "group": "ja-vertical-clean",        // ja|zh-Hans|zh-Hant x vertical|horizontal|mixed x clean|blurry|furigana
      "reading_direction": "rtl",
      "regions": [
        {"polygon": [[x,y],[x,y],[x,y],[x,y]], "text": "...", "lang": "ja", "order": 0}
      ]
    }
  ]
}
```

## Chạy

Trong container worker (model đã cache):

```
podman compose -p manga-translator -f compose.yaml run --rm \
  -v "$PWD/benchmarks:/bench:Z" worker python /bench/run_benchmark.py /bench/data/manifest.json \
  --out /bench/reports/report.json --det-model PP-OCRv5_mobile_det --rec-model PP-OCRv5_mobile_rec
```

So sánh cấu hình bằng cách đổi `--det-model/--rec-model/--ocr-version` (PP-OCRv6 medium/small, PP-OCRv5
mobile/server) và `--force-manga-ocr`. Mỗi lần chạy ghi `report.json` với:

- `detection_recall@0.5` và precision (IoU trên bbox của polygon);
- `cer` (character error rate, Levenshtein) theo `group`;
- `reading_order_accuracy` (Kendall tau-based, tỷ lệ cặp đúng thứ tự);
- `lang_accuracy`;
- thời gian/trang và RSS tối đa.

Ngưỡng nghiệm thu được chốt sau khi có số liệu thực (PLAN.md §7), không tuyên bố trước.
