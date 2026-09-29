# CLAUDE.md

File này cung cấp hướng dẫn cho Claude Code (claude.ai/code) khi làm việc với mã nguồn trong repository này.

## Tổng quan dự án

Một pipeline giám sát nhà thông minh dùng AI (không có lớp web/API — phần đó đã bị loại bỏ). Ứng dụng đọc
một nguồn video (file MP4, webcam, hoặc luồng RTSP), chạy YOLOv8 để phát hiện người (tùy chọn kèm theo
tracking bằng ByteTrack), hiển thị các frame đã được annotate trong cửa sổ OpenCV, và khi có người mới
xuất hiện sẽ phát ra sự kiện `PERSON_DETECTED` — sự kiện này có thể lưu snapshot xuống đĩa và/hoặc gửi
thông báo kèm ảnh qua Telegram.

## Các lệnh thường dùng

Cài đặt:
```bash
pip install -r requirements.txt
cp .env.example .env   # sau đó điền TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID, v.v.
```

Chạy pipeline:
```bash
python main.py --source data/videos/test.mp4      # file MP4
python main.py --source 0 --track                 # webcam, index 0, kèm tracking ByteTrack
python main.py --source "rtsp://user:pass@host:554/stream" --track   # luồng RTSP
python main.py --source 0 --conf-threshold 0.5     # ghi đè ngưỡng confidence
python main.py --source 0 --track --headless        # chạy không mở cửa sổ video (server/NAS không có màn hình)
python main.py                                     # không truyền --source: dùng RTSP_URL từ .env nếu có, ngược lại in hướng dẫn sử dụng rồi thoát
```
Nhấn `q` trong cửa sổ video để thoát. Hiện repo chưa có test suite (`tests/` chỉ có file `.gitkeep`);
thay vào đó có một script kiểm tra thủ công:
```bash
python scripts/test_telegram_snapshot.py   # ghi một snapshot giả và thử gửi qua Telegram
```

Kiểm tra CUDA có sẵn không (detector tự động chọn `cuda:0` nếu có, ngược lại dùng `cpu`):
```bash
python -c "import torch; print(torch.cuda.is_available())"
```

## Kiến trúc

`main.py` là entry point duy nhất: nó parse các tham số dòng lệnh, lấy config từ `src/config`, khởi tạo
hoặc `PersonDetector` (chỉ phát hiện) hoặc `PersonTracker` (phát hiện + tracking + sự kiện, dùng khi có
`--track`), rồi truyền phương thức `annotate_frame` của nó cho `play_video_source` làm callback xử lý
từng frame. Trước khi chạy, nó đăng ký handler cho `SIGTERM` (`_handle_termination_signal`) để chuyển
tín hiệu này thành `KeyboardInterrupt` — nhờ vậy `python main.py ... &` chạy như service (systemd/Docker)
khi bị `stop`/`kill` (không phải `kill -9`) sẽ tắt sạch giống hệt lúc nhấn Ctrl+C, thay vì bị giết giữa
chừng.

Luồng xử lý: `src/video/player.py` (`play_video_source`) đảm nhiệm vòng lặp capture của OpenCV, hiển thị
FPS, và xử lý cửa sổ/thoát. Tham số `display` (mặc định `True`) điều khiển việc có mở cửa sổ `cv2.imshow`
hay không — đặt `False` (qua flag `--headless` của `main.py`) để chạy nền không cần màn hình; khi đó chỉ
có thể thoát bằng Ctrl+C, hết luồng video, hoặc tín hiệu dừng tiến trình. Nó gọi `frame_processor` được
inject vào cho mỗi frame, và tự xử lý việc kết
nối lại RTSP (thử lại mỗi `RECONNECT_DELAY` giây, log `[CAMERA] Connection lost` /
`Reconnecting...` / `Connected`); với file cục bộ/webcam thì không có logic kết nối lại — hết luồng là
dừng vòng lặp.

- `src/detection/person_detector.py` — `PersonDetector` bọc một model Ultralytics `YOLO` chỉ giới hạn ở
  lớp `person` (`person_class_id = 0`). `_run_inference` gọi `model.predict`, hoặc khi `track=True` thì
  gọi `model.track(..., tracker="bytetrack.yaml", persist=True)`. `_annotate_results` vẽ khung lên bản sao
  của frame.
- `src/tracking/person_tracker.py` — `PersonTracker` kết hợp một `PersonDetector` (luôn chạy inference với
  `track=True`) cùng với `PersonDetectedEventManager`. Mỗi frame nó trích xuất các cặp `(track_id,
  confidence)` từ các box đã tracking, đưa cho event manager xử lý, và với mỗi sự kiện mới phát sinh sẽ
  tùy chọn ghi snapshot JPEG vào `SNAPSHOT_DIR` và/hoặc gửi ảnh qua Telegram bằng `TelegramNotifier`. Lỗi
  khi lưu snapshot/gửi Telegram đều được bắt và log lại, không bao giờ raise ra ngoài — lỗi thông báo hay
  lỗi ghi đĩa không được phép làm gián đoạn vòng lặp video.
- `src/events/person_detected.py` — `PersonDetectedEventManager` là state machine chống trùng lặp sự
  kiện: nó lưu `last_seen` theo từng `track_id` và chỉ phát sự kiện `PersonDetectedEvent` mới khi một
  `track_id` xuất hiện lần đầu hoặc đã vắng mặt lâu hơn `absence_timeout` giây. Các track cũ không còn
  active sẽ bị dọn dẹp sau mỗi lần gọi. Đây là logic thuần túy (pure), có thể test được, không có I/O.
- `src/notifications/telegram_notifier.py` — `TelegramNotifier` gọi trực tiếp Telegram Bot API qua
  `requests` (`sendMessage` / `sendPhoto`). An toàn khi khởi tạo kể cả lúc chưa cấu hình — nó chỉ đặt
  `self.enabled = False` và mọi phương thức gửi trở thành no-op, trả về `False`. Không bao giờ raise; bắt
  `RequestException`/`OSError` rồi log lại.
- `src/config/__init__.py` — nguồn cấu hình duy nhất (single source of truth) lấy từ biến môi trường. Tải
  `.env` qua `python-dotenv` nếu có, cung cấp các hằng số cấp module đã được định kiểu (`RTSP_URL`,
  `CONFIDENCE_THRESHOLD`, `ABSENCE_TIMEOUT`, `ENABLE_TELEGRAM`, `ENABLE_SNAPSHOT`, `SNAPSHOT_DIR`,
  `LOG_LEVEL`, token/chat id của Telegram). Mọi module khác import giá trị config từ đây thay vì đọc trực
  tiếp `os.environ`.
- `src/logging_config.py` — `setup_logging()` cấu hình root logger một lần duy nhất (tránh thêm handler
  trùng lặp); `main.py` là nơi duy nhất gọi hàm này. Các module khác chỉ dùng
  `logging.getLogger(__name__)`.

`src/backend/` (stub FastAPI rỗng, trước đây bị loại bỏ theo ghi chú "web frameworks removed" trong
`requirements.txt`) và `src/state/` (chỉ còn `__pycache__` cũ, không có mã nguồn) đã được xoá hẳn khỏi
repo — không còn tồn tại trong `src/`.

`data/snapshots/` và `data/videos/` bị gitignore trừ file `.gitkeep`; video test cục bộ đặt trong
`data/videos/`.
