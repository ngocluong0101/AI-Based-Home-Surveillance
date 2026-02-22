# AI-Based-Home-Surveillance

Một hệ thống giám sát gia đình sử dụng mô hình nhận diện đối tượng (YOLOv8) để phát hiện xâm nhập, gửi cảnh báo và lưu trữ sự kiện. Dự án kết hợp xử lý ảnh thời gian thực, logic an ninh, và một API nhẹ để truy vấn sự kiện và hình ảnh.

## Mục tiêu

- Giám sát khu vực bằng camera, phát hiện con người và chuyển động đáng ngờ.
- Ghi lại và lưu trữ sự kiện (ảnh, timestamp, loại sự kiện).
- Gửi cảnh báo (ví dụ: Telegram) khi phát hiện xâm nhập theo quy tắc an ninh.
- Cung cấp API để truy vấn sự kiện và hình ảnh cho giao diện frontend hoặc ứng dụng di động.

## Tính năng chính

- Phát hiện thời gian thực bằng YOLOv8 (`yolov8n.pt`).
- Lọc và áp dụng `security_rules` để quyết định khi nào gửi cảnh báo.
- Ghi log sự kiện vào `logs/events.csv` và `logs/events.json`.
- Mô-đun cảnh báo qua Telegram (`ai-model/alert/telegram_alert.py`).
- API Flask nhỏ trong `server/` để truy vấn và phục vụ ảnh/sự kiện.

## Cấu trúc dự án

- README.md
- requirements.txt
- test.py
- yolov8n.pt (mô hình detection)
- ai-model/
  - main.py (entry cho xử lý camera hoặc ảnh)
  - detect_image.py (hàm phát hiện ảnh tĩnh)
  - alert/telegram_alert.py (gửi cảnh báo Telegram)
  - detection/detector.py (wrapper model và inference)
  - logic/security_rules.py (quy tắc xác định xâm nhập)
  - logs/ (kết quả và sự kiện)
- camera/webcam.py (đọc luồng webcam)
- server/
  - app.py (Flask app)
  - routes/events.py (API sự kiện)
  - routes/images.py (API ảnh)
  - services/event_service.py (xử lý truy vấn sự kiện)
- utils/
  - cooldown.py (giảm tần suất cảnh báo)
  - event_logger.py (ghi file CSV/JSON)
  - logger.py (logging chung)

## Yêu cầu

- Python 3.8+
- GPU (tùy chọn, giúp nhanh hơn) hoặc CPU
- Các thư viện trong `requirements.txt` (OpenCV, Ultralytics/YOLOv8, Flask, requests, pandas, v.v.)

Cài đặt ví dụ (sử dụng virtualenv):

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Nếu bạn muốn dùng GPU, đảm bảo đã cài CUDA tương thích và phiên bản `torch` phù hợp.

## Cấu hình nhanh

- Mô hình: `yolov8n.pt` nằm tại gốc dự án. Thay bằng mô hình khác nếu cần.
- Cấu hình Telegram: chỉnh token/chat id trong `ai-model/alert/telegram_alert.py` hoặc đọc từ biến môi trường.
- Tham số cooldown / sensitivity: chỉnh trong `utils/cooldown.py` và `ai-model/logic/security_rules.py`.

## Chạy hệ thống (ví dụ cơ bản)

1. Kích hoạt virtualenv và cài dependencies như trên.
2. Chạy mô-đun xử lý camera (ví dụ sử dụng webcam):

```bash
python ai-model/main.py
```

3. Hoặc thử với ảnh mẫu:

```bash
python ai-model/detect_image.py --image test_images/sample.jpg
```

4. Khởi chạy API server để truy vấn sự kiện/hình ảnh (nếu cần):

```bash
python server/app.py
```

Server mặc định sẽ lắng nghe trên port cấu hình trong `server/app.py` (mặc định thường là 5000).

## API (tổng quan)

- `GET /events` — trả về danh sách sự kiện (JSON). (Xem `server/routes/events.py`)
- `GET /images/<image_id>` — phục vụ ảnh đã lưu. (Xem `server/routes/images.py`)

Chi tiết tham số và ví dụ request có trong mã nguồn `server/`.

## Logging & Lưu trữ

- Sự kiện được ghi vào `ai-model/logs/events.csv` và `ai-model/logs/events.json` cùng với đường dẫn ảnh, thời gian và nhãn phát hiện.
- Ảnh sự kiện được lưu trong thư mục con của `ai-model/logs/` (xem code để biết cấu trúc chính xác).

## Cách mở rộng

- Thêm bộ lọc/luật trong `ai-model/logic/security_rules.py` để tùy chỉnh khi nào kích hoạt cảnh báo.
- Hỗ trợ nhiều camera: mở rộng `camera/webcam.py` để đọc nhiều luồng và đẩy vào pipeline phát hiện.
- Thay phương thức cảnh báo: thêm module mới trong `ai-model/alert/` (email, SMS, webhook...).
- Nâng cấp model: thay `yolov8n.pt` bằng mô hình lớn hơn hoặc fine-tune trên dữ liệu riêng.

## Vận hành & Khắc phục sự cố

- Nếu model không load: kiểm tra `yolov8`/`torch` tương thích và đường dẫn `yolov8n.pt`.
- CPU chậm: chuyển sang GPU hoặc sử dụng mô hình nhẹ hơn (yolov8n).
- Không nhận được cảnh báo Telegram: kiểm tra token/chat id và kết nối mạng.
- Kiểm tra logs runtime với `utils/logger.py` — bật debug để biết thêm chi tiết.

## Quy trình phát triển

- Thêm test nhỏ cho các module trong `utils/` và `ai-model/detection/`.
- Sử dụng `test.py` để thử nhanh một luồng kiểm tra.

## Một số lệnh hữu dụng

```bash
# Kiểm tra Python và установленные packages
python --version
pip list

# Chạy unit / smoke test (nếu có)
python test.py
```

## Contributing

- Mô tả ngắn: mở issue mô tả lỗi / tính năng, tạo PR kèm mô tả rõ ràng.
- Coding style: tuân thủ PEP8, giữ API backward-compatible.

## Bản quyền & Liên hệ

- Tài liệu: sửa đổi theo nhu cầu nội bộ.
- Liên hệ tác giả: (thêm email hoặc thông tin liên hệ của bạn vào đây).

---

Tệp README này cung cấp hướng dẫn nhanh để bắt đầu với hệ thống `AI-Based-Home-Surveillance`. Nếu bạn muốn tôi thêm phần hướng dẫn triển khai Docker, CI/CD, hoặc chi tiết endpoint API, hãy nói tôi biết.
