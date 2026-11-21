# Manga Translator

Ứng dụng web **local-first chạy trong container** để dịch trang manga tiếng Nhật và truyện tranh tiếng Trung
(Giản thể/Phồn thể) sang tiếng Việt (hoặc ngôn ngữ đích khác). Kế hoạch chi tiết: [PLAN.md](PLAN.md).

```
backend/   FastAPI API + worker nền (Python 3.11, SQLite, PaddleOCR/manga-ocr, adapter dịch)
frontend/  React + TypeScript (Vite), phục vụ qua nginx không đặc quyền
tests/     unit / integration / e2e (pytest, chạy trong Compose project riêng)
benchmarks/ harness giai đoạn 0 (dữ liệu không nằm trong repo)
compose.yaml, compose.test.yaml, Makefile
```

## Yêu cầu

Chỉ cần **Docker (Compose plugin)** hoặc **Podman (podman compose / podman-compose)**. `make doctor` cho biết
engine được chọn (`ENGINE=auto` ưu tiên Docker, có thể ép `ENGINE=docker|podman`).

Tài nguyên khuyến nghị cho image đầy đủ (OCR): ~6 GB đĩa cho image + model, 3 GB RAM cho worker.

Mỗi lần upload tối đa 256 MiB cho cả request (`MT_MAX_UPLOAD_TOTAL_BYTES`), 100 trang, 25 MiB/ảnh. Giới hạn
tổng được kiểm tra ngay khi nhận (nginx `client_max_body_size` và lớp ASGI của API) vì parser multipart ghi tạm
ra tmpfs của container `api` (512m, tính vào RAM 768M); muốn nâng phải tăng đồng bộ cả ba.

## Chạy

```
make doctor      # kiểm tra engine
make config      # xác nhận cấu hình Compose (tạo secrets/master.key nếu chưa có)
make up          # build + chạy api, worker, web
open http://127.0.0.1:8080
make logs / make ps / make down
```

- Chỉ `web` publish cổng `127.0.0.1:8080`; `api`/`worker` ở mạng Compose nội bộ.
- Dữ liệu (ảnh, DB) nằm trong volume `data`; model OCR trong volume `models` (tải lần đầu từ nguồn chính thức,
  checksum được ghi vào `models.lock.json` và kiểm tra ở mỗi lần khởi động worker).
- `secrets/master.key` (git-ignored, do `make secrets` tạo) dùng để mã hóa API key của nhà cung cấp dịch.
  Mất file này thì phải nhập lại key. Không bao giờ commit.
- Tùy chọn qua `.env` (xem `.env.example`). `OCR_EXTRAS=none make up` build image không có PaddleOCR/manga-ocr
  (chỉ để thử giao diện với `MT_OCR_ENGINE=mock`).

### OCR

| Nền tảng | Engine mặc định |
|---|---|
| linux/amd64, linux/arm64 | PaddleOCR 3.7 + **onnxruntime** (`MT_OCR_INFERENCE_ENGINE=onnxruntime`) |
| linux/amd64 (tùy chọn) | PaddlePaddle `paddle_static` — `MT_OCR_INFERENCE_ENGINE=paddle_static` và cài `paddlepaddle` thêm |

PaddlePaddle không phát hành wheel Linux arm64, vì vậy image mặc định dùng onnxruntime để chạy được trên cả
Docker Desktop/Podman trên Apple Silicon.

**Lưu ý VM Apple Silicon (M4 + Apple Virtualization/Podman applehv):** VM khai báo SME nhưng không có SVE nên
wheel `cryptography>=47` và `onnxruntime>=1.31` crash SIGILL (exit 132). `make doctor` phát hiện và in khuyến
nghị; `make build/up/test` **từ chối build mặc định** trên VM như vậy thay vì tự đổi nhánh dependency. Phải chọn
tường minh: `COMPAT_SME_VM=1 make up` (hoặc `COMPAT_SME_VM=1` trong `.env`) để dùng
`backend/requirements-compat-sme-vm.txt` (cryptography 46.0.7 có 4 advisory chỉ vá ở bản ≥48.0.1/49/50,
onnxruntime 1.30.0 — xem ghi chú trong file đó; **chỉ dùng cục bộ, không bao giờ deploy image này**), hoặc
`COMPAT_SME_VM=0` để giữ pin mặc định (sẽ SIGILL). Cách triệt để, không cần pin: tắt SME trong kernel guest
(`arm64.nosme`) hoặc chạy trên máy thật — xem `docs/OPERATIONS.md`, mục "VM Apple Silicon". Model mặc định `PP-OCRv5_mobile_*` (ít RAM); đổi sang
`PP-OCRv6_medium_*`/`PP-OCRv5_server_*` bằng `MT_OCR_DET_MODEL`/`MT_OCR_REC_MODEL`/`MT_OCR_VERSION` **sau khi
benchmark** (xem `benchmarks/`). `PP-OCRv5_server_det` cần hơn 2,5 GB RAM khi nạp qua onnxruntime nên không
vừa giới hạn worker mặc định trên VM 4 GB; tăng `WORKER_MEMORY_LIMIT` nếu máy đủ RAM.

Kích thước ảnh đưa vào phát hiện quyết định recall trên kana nhỏ: mặc định `MT_OCR_DET_LIMIT_SIDE_LEN=1600`
(`MT_OCR_DET_LIMIT_TYPE=max`); thư viện mặc định 960 làm mất nhiều ô thoại trên trang 1,3k–2k px. Phát hiện
chạy thêm một lượt ở `MT_OCR_DET_SECONDARY_SIDE_LEN=960` (0 = tắt) và gộp hộp không trùng để bắt chữ đậm/lớn.
Hộp mà PP-OCRv5 rec không đọc được (text rỗng) vẫn được giữ để tạo vùng và kích hoạt manga-ocr. Có thể chỉnh
`MT_OCR_DET_THRESH`, `MT_OCR_DET_BOX_THRESH`, `MT_OCR_DET_UNCLIP_RATIO`.

manga-ocr chạy bổ sung cho vùng tiếng Nhật: `MT_MANGA_OCR_MODE=auto` (chỉ vùng điểm thấp, mặc định),
`always_ja` (mọi vùng Nhật — chính xác hơn với kana nhỏ, chậm hơn ~1–2 s/vùng trên CPU), `off`; nút
"OCR (manga-ocr)" trong editor luôn ép dùng cho trang đó.

### Nhà cung cấp dịch

Trang **Nhà cung cấp dịch**: Google Cloud Translation v3 (service-account JSON quyền tối thiểu; tùy chọn
`project_id`, `location`, `glossary_id`), OpenAI Responses API (`store:false`), Anthropic Messages API
(structured outputs), hoặc endpoint tương thích Responses API (chỉ HTTPS, host phải nằm trong
`MT_ENDPOINT_ALLOWLIST`; `MT_ALLOW_INTERNAL_ENDPOINTS=1` chỉ dành cho quản trị khi tự host model trong mạng
riêng). Tùy chọn (JSON) cho OpenAI/Responses-compatible: `timeout_seconds` (5–600, model suy luận cần lớn hơn
mặc định 60), `structured_output` (`auto`: thử json_schema rồi tự chuyển sang JSON theo prompt nếu gateway không
trả text; `json_schema`; `none`), `reasoning_effort`, `max_output_tokens`. "Kiểm tra kết nối" trước khi chạy lô; UI hiển thị nơi nhận dữ liệu và ước lượng số ký tự gửi đi.

### Xuất ảnh

Trang dự án: **Tải ZIP đầy đủ** (ảnh đã dịch + JSON + TXT), **Ảnh đã dịch** (`fmt=png`, chỉ PNG đã dựng),
**Toàn bộ ảnh** (`fmt=png&include_original=1`: trang chưa dịch lấy ảnh làm việc, hậu tố `_original`), hoặc tick
nhiều trang rồi **Ảnh đã chọn** (`pages=`). Mỗi thumbnail có nút ⤓ tải PNG của riêng trang đó. Bảng công việc
hiển thị thời gian chạy (đang chạy thì đếm tiếp). Nhà cung cấp đầu tiên trong danh sách được chọn sẵn; ở trang
nhà cung cấp, "Kiểm tra kết nối" điền model đầu tiên gateway trả về nếu Model ID còn trống.

### Dựng ảnh (xoá chữ và chữ mới)

Chế độ nền "tự động" làm theo thứ tự:

1. **Xoá nét chữ**: pixel khác màu nền được tách thành mask (bỏ blob dài chạm biên như viền bong bóng), nở 2 px,
   rồi inpaint. Nếu tìm được lòng bong bóng (flood-fill từ tâm vùng) thì chỉ xoá bên trong bong bóng.
2. **Backend inpaint**: OpenCV Telea (mặc định, ~10 ms) hoặc **LaMa** (ONNX, ~1–1,5 s/tile CPU, cần
   `make lama-model` tải model ~200 MB; `MT_RENDER_INPAINT_MODEL=auto|lama|telea`). LaMa nhận lỗ nhỏ qua một
   cửa sổ 512 px ở độ phân giải gốc; lỗ lớn hơn được chia **tile 512 px chồng 128 px** và trộn bằng trọng số
   raised-cosine (tối đa 12 tile/vùng, quá thì mới thu nhỏ).
3. **Chữ đè thẳng lên tranh** (SFX, webtoon, truyện 3D, không có bong bóng): nét chữ được tách bằng **gom
   màu** (k-means 8 cụm trong polygon OCR + vành quanh nó). Cụm được nhận là chữ khi màu đồng nhất, dày đặc bên
   trong polygon hơn vành ngoài ≥3 lần, nét mảnh, không phải một mảng đặc (vầng sáng, mảng màu). Sau đó: glyph
   bị polygon OCR cắt cụt được lấy trọn nếu phần thừa kết thúc trong tầm với; lớp **viền/bóng** của chữ (màu
   khác, kể cả màu có trong tranh) được hút theo bề rộng đo bằng độ phủ từng vòng khoảng cách quanh nét. Lỗ xoá
   = nét nở theo cỡ glyph (2–8 px) rồi inpaint, tranh xung quanh giữ nguyên. Không tách được thì theo tuỳ chọn
   dự án **"SFX đè tranh"**: *giữ nguyên* (mặc định: không che, không viết chữ dịch, báo `complex_background`
   với `action: kept`) hoặc *hộp trắng + chữ dịch*. Từng vùng vẫn ép được "Nền: hộp trắng" trong editor. Đường
   bong bóng của manga không đi qua bước này (bong bóng được tìm trước), có fixture hồi quy
   `tests/fixtures/render/` (bong bóng Nhật, chữ viền, chữ đặc, chữ 3 lớp trên tranh có sọc cùng màu viền).
4. **Chữ mới**: cỡ chữ tối đa suy từ cỡ glyph gốc (không còn phóng hết hộp), căn giữa theo lòng bong bóng, viền
   trắng chỉ khi nền phức tạp (`MT_RENDER_HALO`), chữ trắng trên nền tối, font viết tay Patrick Hand cho vi/en
   (đủ dấu tiếng Việt, OFL) và Noto Sans CJK cho ja/zh; đích ja/zh với vùng dọc được dựng **dọc** (cột phải → trái).

`MT_RENDER_INPAINT=0` khôi phục hẳn cách tô hộp cũ để so sánh. Báo cáo dựng (`render_report`) ghi `inpainted`,
`inpainter` (`lama`/`telea`) và `kept` (số vùng giữ nguyên).

## Kiểm thử

```
make test
```

Chạy toàn bộ unit/integration/e2e trong Compose project `manga-translator-test` (image không có OCR, OCR và
provider đều mock, volume riêng, tự dọn sau khi chạy). Thất bại khi bất kỳ test nào thất bại.

Project test được cô lập hoàn toàn khỏi bí mật thật: không nạp `.env` (`--env-file /dev/null`, `env_file` bị
`!reset`), không mount `secrets/master.key` (secret bị `!reset`, master key là giá trị thử nghiệm inline
`MT_MASTER_KEY` trong `compose.test.yaml`), nên `make test` chạy được khi chưa có `.env` lẫn `secrets/master.key`.
Kiểm tra: `podman compose -f compose.yaml -f compose.test.yaml -p manga-translator-test --env-file /dev/null config`
không chứa `env_file` hay `secrets/master.key`. Trên VM Apple Silicon bị lỗi SME cần `COMPAT_SME_VM=1 make test`
(xem mục OCR ở trên).

Chạy nhanh trên máy phát triển (không bắt buộc): `python -m venv .venv && pip install -r backend/requirements.txt
-r backend/requirements-test.txt && cd backend && pytest`.

## Bảo mật (tóm tắt, chi tiết ở PLAN.md §5)

- Ảnh chỉ nhận PNG/JPEG/WebP theo nội dung, decode thật, giới hạn pixel/dung lượng, bỏ EXIF trong bản làm việc;
  ảnh gốc được giữ nguyên và không bao giờ ghi đè.
- Mọi file lưu dưới ID ngẫu nhiên; tên file người dùng chỉ dùng để hiển thị; ZIP export dùng tên sinh ra.
- API key mã hóa bằng master key ngoài DB/image/volume dữ liệu; không vào log, URL, export hay frontend.
- Endpoint tùy chỉnh: HTTPS, allowlist, chặn IP private/link-local/metadata sau phân giải DNS, ghim IP khi kết
  nối, không theo redirect, giới hạn kích thước phản hồi và timeout.
- Container non-root, `cap_drop ALL`, `no-new-privileges`, root FS chỉ đọc, tmpfs cho file tạm, giới hạn
  CPU/RAM/PID; không mount socket engine hay thư mục home.
- Văn bản OCR/bản dịch là dữ liệu: kiểm tra schema/ID/độ dài, UI render dạng text, CSP chặt.
- Ứng dụng **chưa có đăng nhập**: chỉ chạy trên `127.0.0.1`. Muốn mở ra mạng phải thêm xác thực, TLS, CSRF,
  rate limit (ngoài MVP).

## Trạng thái theo giai đoạn

| Giai đoạn | Trạng thái |
|---|---|
| 0. Khảo sát mẫu | Harness sẵn sàng (`benchmarks/`); **chưa có bộ ảnh có quyền sử dụng** nên chưa có số liệu. |
| 1. Nền tảng an toàn | Hoàn thành: monorepo, Dockerfile, Compose, Makefile, migration, upload/preview, giới hạn tài nguyên, CI. |
| 2. OCR và biên tập | Hoàn thành về mã: PaddleOCR (onnxruntime) + manga-ocr, chiều chữ/ngôn ngữ/thứ tự đọc, editor. Cần benchmark giai đoạn 0 để chốt model. |
| 3. Dịch đa provider | Hoàn thành: 4 adapter + mock, glossary, retry/backoff, giới hạn chi phí, idempotency, test kết nối. |
| 4. Xuất và hoàn thiện | Hoàn thành: overlay chữ, xoá nét chữ + inpaint (fallback mặt nạ nền phẳng), chỉnh hộp/cỡ chữ, PNG/JSON/TXT/ZIP; theo dõi render/dịch cũ (tự dựng lại khi xuất), OCR lại giữ sửa tay, job `partial` + chạy lại trang lỗi (PLAN.md §10). Nghiệm thu Compose 05/11/2026 trên Podman: `make test` 124 pass; 129 pass ngày 11/11/2026 sau bước inpaint, 135 pass ngày 13/11/2026 sau typography + LaMa, 148 pass cùng ngày sau chữ đè tranh + LaMa tile và chỉnh theo webtoon thật. |
| 5. Sau MVP | Chưa bắt đầu (inpainting, CBZ/PDF, nhiều người dùng). |
