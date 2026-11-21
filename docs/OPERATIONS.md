# Vận hành

## Lệnh

| Lệnh | Tác dụng |
|---|---|
| `make doctor` | Phát hiện Docker/Podman Compose; in engine và file Compose. |
| `make config` | `compose config` để xác nhận cấu hình ứng dụng (tạo `secrets/master.key` nếu thiếu). Cấu hình project test: `podman compose -p manga-translator-test --env-file /dev/null -f compose.yaml -f compose.test.yaml config`. |
| `make build` / `make up` / `make down` / `make ps` / `make logs` | Vòng đời ứng dụng. `down` giữ volume dữ liệu. |
| `make test` | Build + chạy service `tests` trong project `manga-translator-test` (không nạp `.env`, không mount `secrets/master.key`, khóa thử nghiệm inline), rồi xóa container/volume test (`down --volumes --remove-orphans`). |
| `make secrets` | Tạo master key (48 byte ngẫu nhiên, base64). Chỉ `config/build/up` cần; `test` không. |

Biến: `ENGINE=auto|docker|podman`, `OCR_EXTRAS=full|none`, `COMPAT_SME_VM=0|1` (xem dưới), `WEB_PORT`,
`WORKER_MEMORY_LIMIT`, các `MT_*` trong `.env`. Makefile chỉ đọc đúng dòng `COMPAT_SME_VM=` của `.env`, không in
hay dùng giá trị nào khác từ đó.

## VM Apple Silicon (SME không có SVE)

Podman machine `applehv` trên M4 (và một số VM Apple Virtualization khác) khai báo `sme` nhưng không `sve` trong
`/proc/cpuinfo`. Wheel `cryptography>=47` (OpenSSL tĩnh dò SME) và `onnxruntime>=1.31` (kernel KleidiAI/SME)
chạy lệnh SME/SVE và chết SIGILL (exit 132). Không có bản phát hành nào (tính đến 11/2026: cryptography 50.0.2,
onnxruntime 1.31.1) sửa trường hợp "SME nhưng không SVE" trong VM, và onnxruntime không có biến môi trường tắt
kernel SME lúc chạy (chỉ có cờ build `--no_kleidiai`).

Hành vi của Makefile:

- `make doctor` in kết quả dò (`Podman VM CPU: ...`), giá trị `COMPAT_SME_VM` và nguồn (`default` / `.env` /
  `explicit`), kèm khuyến nghị.
- `make build/up/test` **từ chối** khi dò thấy lỗi mà `COMPAT_SME_VM` chưa được đặt; không bao giờ tự chuyển
  sang nhánh pin tương thích.
- `COMPAT_SME_VM=1 make <target>` (hoặc `COMPAT_SME_VM=1` trong `.env`): build với
  `backend/requirements-compat-sme-vm.txt` — cryptography 46.0.7 (4 advisory: GHSA-537c-gmf6-5ccf,
  GHSA-jwv3-5hgf-82ww, GHSA-g6cj-pr64-35w5 mức HIGH; GHSA-m2h6-j472-rp4c MODERATE; chỉ vá ở ≥48.0.1/49/50, các bản
  đó lại SIGILL trên VM này) và onnxruntime 1.30.0 (không có advisory). Ứng dụng chỉ dùng Fernet nên các đường mã
  bị ảnh hưởng (X.509 verifier, PKCS#7, TLS qua OpenSSL của cryptography) không được gọi; rủi ro còn lại chấp
  nhận cho máy phát triển. **Giới hạn triển khai:** image build với `COMPAT_SME_VM=1` chỉ dùng cục bộ, không
  push/deploy; CI audit nhánh này mỗi lần chạy và fail khi xuất hiện advisory mới chưa được ghi nhận.
- `COMPAT_SME_VM=0` tường minh: giữ pin mặc định, Makefile chỉ cảnh báo (sẽ SIGILL trên VM lỗi).

Cách tránh pin hoàn toàn (ưu tiên theo thứ tự):

1. **Tắt SME trong kernel guest** (chưa kiểm chứng trên máy này, được xác nhận trong các báo cáo tương tự):
   `podman machine ssh sudo rpm-ostree kargs --append=arm64.nosme` rồi `podman machine stop && podman machine
   start`. Sau đó `/proc/cpuinfo` không còn `sme`, mọi wheel mặc định chạy bình thường; `make doctor` sẽ báo không
   phát hiện lỗi. Lưu ý: khởi động lại machine dừng mọi container khác trong VM.
2. Chạy trên máy thật (Linux arm64/x86_64) hoặc Docker Desktop ≥ 4.39 (bản sửa việc VM khai báo thừa extension).
3. Chỉ cho cryptography (không giải quyết onnxruntime): `OPENSSL_armcap=0x7d` trong môi trường container bỏ qua
   bước dò SME của OpenSSL nhưng vẫn giữ NEON/AES/SHA; chưa áp dụng trong compose vì không đủ để bỏ pin onnxruntime.
4. Provider `libkrun` cho Podman machine: chưa xác nhận là ẩn SME; có báo cáo treo khi boot ≥2 vCPU trên M4 Pro.

## Volume

- `data`: SQLite + `projects/<id>/{original,work,render}/<page-id>.*`. Xóa dự án trong UI sẽ xóa thư mục.
- `models`: cache PaddleX (`/models/paddlex`), HuggingFace (`/models/huggingface`), LaMa (`/models/lama/lama_fp32.onnx`, tải bằng `make lama-model`) + `models.lock.json`.
  Nếu worker báo `checksum mismatch`, model đã bị thay đổi: xóa file đó (hoặc toàn bộ volume) để tải lại.
- `state`: heartbeat của worker (healthcheck và `/api/health.worker_alive`).

## Khôi phục

- Worker khởi động lại: job `running` được xếp lại hàng đợi; quá `MT_JOB_MAX_ATTEMPTS` (3) thì đánh dấu `failed`.
- Trang lỗi không chặn lô; chạy lại từng trang/bước từ editor (`POST /api/pages/{id}/retry`).
- Đổi/xóa API key: trang Nhà cung cấp. Mất `secrets/master.key`: xóa key cũ và nhập lại.

## GPU (tùy chọn, chưa kiểm thử)

Mặc định CPU. Với NVIDIA + Docker: thêm overlay riêng cấp `--gpus` cho `worker`, cài `onnxruntime-gpu` thay
`onnxruntime` và đặt `providers: ["CUDAExecutionProvider"]` trong `app/ocr/paddle.py`. Không bật trong file chính.
