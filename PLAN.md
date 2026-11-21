# Kế hoạch dự án Manga Translator

Ngày lập: 30/10/2026 · Cập nhật: 04/11/2026 (xem mục 9–10)

## 1. Mục tiêu và phạm vi

Xây dựng ứng dụng web chạy **local-first trong container** để dịch trang manga tiếng Nhật và truyện tranh tiếng Trung (Giản thể/Phồn thể). Người dùng tải một ảnh hoặc nhiều ảnh cùng lúc, xem vùng chữ nhận dạng được, sửa OCR và bản dịch, rồi xuất ảnh có lớp chữ dịch hoặc dữ liệu văn bản. Ngôn ngữ đích mặc định là **tiếng Việt**, có thể đổi trong cài đặt dự án.

Ứng dụng phải xử lý chữ **dọc, ngang và hỗn hợp trên cùng trang**, giữ thứ tự đọc phù hợp (manga Nhật thường từ phải sang trái; truyện Trung do người dùng chọn hoặc ứng dụng đề xuất). Tự nhận diện ngôn ngữ nguồn ở cấp trang/vùng, nhưng luôn cho phép chỉnh tay vì chữ Nhật có kanji và chữ Trung dễ bị nhầm.

### Kết quả của bản đầu tiên (MVP)

- Nhập PNG, JPEG, WebP; chọn một hoặc nhiều ảnh, sắp xếp lại thứ tự trang; xử lý một trang riêng lẻ hoặc toàn bộ lô.
- Hiển thị tiến độ theo trang và từng bước: kiểm tra ảnh → phát hiện vùng chữ → OCR → dịch → dựng kết quả. Một trang lỗi không làm hỏng các trang còn lại; có thể chạy lại đúng trang hoặc đúng bước.
- Hiển thị khung vùng chữ, chiều chữ, thứ tự đọc, văn bản gốc, độ tin cậy OCR và bản dịch. Cho phép thêm/xóa/chỉnh vùng, sửa OCR và dịch trước khi xuất.
- Chọn dịch bằng **Google Cloud Translation API** hoặc model AI: **OpenAI Responses API**, **Anthropic Messages API**, hoặc **endpoint tùy chỉnh tương thích Responses API**. Người dùng nhập model ID và cấu hình riêng cho từng provider; kiểm tra kết nối trước khi chạy lô.
- Xuất ảnh PNG với lớp chữ dịch có thể định vị/chỉnh cỡ; xuất JSON/TXT chứa OCR, bản dịch và tọa độ; tải nhiều trang dưới dạng ZIP: ZIP đầy đủ (ảnh + JSON + TXT), chỉ ảnh đã dịch, toàn bộ ảnh của dự án (trang chưa dịch kèm ảnh gốc), hoặc các trang được chọn; tải PNG từng trang. Luôn giữ ảnh gốc. Bản MVP che chữ cũ bằng nền phẳng trong vùng thoại đơn giản trước khi phủ bản dịch; người dùng chỉnh mặt nạ/nền bằng tay khi cần. Chưa cam kết tẩy chữ trên nền tranh phức tạp.
- Từ điển tên riêng/thuật ngữ theo dự án; với AI đưa vào ngữ cảnh dịch, với Google chỉ dùng glossary của Cloud Translation Advanced nếu đã cấu hình, hoặc áp dụng đối chiếu thuật ngữ có kiểm tra sau dịch.

### Ngoài MVP

Tẩy chữ/inpainting và sắp chữ tự động theo hình bóng thoại; CBZ/PDF đầu vào; chia sẻ nhiều người dùng; triển khai cloud. Chỉ thực hiện sau khi OCR và luồng chỉnh sửa ổn định. Không dùng API Google Translate không chính thức hoặc scraping giao diện web.

## 2. Quyết định công nghệ

| Thành phần | Lựa chọn | Lý do |
|---|---|---|
| Xử lý ảnh, OCR, API | **Python 3.11**, FastAPI, Pydantic | Hệ sinh thái OCR/ML trưởng thành; kiểm tra tương thích wheel và model khi tạo lockfile. |
| Giao diện | **TypeScript + React** | Canvas chỉnh vùng chữ, điều phối nhiều trang và trạng thái công việc thuận tiện; TypeScript giảm lỗi dữ liệu giữa UI/API. |
| Ảnh | Pillow + OpenCV | Đọc/chuẩn hóa ảnh và cắt vùng, xoay, tăng tương phản khi cần. |
| OCR chính | **PaddleOCR PP-OCRv6** (bản medium/server tùy tài nguyên) | Một pipeline phát hiện và nhận dạng Nhật, Trung Giản thể/Phồn thể, kể cả trường hợp chữ dọc; lấy polygon và điểm tin cậy. Chốt phiên bản sau benchmark. |
| OCR bổ sung cho Nhật | **manga-ocr** trên crop được phát hiện | Được thiết kế cho chữ manga Nhật dọc/ngang, furigana và nhiều dòng; chỉ chạy khi vùng tiếng Nhật có điểm thấp hoặc người dùng yêu cầu. Không dùng một mình để phát hiện tọa độ. |
| Dịch | Adapter Google Cloud Translation v3, OpenAI Responses, Anthropic Messages, Responses-compatible | Cùng một hợp đồng nội bộ, dễ thay provider và test bằng mock. |
| Lưu trữ MVP | SQLite + thư mục dữ liệu cục bộ ngoài source tree | Đơn giản cho một máy; metadata trong DB, ảnh gốc/kết quả trên đĩa. Chỉ nâng lên PostgreSQL/Redis khi cần nhiều người dùng hoặc nhiều worker. |
| Công việc nền | Worker tiến trình riêng với hàng đợi bền trong SQLite | Không chặn request, có trạng thái/retry/idempotency; giới hạn số job OCR/AI đồng thời. |
| Đóng gói và vận hành | OCI image + Compose, điều khiển bằng `Makefile` | Cùng một cách build/chạy/test trên Docker Compose hoặc Podman Compose; Docker được ưu tiên nếu cả hai khả dụng. |

**Lưu ý lựa chọn OCR:** Thông số benchmark do nhà phát triển model công bố không thay thế thử nghiệm trên bộ ảnh của dự án. Dùng PP-OCRv6 làm baseline, so sánh PP-OCRv5 và manga-ocr trên vùng Nhật khó (furigana, chữ phủ tranh, scan mờ) trước khi mặc định bật nhánh bổ sung. Kiểm tra giấy phép **mã và trọng số model** ở phiên bản ghim; không sao chép trực tiếp mã từ dự án `manga-image-translator` (GPL-3.0) nếu không quyết định phát hành phù hợp.

### Thiết kế container và lệnh vận hành

- Giai đoạn 1 tạo `compose.yaml` cho `api`, `worker`, `web` và volume dữ liệu/model. `api` và `worker` dùng cùng image backend nhưng lệnh chạy riêng; `web` phục vụ giao diện đã build. Chỉ `web` được publish cổng host `127.0.0.1:8080`; `api` và `worker` ở mạng Compose nội bộ. Container API lắng nghe `0.0.0.0` **bên trong** mạng đó. Có healthcheck cho API và web; tiến trình worker có kiểm tra sống riêng.
- Tạo `compose.test.yaml` với service `tests` chạy toàn bộ unit, integration và smoke/e2e test qua mạng Compose, dùng mock provider và dữ liệu mẫu không nhạy cảm. Test chạy bằng project Compose riêng (`manga-translator-test`) và volume riêng; sau test xóa container/volume test, giữ nguyên dữ liệu ứng dụng. Không cần Docker/Podman trên máy phát triển ngoài engine được chọn.
- `Makefile` trong thư mục dự án cung cấp `make doctor`, `make config`, `make build`, `make up`, `make down`, `make ps`, `make logs`, `make test`. `ENGINE=auto` chọn `docker compose` trước, rồi `podman compose`, rồi `podman-compose` nếu có Podman. Có thể ép `ENGINE=docker` hoặc `ENGINE=podman`; báo lỗi rõ khi thiếu Compose/engine. `make test` thất bại khi service `tests` thất bại và luôn dọn project test.
- Dockerfile nhiều giai đoạn, ghim phiên bản base image/digest và dependency lock; chạy bằng UID không phải root. Không nhúng API key, source test hoặc dữ liệu người dùng vào image. Cache model qua volume có kiểm tra checksum, không tải lại mỗi lần khởi động. Mặc định CPU để chạy được trên Docker/Podman thông thường; GPU là cấu hình tùy chọn riêng sau khi kiểm tra thiết bị/driver.
- Kiểm thử tính tương thích trên Docker Compose và Podman Compose ở môi trường có sẵn; tránh các tính năng chỉ một engine hỗ trợ trong file chính. CI gọi `make test` và ghi nhận engine/phiên bản đã dùng. `make config` dùng để xác nhận cấu hình Compose trước khi chạy.

## 3. Luồng xử lý ảnh và văn bản

```text
Ảnh tải lên
  → kiểm tra định dạng, kích thước, số pixel; chuẩn hóa orientation/màu
  → phát hiện polygon chữ trên toàn trang
  → nhóm thành dòng/vùng thoại; ước lượng dọc/ngang; sắp thứ tự đọc
  → OCR từng crop (PaddleOCR; manga-ocr bổ sung cho vùng Nhật khó)
  → xác định Nhật / Trung Giản thể / Trung Phồn thể; gắn cờ không chắc chắn
  → người dùng duyệt/sửa OCR nếu muốn
  → dịch theo provider đã chọn, theo nhóm ngữ cảnh nhỏ nhưng giữ ID từng vùng
  → kiểm tra đầu ra, cho phép sửa bản dịch
  → dựng lớp chữ dịch và xuất ảnh + JSON/TXT
```

- **Phát hiện vùng:** giữ polygon, không chỉ bounding box. Chuẩn hóa ảnh và thử xoay crop 90°/270° **chỉ khi** bộ phân loại chiều chữ hoặc độ tin cậy cho thấy cần; không xoay mù cả trang. Nhóm nét chữ thành dòng/vùng thoại, tách furigana khỏi nội dung chính khi có thể; hiển thị vùng không chắc chắn để sửa tay.
- **Thứ tự đọc:** dựa trên hướng trang và vị trí nhóm thoại, sau đó thứ tự cột trong vùng (chữ dọc: phải → trái; trong cột: trên → dưới) hoặc dòng (chữ ngang: trên → dưới; trong dòng theo ngôn ngữ). Quy tắc hình học chỉ là đề xuất; người dùng có thể kéo đổi thứ tự.
- **Nhận diện ngôn ngữ:** xét script/Unicode và OCR ở nhiều crop; phân biệt tiếng Nhật với Trung bằng kana khi có, nhưng không suy luận chỉ từ một câu toàn kanji/hanzi. Mặc định hỏi chỉnh tay đối với trang hỗn hợp hoặc không chắc chắn; lưu override theo trang/vùng.
- **Dịch AI:** gửi văn bản đã OCR, ngữ cảnh trang lân cận và glossary nếu người dùng bật. Yêu cầu đầu ra có `{region_id, translated_text}` theo JSON Schema khi endpoint hỗ trợ, xác thực đủ ID/không trùng, giữ nguyên tiếng lóng/tên riêng theo cấu hình, đánh dấu câu thiếu ngữ cảnh. Không gửi ảnh gốc cho model dịch mặc định; chế độ gửi crop ảnh để giải nghĩa là tùy chọn riêng và phải hiển thị tác động riêng tư/chi phí.
- **Dịch Google:** gọi Cloud Translation v3 `translateText` với mã ngôn ngữ rõ ràng, giới hạn kích thước request và ánh xạ kết quả về đúng vùng. Dùng glossary của Advanced khi có. Không giả định Google tự giữ ngữ cảnh hội thoại như model AI.
- **Dựng ảnh:** với bóng thoại nền phẳng, tạo mặt nạ có biên an toàn và tô nền phù hợp trước khi đặt chữ dịch; cho phép sửa mặt nạ hoặc dùng hộp nền thủ công trên vùng phức tạp. Chữ dịch là lớp phủ chỉnh được; tính cỡ chữ theo vùng, wrap theo tiếng Việt, cảnh báo khi tràn khung. Ảnh xuất được render lại từ dữ liệu đã duyệt; ảnh gốc không bị ghi đè.

## 4. Hợp đồng dữ liệu và API dự kiến

Các thực thể: `Project` (ngôn ngữ đích, hướng đọc, glossary), `Page` (thứ tự, đường dẫn ảnh nội bộ, trạng thái), `Region` (polygon, hướng chữ, thứ tự, ngôn ngữ nguồn, OCR, confidence, bản dịch, trạng thái duyệt), `Job` (bước, tiến độ, lỗi đã khử nhạy cảm), `ProviderProfile` (provider, model ID, endpoint đã duyệt, tham chiếu bí mật).

Endpoint nội bộ dự kiến:

- `POST /api/projects`, `POST /api/projects/{id}/pages` (multipart, nhiều file), `GET /api/projects/{id}`.
- `POST /api/projects/{id}/jobs` với `scope=all|page`, `step=ocr|translate|render`, provider và tùy chọn tương ứng; trả `job_id`; `GET /api/jobs/{id}`; `POST /api/jobs/{id}/cancel`; job trả `started_at`/`finished_at` để UI hiển thị thời gian chạy.
- `PATCH /api/pages/{id}/regions/{region_id}` để sửa polygon/thứ tự/OCR/dịch; `POST /api/pages/{id}/retry`.
- `POST /api/providers/test` chỉ trả trạng thái, thông tin model có thể dùng và lỗi đã khử bí mật; `GET /api/projects/{id}/export?fmt=zip|json|txt|png&pages=<id,...>&include_original=0|1` để xuất dữ liệu/ZIP (`png` = ZIP chỉ ảnh; `pages` giới hạn trang; `include_original` thêm ảnh làm việc cho trang chưa dựng).

Mọi thao tác ghi dùng ID ngẫu nhiên, kiểm tra quyền sở hữu dự án và version field để tránh ghi đè khi nhiều tab cùng sửa. Kết quả OCR và dịch lưu tách biệt để sửa văn bản gốc không làm mất dữ liệu lịch sử; bản dịch được đánh dấu “cần dịch lại” khi OCR đổi.

## 5. Bảo mật và quyền riêng tư

### Mô hình đe dọa và biện pháp bắt buộc

1. **Ảnh độc hại / cạn tài nguyên:** chỉ nhận PNG/JPEG/WebP qua kiểm tra MIME bằng nội dung và decode thực tế; giới hạn dung lượng file, số trang, tổng pixel và thời gian xử lý; bắt lỗi ảnh giải nén quá lớn; loại metadata EXIF trước khi gửi ra ngoài hoặc xuất mặc định. Không nhận URL ảnh hoặc archive đầu vào ở MVP để giảm SSRF/zip bomb.
2. **Đường dẫn và file xuất:** lưu dưới ID ngẫu nhiên trong data directory; không dùng tên file do người dùng cung cấp làm path; chặn path traversal/symlink; xuất ZIP bằng tên đã chuẩn hóa; xóa file tạm trong `finally`; quyền file/thư mục chỉ cho tài khoản chạy ứng dụng.
3. **API key:** nhập qua backend; khi chạy container dùng secret file/mount chỉ đọc hoặc kho bí mật ngoài container, và nếu lưu lâu dài thì mã hóa bằng khóa master nằm ngoài DB/image/volume dữ liệu. Không đưa key vào frontend/localStorage, URL, log, image layer, Compose file hay JSON export. Có chức năng xóa/đổi key. Dùng service account có quyền tối thiểu cho Google Cloud Translation; không lưu file service-account JSON trong repo.
4. **Endpoint AI tùy chỉnh:** chỉ hỗ trợ HTTPS và đường dẫn Responses API đã xác định; kiểm tra allowlist host do người quản trị cấu hình, DNS/IP sau phân giải và sau redirect (mặc định tắt redirect), chặn localhost, link-local, private IP, metadata endpoint và scheme khác. Chế độ endpoint nội bộ chỉ bật bằng cấu hình quản trị riêng, không từ form người dùng. Giới hạn timeout, số byte phản hồi và kết nối đồng thời.
5. **Ứng dụng web:** chỉ publish cổng web trên `127.0.0.1` của host; API/worker không publish cổng. Nếu đưa lên mạng phải bật đăng nhập, TLS, phân quyền từng dự án, CSRF protection với cookie session, CORS chính xác, rate limit và giới hạn upload. Escape text trong UI/canvas, cấm HTML tùy ý từ OCR hoặc bản dịch, đặt Content Security Policy.
6. **Prompt injection / dữ liệu không đáng tin:** văn bản OCR và glossary người dùng là **dữ liệu**, không phải lệnh hệ thống; không cho model gọi công cụ hoặc truy cập file/mạng. Kiểm tra schema/ID/độ dài bản dịch, từ chối nội dung bất thường và hiển thị để người dùng duyệt; không tin kết quả model để tạo path, URL hay cấu hình.
7. **Quyền riêng tư:** OCR chạy local; chỉ văn bản/crop được chọn mới gửi nhà cung cấp dịch. UI báo rõ provider nào nhận dữ liệu và ước lượng chi phí trước khi chạy. Với OpenAI Responses đặt `store: false` khi API hỗ trợ; rà soát chính sách lưu trữ của từng provider theo cấu hình triển khai. Có tùy chọn xóa dự án và chính sách tự xóa file tạm; log chỉ metadata kỹ thuật, không log ảnh, OCR, bản dịch hoặc key.
8. **Chuỗi cung ứng:** ghim phiên bản thư viện và checksum model; tải model từ nguồn chính thức, lưu cache có kiểm soát, quét dependency/secret ở CI; không thực thi script từ ảnh, model hoặc endpoint; kiểm tra giấy phép thư viện, font và trọng số model trước phát hành.
9. **Ranh giới container:** chạy non-root, bỏ Linux capabilities không cần thiết, bật `no-new-privileges`, giới hạn CPU/RAM/PID và `tmpfs` cho file tạm; chỉ mount volume cần thiết, không mount Docker/Podman socket hoặc thư mục home của host. Root filesystem chỉ đọc khi code và model cho phép; volume dữ liệu/model có quyền tối thiểu. Không dùng `privileged` hoặc network host trong cấu hình mặc định.

### Kiểm thử bảo mật tối thiểu

File giả MIME, ảnh decompression bomb, file cực lớn, tên file traversal, ZIP export chứa tên độc hại, truy cập ID dự án khác, CSRF/CORS, SSRF qua DNS/redirect/IPv6, phản hồi provider quá lớn hoặc sai schema, prompt injection trong OCR, log có chuỗi trông như API key. Dùng mock provider trong CI để không gửi ảnh thật hay tiêu tiền.

## 6. Các giai đoạn triển khai

| Giai đoạn | Công việc | Tiêu chí hoàn thành |
|---|---|---|
| 0. Khảo sát mẫu | Tập ảnh có quyền sử dụng: ít nhất 100 trang gồm Nhật/Trung Giản thể/Phồn thể, chữ dọc/ngang, scan mờ và furigana; gán nhãn vùng + văn bản chuẩn. Đo tốc độ/RAM trên máy mục tiêu. | Có báo cáo recall phát hiện vùng, CER OCR theo nhóm, lỗi thứ tự đọc; chọn cấu hình OCR bằng số liệu. |
| 1. Nền tảng an toàn | Tạo monorepo `backend/`, `frontend/`, `tests/`; Dockerfile, `compose.yaml`, `compose.test.yaml`, cấu hình, DB migration, upload/preview và giới hạn tài nguyên; CI lint/typecheck/dependency scan. | `make up` chạy ứng dụng trong container; `make test` chạy test trong project Compose riêng; upload nhiều ảnh, lưu/khôi phục dự án; test file độc hại và đường dẫn đạt. |
| 2. OCR và biên tập | PaddleOCR, nhánh manga-ocr, xác định chiều chữ/ngôn ngữ, thứ tự đọc và editor sửa vùng/OCR. | Chạy từng ảnh/lô, có confidence và sửa tay; benchmark ở giai đoạn 0 được chạy lại sau thay đổi. |
| 3. Dịch đa provider | Adapter Google/OpenAI/Anthropic/Responses-compatible, cấu hình model, test kết nối, glossary, retry/backoff cho 429/5xx, giới hạn chi phí và idempotency. | Cùng bộ region dịch được bằng mọi provider cấu hình hợp lệ; lỗi một trang không mất trang khác; không lộ key. |
| 4. Xuất và hoàn thiện | Overlay chữ dịch, sửa kiểu chữ, export PNG/JSON/TXT/ZIP (ZIP đầy đủ / chỉ ảnh đã dịch / toàn bộ ảnh / trang đã chọn / từng trang), thời gian chạy job, chọn sẵn provider và model, tài liệu cài đặt và lưu dữ liệu. | Ảnh xuất đúng thứ tự, không ghi đè ảnh gốc, text không tràn âm thầm; chạy nghiệm thu end-to-end trên bộ mẫu. |
| 5. Sau MVP | Inpainting, CBZ/PDF, deployment nhiều người dùng. | Chỉ mở khi có yêu cầu và kiểm thử riêng cho định dạng/quyền truy cập mới. |

## 7. Tiêu chí nghiệm thu tổng thể

- Người dùng có thể dịch **một ảnh** hoặc **một lô**, sửa một trang rồi dịch lại riêng trang đó mà không chạy lại toàn bộ.
- Bộ mẫu có cả chữ dọc/ngang và ba nhóm ngôn ngữ nguồn; báo cáo được recall vùng, CER OCR, tỷ lệ đúng thứ tự đọc và lỗi dịch theo nhóm. Chốt ngưỡng số học sau giai đoạn 0 theo chất lượng ảnh thực tế, không tuyên bố “chính xác nhất” khi chưa benchmark.
- Mọi vùng có ID ổn định; số vùng dịch trả về phải khớp số vùng đầu vào hoặc hiện lỗi rõ ràng. Không có vùng bị bỏ qua âm thầm.
- Chạy lại OCR không làm mất chỉnh sửa đã duyệt; job dịch không ghi đè bản dịch được sửa trong lúc job chạy. Thay đổi OCR, bản dịch, mặt nạ, bố cục hoặc cấu hình dịch phải làm kết quả liên quan hết hiệu lực cho đến khi dịch/render lại.
- Mô phỏng timeout, 429, 5xx, mất mạng và hủy job; trạng thái job nhất quán sau khởi động lại. Có giới hạn đồng thời, chi phí và dung lượng.
- Job có trang thất bại phải hiển thị trạng thái lỗi một phần rõ ràng; không báo thành công hoàn toàn khi `failed > 0`.
- Kiểm thử bảo mật ở mục 5 đạt trước khi mở ứng dụng ra mạng. Khi chạy local, API key và nội dung ảnh không xuất hiện trong log, URL, export metadata hay source control.
- Quy trình build/chạy/test chỉ cần Docker Compose hoặc Podman Compose; khi cả hai có sẵn `make doctor` chọn Docker. `make test` không dùng volume, `.env` hoặc secret thật của ứng dụng và trả lỗi khi test thất bại.

## 8. Nguồn kỹ thuật tham khảo

- [PaddleOCR PP-OCRv6](https://www.paddleocr.ai/latest/en/version3.x/algorithm/PP-OCRv6/PP-OCRv6.html) và [hướng dẫn pipeline OCR](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/OCR.html).
- [Manga OCR của kha-white](https://github.com/kha-white/manga-ocr) (nhận dạng manga Nhật, chữ dọc/ngang, furigana).
- [Google Cloud Translation v3](https://docs.cloud.google.com/translate/docs/reference/rest) và [glossary](https://docs.cloud.google.com/translate/docs/samples/translate-v3-translate-text-with-glossary).
- [OpenAI Responses API và structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses); [tắt lưu trạng thái với `store: false`](https://developers.openai.com/api/docs/guides/migrate-to-responses?api-mode=responses).
- [Anthropic Messages API](https://platform.claude.com/docs/en/api/http/messages) và [structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs).
- [Giấy phép GPL-3.0 của manga-image-translator](https://github.com/zyddnys/manga-image-translator) để đánh giá tương thích trước khi tái sử dụng mã.
- [Docker Compose `run --rm`](https://docs.docker.com/reference/cli/docker/compose/run) và [Podman Compose provider](https://docs.podman.io/en/v6.0.0/markdown/podman-compose.1.html).

## 9. Nhật ký thay đổi so với kế hoạch ban đầu

| Ngày | Thay đổi | Lý do |
|---|---|---|
| 31/10/2026 | OCR chạy PaddleOCR 3.7 qua **onnxruntime** thay vì PaddlePaddle; `COMPAT_SME_VM` tự bật trên Podman applehv (M4) để tránh SIGILL của cryptography ≥47 / onnxruntime ≥1.31. | PaddlePaddle không có wheel Linux arm64; VM khai báo SME không có SVE. |
| 31/10/2026 | Phát hiện vùng đa tỉ lệ (1600 + 960, gộp theo IoU), giữ hộp rec trả rỗng, `MT_MANGA_OCR_MODE=auto\|always_ja\|off`. | Mặc định 960 bỏ sót kana nhỏ và chữ đậm trên trang 1,3k–2k px. |
| 31/10/2026 | Adapter Responses-compatible thêm `structured_output=auto` (tự lùi về JSON theo prompt), `timeout_seconds`, `reasoning_effort`, `max_output_tokens`. | Gateway model suy luận chỉ trả `reasoning` khi ép `text.format`, và cần timeout dài hơn 60 s. |
| 02/11/2026 | Export `fmt=png` + `pages` + `include_original`; UI chọn nhiều trang (checkbox, chọn tất cả/đã dịch), nút tải ảnh đã dịch / toàn bộ ảnh / ảnh đã chọn, tải PNG từng trang. | Người dùng cần lấy ảnh dịch hàng loạt mà không kèm JSON/TXT. |
| 02/11/2026 | Bảng công việc hiển thị thời gian chạy (đang chạy đếm tiếp, xong thì chốt). | Biết job hoàn thành trong bao lâu để cân chỉnh cấu hình. |
| 02/11/2026 | Provider đầu tiên trong danh sách được chọn sẵn ở trang dự án/editor; "Kiểm tra kết nối" điền model đầu tiên gateway trả về khi Model ID trống, kèm gợi ý danh sách model. | Bớt thao tác với cấu hình một provider. |
| 11/11/2026 | Giao diện theo sắc thái anime: nền đen, điểm nhấn xanh lá neon, speed-line, panel kiểu khung manga; logo mascot SVG (mèo tai, bong bóng 「訳」) ở header + favicon (`frontend/public/`, Dockerfile copy `public`, nginx phục vụ `logo.svg`/`favicon.svg`). Màu chọn vùng trên canvas đổi sang xanh lá. | Yêu cầu thẩm mỹ của người dùng. |
| 13/11/2026 | Dựng ảnh bước 2+3. Typography (`renderer.py`, `fonts.py`): font theo ngôn ngữ đích (Patrick Hand OFL vendored `backend/fonts/`, Noto CJK), cỡ chữ tối đa = cỡ glyph gốc × hệ số, hộp chữ = lòng bong bóng (flood-fill 4-liên thông trên ảnh gốc đã thay nét chữ bằng màu nền), halo chỉ khi nền phức tạp, chữ trắng trên nền tối, chữ dọc cho đích CJK. Mask xoá giới hạn trong bong bóng, nét viền được bảo vệ khỏi dilate. LaMa ONNX (`render/lama.py`, Carve/LaMa-ONNX big-lama fp32, Apache-2.0, sha256 pin, 512×512 cố định, cửa sổ ngữ cảnh ≥512 px, chỉ ghi lại pixel trong mask): `MT_RENDER_INPAINT_MODEL=auto|lama|telea`, `make lama-model` / `MT_LAMA_AUTO_DOWNLOAD`; nền phức tạp không có bong bóng → giao cả polygon OCR cho LaMa, thiếu LaMa → hộp trắng. Fallback Telea khi model lỗi. Test: `test_lama.py` (runner giả), `test_inpaint.py`. | Yêu cầu "ảnh dựng tự nhiên như gốc" bước 2+3. Hạn chế: LaMa trên hatching chéo tổng hợp cho mảng xám; cần đánh giá trên trang thật. |
| 13/11/2026 | Chữ đè tranh + LaMa tile. `inpaint.art_text_mask`: k-means 5 cụm màu trong polygon OCR + vành 0,5×cạnh ngắn; nhận cụm là chữ khi std ≤ 28, mật độ trong/ngoài ≥ 3, phủ 2–55 % polygon, bề rộng nét ≤ 0,35×cạnh ngắn, nhiều blob; chỉ chạy khi không tìm được bong bóng và nền không phẳng (`renderer._erase_over_art`, ngữ cảnh 0,75×cạnh ngắn). Bỏ đường "giao cả polygon cho LaMa". `lama.tile_plan`/`_inpaint_tiled`: tile 512 chồng 128, trọng số raised-cosine, ≤12 tile, ngược lại cửa sổ đơn (thu nhỏ). Tuỳ chọn dự án `settings.sfx_policy` = `keep` (mặc định) \| `white_box` (UI trang dự án "SFX đè tranh"); `render_report.kept`, cảnh báo `complex_background` có `action` = `inpainted`/`kept`/`white_box`. Bong bóng tìm được thì luôn snap nền và không báo nền phức tạp. Fixture hồi quy `tests/fixtures/render/` (ja_bubble, art_letters, art_solid + `make_fixtures.py`), `test_render_fixtures.py`, tile test trong `test_lama.py`. | Yêu cầu xử lý truyện 3D/webtoon chữ đè tranh không làm lem tranh; không đổi đường bong bóng manga Nhật. Chưa có: cọ mask thủ công (bước 3), đánh giá trên trang thật. |
| 13/11/2026 | Kiểm tra trên 10 trang webtoon thật (`example/junk-to-king-ep5`, OCR PaddleOCR + LaMa trong worker qua API): bong bóng thoại sạch; SFX nhiều lớp (SWOOSH/BLAST) ban đầu chỉ mất lớp tô, lem lớp viền; vầng sáng sau chữ bị nhận là chữ khi polygon OCR rộng. Sửa `inpaint.py`: k=8 cụm; loại cụm "mảng đặc" (`_is_patch`: blob lớn nhất ≥50 % cụm và phủ ≥45 % hộp bao); `_complete_cut_glyphs` lấy trọn glyph bị polygon cắt (blob cùng màu, phần thừa ≤ phần trong, không chạm rìa tầm với 0,6×glyph); `_absorb_outline_layers` hút lớp viền/bóng theo bề rộng đo bằng độ phủ vòng khoảng cách (`_layer_width`, ≥35 %), 2 lượt; `renderer._erase_over_art` nở lỗ 2–8 px theo cỡ glyph; `SNAP_TOLERANCE` 24→40 và snap cả vành 6 px quanh lỗ trên nền phẳng (`SNAP_HALO_PX`, xoá viền nhiễu JPEG quanh chữ gốc). Fixture mới `art_layered.png` + mask chuẩn `art_layered_mask.png`. Scripts kiểm tra ở scratchpad (không commit). | Yêu cầu "chữ không lem, không nhoè nền" trên webtoon. Còn lại: OCR cắt cụt chữ (CHAROS) chỉ xoá phần trong polygon + tầm với, cần sửa polygon trong editor; vùng OCR rác (ký tự đơn, vệt sáng) vẫn được dựng — đề xuất lọc theo độ tin cậy/độ dài ở bước OCR. |
| 11/11/2026 | Dựng ảnh bước 1: thay "tô hộp màu nền" bằng xoá nét chữ + inpaint (`backend/app/render/inpaint.py`): pixel khác màu nền > 70 → mask, bỏ blob dài chạm biên (viền bong bóng/khung), dilate 2 px, `cv2.inpaint` Telea; fallback hộp phẳng khi mask > 60 % ô (khối đặc), fallback numpy diffusion khi thiếu OpenCV. Công tắc `MT_RENDER_INPAINT` (mặc định 1), `render_report.inpainted`. Ảnh test thêm `opencv-python-headless` (ảnh full đã có opencv-contrib qua PaddleOCR). | Người dùng muốn ảnh dựng tự nhiên như gốc; đánh giá trước khi làm bước 2 (typography) và 3 (LaMa). |
| 11/11/2026 | Bảng công việc (panel "Chạy xử lý") phân trang 8 dòng/trang với pager (`JobList` prop `pageSize`, helper `paginate` trong `frontend/src/langs.ts`). Ngôn ngữ đích ở trang tạo dự án và trang dự án đổi từ ô nhập tay sang select: vi, en, ja (nhãn jp), zh-Hans (nhãn cn) — giữ mã ISO mà adapter Google/LLM đã hiểu; dự án có mã khác vẫn hiển thị mã hiện tại. Vitest 13 pass. | Yêu cầu UI của người dùng. |
| 05/11/2026 | Review lần 3: `final_lang` cho `final_text` nhập tay (migration 0005); đổi ngôn ngữ đích làm vùng chỉ có bản tay cũng hết hiệu lực; dịch lại không xóa cờ stale khi bản tay thuộc ngôn ngữ cũ (cờ `final_text_lang_mismatch`, nút "Dùng bản máy dịch"). | Ảnh xuất 200 với chữ tay tiếng Việt dù dự án đã đặt tiếng Anh. |
| 05/11/2026 | Sửa 3 điểm sau review lần 2 (mục 10, phần "Review lần 2"): cờ sửa tay theo trường cho polygon/chiều chữ/ngôn ngữ nguồn (migration 0004); đổi ngôn ngữ đích/glossary/ngôn ngữ nguồn làm render hết hiệu lực cùng lúc, export trả 409 khi bản dịch cũ trừ khi `allow_stale_translation=1` (kèm `X-Export-Warning`); fingerprint ngữ cảnh dịch trong `apply_translation` (cờ `context_changed_during_job`). | OCR lại vẫn có thể mất sửa hình học/ngôn ngữ; ảnh cũ vẫn tải được sau khi đổi cấu hình; job đang chạy xóa nhầm cờ stale. |
| 04/11/2026 | Hoàn thành mã cho 5 mục P1 và 4 mục P2 ở mục 10 (ghép OCR giữ sửa tay, ghi dịch có điều kiện version, theo dõi render/dịch cũ, cách ly secret test, giới hạn tổng upload, trạng thái job `partial` + chạy lại trang lỗi, refresh token Google, idempotency theo dự án, opt-in tường minh `COMPAT_SME_VM`). | Theo danh sách review mã 04/11/2026. |
| 04/11/2026 | Bổ sung danh sách sửa lỗi sau review mã ở mục 10, tách trạng thái đã viết mã khỏi trạng thái đã nghiệm thu. | Chưa đạt các điều kiện về giữ chỉnh sửa, tính mới của ảnh xuất, cách ly secret test và benchmark OCR. |

## 10. Việc cần sửa sau review mã (04/11/2026)

Trạng thái 04/11/2026: toàn bộ P1 và P2 đã viết mã kèm test hồi quy (xem ghi chú ✅ từng mục); phần kiểm chứng xem cuối mục. Xử lý P1 trước khi coi bản local MVP là sẵn sàng; P2 và kiểm chứng cuối phải xong trước khi chốt nghiệm thu. Mỗi mục sửa mã cần có test hồi quy chạy qua `make test` trong Compose, ngoài các kiểm tra thủ công được nêu rõ.

### P1 — Ngăn mất dữ liệu và cô lập môi trường test

- [x] **Giữ chỉnh sửa khi chạy lại OCR** (`backend/app/repo.py`, `backend/app/pipeline.py`): thay cơ chế xóa tất cả vùng `origin='auto'` và cấp ID mới bằng ghép kết quả OCR với vùng hiện có. Giữ ID, văn bản đã sửa, bản dịch đã sửa, trạng thái duyệt, thứ tự đọc, mặt nạ và hộp chữ của vùng khớp; vùng mới nhận ID mới. Vùng không khớp có chỉnh sửa phải được giữ để người dùng quyết định, không xóa âm thầm. **Đạt khi:** chạy OCR lại một trang đã chỉnh sửa không làm mất các chỉnh sửa đó; test bao gồm vùng khớp, vùng mới và vùng cũ không còn được OCR phát hiện.
  ✅ 04/11/2026 — `region_merge.plan_merge` ghép theo IoU/centroid, giữ ID/sửa tay; vùng cũ có sửa nhưng OCR không thấy được giữ với cờ `ocr_missing`. Test: `test_edit_safety.py::test_rerun_ocr_*`, `test_region_merge.py`.
- [x] **Chặn job dịch ghi đè thao tác sửa tay đồng thời** (`backend/app/translate/service.py`, `backend/app/repo.py`): trước khi ghi kết quả provider, đọc lại trạng thái/version trong transaction hoặc dùng cập nhật có điều kiện. Nếu người dùng sửa vùng sau khi job bắt đầu, giữ `final_text` của người dùng và xử lý xung đột/trạng thái bản dịch một cách tường minh. **Đạt khi:** test cho provider trả lời chậm, người dùng sửa `final_text` trong lúc chờ, rồi job hoàn thành mà bản sửa vẫn còn.
  ✅ 04/11/2026 — `repo.apply_translation` so `version` trong transaction; giữ `final_text` người dùng, thêm cờ `user_edited_during_job`. Test: `test_translate_job_does_not_overwrite_edits_made_while_running`.
- [x] **Theo dõi dữ liệu dịch/render đã cũ** (`backend/app/routers/pages.py`, `backend/app/routers/projects.py`, `backend/app/export.py`): đổi OCR/ngôn ngữ nguồn/ngôn ngữ đích/glossary phải đánh dấu bản dịch cần cập nhật; đổi bản dịch, polygon, mặt nạ, hộp chữ hoặc cỡ chữ phải đánh dấu ảnh render cần cập nhật. `has_render` không được đồng nghĩa với ảnh còn hợp lệ. Khi xuất PNG/ZIP, render lại từ dữ liệu hiện tại hoặc từ chối xuất với lỗi rõ ràng. **Đạt khi:** sửa chữ hoặc bố cục sau lần render đầu thì PNG/ZIP không thể trả về ảnh cũ; ảnh gốc không bị ghi đè.
  ✅ 04/11/2026 — migration 0002 thêm `pages.render_stale`; `has_render` = có ảnh **và** còn mới; `render.png`/ZIP/PNG tự dựng lại trang cũ hoặc trả 409. Test: `test_stale_render_*`, `test_layout_and_text_edits_mark_render_stale`, `test_language_and_glossary_changes_mark_translations_stale`.
- [x] **Tách hoàn toàn bí mật của test Compose** (`compose.yaml`, `compose.test.yaml`, `Makefile`): `api`/`worker` trong project test không được kế thừa `.env` hay mount `secrets/master.key` thật; dùng cấu hình và khóa dành riêng cho test. Bỏ phụ thuộc tạo khóa thật khỏi `make test`; giữ volume test tách biệt và dọn sau khi chạy. **Đạt khi:** cấu hình Compose đã merge của project test không có đường dẫn secret thật hoặc `env_file` ứng dụng; test vẫn chạy khi chưa có `secrets/master.key`.
  ✅ 04/11/2026 — `compose.test.yaml` reset `env_file`/`secrets`; Makefile dùng `--env-file /dev/null` cho project test, `test` không còn phụ thuộc `secrets`. Config merge không chứa `master.key`/`.env`.
- [x] **Giới hạn tổng upload theo tài nguyên thực tế** (`frontend/nginx.conf`, `backend/app/main.py`, `backend/app/routers/projects.py`, `compose.yaml`): giới hạn tổng request và dữ liệu multipart ngay khi nhận, trước khi parser có thể chiếm quá `/tmp`; điều chỉnh mức tối đa số trang, kích thước file, tmpfs/RAM cho nhất quán. **Đạt khi:** lô sát ngưỡng hợp lệ xử lý được trong container; lô vượt ngưỡng bị từ chối có kiểm soát, không làm API hết chỗ hoặc ngừng hoạt động.
  ✅ 04/11/2026 — `MT_MAX_UPLOAD_TOTAL_BYTES`=256 MiB = nginx `client_max_body_size` ≤ tmpfs api 512m < RAM 768M; middleware ASGI đếm byte (chunked/sai Content-Length) → 413; parser dừng ở part N+1. Test: `test_upload_total_limit_*`, `test_upload_too_many_parts_*`, `test_body_limit_middleware_*`.

### P2 — Trạng thái job, provider và chuỗi cung ứng

- [x] **Hiển thị job lỗi một phần** (`backend/app/pipeline.py`, `backend/app/worker/runner.py`, `frontend/src/components/JobPanel.tsx`): thêm trạng thái `partial` hoặc tương đương khi có trang lỗi nhưng các trang khác xong; tiến độ và danh sách trang lỗi phải rõ ràng, hỗ trợ chạy lại đúng trang. **Đạt khi:** lô có một trang lỗi không hiển thị như hoàn thành thành công; test API/UI xác nhận trạng thái mới.
  ✅ 04/11/2026 — trạng thái `partial`, `progress.failed_pages`, `POST /api/jobs/{id}/retry-failed` (chạy lại đúng trang lỗi qua `options.page_ids`); UI badge cảnh báo + nút "Chạy lại trang lỗi". Test: `test_partial_job_lists_failed_pages_and_retries_only_them`, vitest `jobs.test.ts`.
- [x] **Làm mới Google access token trong job dài** (`backend/app/translate/google.py`): giữ credential và kiểm tra thời hạn token trước mỗi yêu cầu, làm mới trước khi hết hạn; đóng HTTP response sau khi đọc. **Đạt khi:** test giả lập token hết hạn trong một job nhiều trang vẫn dịch tiếp được, không gửi lại token cũ.
  ✅ 04/11/2026 — giữ credentials, refresh trước mỗi request khi hết hạn/sắp hết (60 s), có lock; response đóng bằng context manager. Test: `test_google_token.py` (9 test).
- [x] **Giới hạn idempotency key theo dự án và yêu cầu** (`backend/app/repo.py`, `backend/app/migrations/`, `backend/app/routers/jobs.py`): cùng key chỉ trả lại đúng job của cùng dự án và cùng scope/page/step/options; dùng lại key với nội dung khác trả lỗi xung đột. **Đạt khi:** cùng key ở hai dự án không làm lộ ID/trạng thái job của nhau; retry cùng yêu cầu không tạo job trùng.
  ✅ 04/11/2026 — migration 0003: unique `(project_id, idempotency_key)` + `request_fingerprint` (sha256 JSON chuẩn hóa); khác fingerprint → 409. Test: `test_idempotency.py` (6 test).
- [x] **Kiểm soát nhánh dependency tương thích SME VM** (`backend/requirements-compat-sme-vm.txt`, `Makefile`, `.github/workflows/ci.yml`): đánh giá advisory của các phiên bản đang ghim, tìm phiên bản hoặc môi trường chạy không cần pin có advisory; audit cả nhánh build tương thích. Nếu chưa có bản an toàn, yêu cầu bật nhánh này một cách tường minh và ghi rõ giới hạn triển khai thay vì âm thầm tự chọn. **Đạt khi:** CI phát hiện advisory trên mọi nhánh dependency được hỗ trợ và build mặc định không tự chuyển sang nhánh chưa được chấp nhận.
  ✅ 04/11/2026 — không có bản vá SME-không-SVE; `COMPAT_SME_VM` phải bật tường minh (Makefile từ chối khi auto-detect mà chưa chọn), CI pip-audit nhánh compat với 4 GHSA được chấp nhận có ghi lý do (cryptography 46.0.7: GHSA-537c-gmf6-5ccf, GHSA-jwv3-5hgf-82ww, GHSA-g6cj-pr64-35w5, GHSA-m2h6-j472-rp4c; app chỉ dùng Fernet). Chi tiết docs/OPERATIONS.md.

### Review lần 2 (05/11/2026)

- [x] **Chạy lại OCR làm mất sửa hình học/ngôn ngữ**: thêm `polygon_edited`/`direction_edited`/`source_lang_edited` (migration 0004), chỉ bật khi giá trị thực sự đổi; vùng khớp giữ các trường đã sửa, `has_user_edits` nhận cả ba cờ nên vùng không khớp được giữ + `ocr_missing`. Test: `test_manual_field_edits.py` (7 test), `test_region_merge.py`.
- [x] **Đổi ngôn ngữ đích/glossary vẫn xuất ảnh cũ**: mọi thao tác đặt `translation_stale` cũng đặt `render_stale` trong cùng transaction (project lang/glossary, override ngôn ngữ trang, sửa `source_lang`/`source_text` vùng đã dịch). Quy ước xuất: trang có bản dịch cũ → ZIP/PNG trả 409 liệt kê trang; `allow_stale_translation=1` thì dựng lại và xuất kèm header `X-Export-Warning`; `render.png` xem trước vẫn phục vụ. UI hiện "Bản dịch đã cũ" và hỏi xác nhận trước khi tải. Test: `test_stale_translation_export.py` (3 test).
- [x] **Job dịch xóa cờ stale sau khi đổi cấu hình**: fingerprint ngữ cảnh (target_lang, glossary, override ngôn ngữ trang) chụp khi lập lô; `apply_translation` đọc lại trong transaction, lệch thì giữ `translation_stale=1` + cờ `context_changed_during_job`; lệch `source_lang` vùng → `user_edited_during_job`. Test: `test_translation_context.py` (5 test).

- [x] **Review lần 3 (05/11/2026) — `final_text` nhập tay không gắn với ngôn ngữ đích**: (a) vùng chỉ có `final_text` tay (không có `translated_text`) bị bỏ qua khi đổi ngôn ngữ đích; (b) sau khi đổi ngôn ngữ và dịch lại, vùng đã sửa tay giữ chữ cũ nhưng cờ stale bị xóa nên ảnh xuất 200 với chữ sai ngôn ngữ. Sửa: migration 0005 thêm `regions.final_lang` (backfill từ `target_lang` dự án); `mark_project_translations_stale`/`apply_page_source_lang` coi `final_text<>''` là có bản dịch; `apply_translation` giữ `final_text` tay nhưng nếu `final_lang` ≠ ngôn ngữ đích hiện tại thì giữ `translation_stale=1` + cờ `final_text_lang_mismatch` (export vẫn 409); PATCH `final_text` ghi `final_lang` = ngôn ngữ đích hiện tại và xóa cờ/stale khi bản tay trước đó thuộc ngôn ngữ khác; editor hiện cảnh báo kèm nút "Dùng bản máy dịch". Test: `test_final_text_lang.py` (4 test).

Thay đổi hành vi cần biết: sửa `source_text` của vùng đã có bản dịch nay cũng làm render hết hiệu lực (test `test_layout_and_text_edits_mark_render_stale` được tách bước tương ứng).

### Kiểm chứng trước khi đánh dấu hoàn thành

- [ ] **Benchmark OCR giai đoạn 0:** bổ sung bộ ảnh có quyền sử dụng theo mục 6 và báo cáo recall vùng, CER, thứ tự đọc, tốc độ/RAM theo Nhật/Trung Giản thể/Phồn thể và chữ dọc/ngang. Chỉ chốt model OCR mặc định và tuyên bố chất lượng sau khi có số liệu.
- [x] **Nghiệm thu bằng container** (04/11/2026, Podman 5.2.0 + docker-compose 2.36.2 qua `podman compose`, chỉ có Podman nên không kiểm được ưu tiên Docker; VM applehv M4 cần `COMPAT_SME_VM=1`): `make doctor` báo SME/SVE và yêu cầu chọn tường minh; `COMPAT_SME_VM=1 make test` → 103 test pass (04/11), 120 test pass sau review lần 2, 124 sau review lần 3 (05/11), 129 sau bước inpaint dựng ảnh (11/11), 135 sau bước typography + LaMa (13/11), 148 sau chữ đè tranh + LaMa tile + chỉnh theo webtoon thật (13/11) trong project `manga-translator-test` (exit 0, không dùng `.env`/`master.key`); `COMPAT_SME_VM=1 make up` → api/worker/web healthy, migration 1–5 áp dụng, export PNG 200, upload vượt ngưỡng 413. Giới hạn còn lại: nhánh compat mang 4 advisory cryptography (chỉ dùng cục bộ), benchmark giai đoạn 0 chưa có. Mục cũ: chạy `make doctor`, `make config`, `make test` và luồng dịch một ảnh/lô trên Docker Compose hoặc Podman Compose thực tế; nếu có cả hai thì xác nhận Docker được ưu tiên. Ghi engine/phiên bản, kết quả test và giới hạn còn lại. Lần review 04/11/2026 mới xác nhận frontend có 5 test đạt và build thành công; backend/Compose chưa chạy được trong môi trường review vì thiếu `pytest` trên host và không có Compose engine khả dụng. Không coi trạng thái “hoàn thành về mã” trong README là đã nghiệm thu.
