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
python main.py --gui                               # mở cửa sổ desktop Tkinter (chọn nguồn, tracking, confidence, Start/Stop trong GUI)
python main.py --gui --source 0 --track            # như trên, nhưng điền sẵn nguồn/tracking/confidence vào các control
```
Nhấn `q` trong cửa sổ video để thoát (chế độ CLI). `--gui` không dùng chung được với `--headless`.

Chạy unit test (pytest, cấu hình trong `pytest.ini` với `pythonpath = .`):
```bash
pytest                                                    # chạy toàn bộ test trong tests/
pytest tests/test_person_detected_event_manager.py -v     # chỉ test của event manager
```
Test hiện có: `PersonDetectedEventManager`, `play_video_source` (tham số `stop_event`/`on_frame`), các hàm
thuần của GUI (`src/gui/helpers.py`), `PipelineWorker` và `event_callback` của `PersonTracker` (thay
`PersonDetector` bằng bản giả nên không nạp YOLO). Tất cả chạy bằng video tổng hợp do fixture
`sample_video` (`tests/conftest.py`) tạo ra — không cần camera/YOLO/mạng/màn hình; riêng lớp Tk
(`src/gui/app.py`) không có unit test, kiểm tra bằng cách chạy thật `python main.py --gui`. Ngoài ra có
một script kiểm tra thủ công:
```bash
python scripts/test_telegram_snapshot.py   # ghi một snapshot giả và thử gửi qua Telegram
```

Kiểm tra CUDA có sẵn không (detector tự động chọn `cuda:0` nếu có, ngược lại dùng `cpu`):
```bash
python -c "import torch; print(torch.cuda.is_available())"
```

Log được ghi cả ra terminal và ra file xoay vòng tại `logs/app.log` (cấu hình qua `LOG_FILE`,
`LOG_MAX_BYTES`, `LOG_BACKUP_COUNT` trong `.env`).

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
dừng vòng lặp. Hai tham số tùy chọn dành cho GUI (mặc định `None`, CLI không dùng): `stop_event`
(`threading.Event`; khi được set, mọi vòng lặp — kể cả chờ kết nối lại RTSP — thoát sạch và trả `0`) và
`on_frame` (callback nhận từng frame đã xử lý, sau lớp phủ FPS).

Chế độ GUI (`python main.py --gui`): `main.py` import lười `src.gui.app.run_gui` (máy server không có
tkinter/màn hình vẫn chạy CLI bình thường), gọi `setup_logging` rồi mở cửa sổ; `--source/--track/
--conf-threshold` chỉ điền sẵn vào các control. Toàn bộ pipeline chạy ở thread nền, Tk chỉ chạy ở main thread:
- `src/gui/worker.py` — `PipelineWorker` chạy `play_video_source(display=False, stop_event=..., on_frame=...)`
  trên daemon thread. Model YOLO được nạp trong thread (không đơ UI). Frame mới nhất nằm trong
  `queue.Queue(maxsize=1)` (frame cũ bị bỏ, UI chậm không tạo backlog). Expose `state` (`idle/loading/running/
  stopped`), `error`, `exit_code`; mọi lỗi (nạp model, mở nguồn, pipeline crash) được bắt và lưu vào `error`,
  không raise. `set_confidence()` đổi ngưỡng ngay từ frame kế tiếp (gán `confidence_threshold` của detector —
  hoặc `tracker.detector`); nguồn video và bật/tắt tracking chỉ có hiệu lực lúc Start (khóa control khi chạy).
  `processor_factory` được inject để test không cần YOLO. Sự kiện: nếu processor có thuộc tính
  `event_callback` (chỉ `PersonTracker`), worker gắn `_publish_event` vào đó; mỗi event `PERSON_DETECTED` +
  đường dẫn snapshot (hoặc `None`) được đẩy vào `worker.events` (`queue.Queue` không giới hạn, không bỏ sự kiện
  nào — khác với `frames`).
- `src/gui/app.py` — `SurveillanceApp` (cửa sổ chính: combobox loại nguồn Webcam/File/RTSP + ô nhập + nút chọn
  file, checkbox tracking, thanh confidence, Start/Stop, canvas video, panel sự kiện bên phải, status bar) và
  `run_gui`. Poll `worker` mỗi 30 ms bằng `root.after`: hiển thị frame, rút cạn `worker.events` vào `ttk.Treeview`
  (cột Giờ/Người/Conf/Snapshot, mới nhất ở trên, tối đa `MAX_EVENT_ROWS=200` dòng, nút "Xóa danh sách"; bảng
  giữ nguyên sau khi Stop/hết video). Khi worker `stopped` thì bật lại control, hiện hộp thoại nếu có `error`.
  Đóng cửa sổ sẽ dừng worker (join tối đa 3 giây). `report_callback_exception` được ghi đè để
  `KeyboardInterrupt` (SIGTERM) trong callback vẫn dẫn tới tắt sạch thay vì bị Tk nuốt.
- `src/gui/helpers.py` — hàm thuần không phụ thuộc màn hình: `classify_source`, `validate_source` (thông báo lỗi
  tiếng Việt cho người dùng), `format_event_row` (event → các ô của bảng sự kiện), `frame_to_ppm` (resize giữ tỉ lệ + encode PPM để `tk.PhotoImage` đọc trực tiếp,
  không cần Pillow).

- `src/detection/person_detector.py` — `PersonDetector` bọc một model Ultralytics `YOLO` chỉ giới hạn ở
  lớp `person` (`person_class_id = 0`). `_run_inference` gọi `model.predict`, hoặc khi `track=True` thì
  gọi `model.track(..., tracker="bytetrack.yaml", persist=True)`. `_annotate_results` vẽ khung lên bản sao
  của frame.
- `src/tracking/person_tracker.py` — `PersonTracker` kết hợp một `PersonDetector` (luôn chạy inference với
  `track=True`) cùng với `PersonDetectedEventManager`. Mỗi frame nó trích xuất các cặp `(track_id,
  confidence)` từ các box đã tracking, đưa cho event manager xử lý, và với mỗi sự kiện mới phát sinh sẽ
  tùy chọn ghi snapshot JPEG vào `SNAPSHOT_DIR`, gọi `event_callback(event, snapshot_path)` nếu được cung cấp
  (GUI dùng để cập nhật panel; gọi trước Telegram vì Telegram chặn mạng; `snapshot_path` là `None` khi tắt
  snapshot hoặc lưu lỗi), rồi tùy chọn gửi ảnh qua Telegram bằng `TelegramNotifier`. Lỗi khi lưu snapshot/
  gọi callback/gửi Telegram đều được bắt và log lại, không bao giờ raise ra ngoài — lỗi thông báo hay
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
  `SNAPSHOT_RETENTION_DAYS`, `SNAPSHOT_CLEANUP_INTERVAL`, `LOG_LEVEL`, `LOG_FILE`, `LOG_MAX_BYTES`,
  `LOG_BACKUP_COUNT`, token/chat id của Telegram). Mọi module khác import giá trị config từ đây thay vì
  đọc trực tiếp `os.environ`.
- `src/logging_config.py` — `setup_logging()` cấu hình root logger một lần duy nhất (tránh thêm handler
  trùng lặp); `main.py` là nơi duy nhất gọi hàm này. Gắn cả `StreamHandler` (terminal) và
  `RotatingFileHandler` (file `LOG_FILE`, mặc định `logs/app.log`, xoay vòng theo `LOG_MAX_BYTES`/
  `LOG_BACKUP_COUNT`) — nếu tạo file log thất bại (vd. không có quyền ghi) thì fallback về chỉ log ra
  terminal, không làm crash ứng dụng. Các module khác chỉ dùng `logging.getLogger(__name__)`.
- `src/snapshot_management.py` — `SnapshotCleaner` quản lý dung lượng snapshot để tránh tràn đĩa: quét
  `SNAPSHOT_DIR` cho các file `.jpg` cũ hơn `SNAPSHOT_RETENTION_DAYS` (mặc định 7 ngày) và xoá chúng.
  `PersonTracker` khởi tạo một instance, gọi `cleanup()` mỗi lần N snapshot được lưu (thông qua
  `SNAPSHOT_CLEANUP_INTERVAL`, mặc định 100) để tránh quét đĩa quá thường xuyên. Lỗi xoá file được bắt
  và log; không bao giờ interrupt video pipeline.

`src/backend/` (stub FastAPI rỗng, trước đây bị loại bỏ theo ghi chú "web frameworks removed" trong
`requirements.txt`) và `src/state/` (chỉ còn `__pycache__` cũ, không có mã nguồn) đã được xoá hẳn khỏi
repo — không còn tồn tại trong `src/`.

`data/snapshots/`, `data/videos/` và `logs/` đều bị gitignore trừ file `.gitkeep`; video test cục bộ đặt
trong `data/videos/`.
