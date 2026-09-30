## Telegram notifications

Project can send simple text notifications via Telegram when a `PERSON_DETECTED` event is emitted.

Setup:

1. Create a Telegram bot with BotFather and obtain the bot token. Do not commit this token.
2. Get the target chat id (your user id or a group id) — you can use helper bots like `@get_id_bot` or inspect `getUpdates`.
3. Copy `.env.example` to `.env` and set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

Example `.env` entries (do not commit):

```
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TELEGRAM_CHAT_ID=987654321
```

Behavior:

- When a `PERSON_DETECTED` event is emitted (one per appearance), the application sends a single text message to the configured chat.
- When a `PERSON_DETECTED` event is emitted the application saves a snapshot and sends it to the configured Telegram chat as a photo (`sendPhoto`) with a short caption.

### Running the AI pipeline (no web)

This repository focuses on the AI pipeline: video input (MP4/Webcam/RTSP), YOLO person detection, ByteTrack tracking, event emission, snapshot saving, and Telegram notifications.

Run the pipeline directly with:

```bash
python main.py --source 0 --track
```

Examples:

- MP4 file:

```bash
python main.py --source data/videos/test.mp4
```

- Webcam:

```bash
python main.py --source 0 --track
```

- RTSP stream:

```bash
python main.py --source "rtsp://username:password@192.168.1.100:554/stream" --track
```

- Headless (no video window, for running on a server/NAS with no display):

```bash
python main.py --source "rtsp://username:password@192.168.1.100:554/stream" --track --headless
```

- Desktop window (Tkinter):

```bash
python main.py --gui
python main.py --gui --source 0 --track     # điền sẵn nguồn / tracking / confidence
```

The application will log device selection, FPS overlay, and `PERSON_DETECTED` events to the terminal. Snapshots are saved to `data/snapshots/` when configured and Telegram notifications are sent if enabled in `.env`.

The app shuts down gracefully (releases the camera, closes any window) on both Ctrl+C and `SIGTERM`, so it can be run as a systemd service or Docker container and stopped with `systemctl stop` / `docker stop` without a hard kill.

### Snapshot storage management

When snapshots are enabled (`ENABLE_SNAPSHOT=true`), the application automatically deletes old snapshots to prevent disk exhaustion. Configuration:

- `SNAPSHOT_RETENTION_DAYS` (default: 7) — keep snapshots newer than this many days; delete older ones.
- `SNAPSHOT_CLEANUP_INTERVAL` (default: 100) — run cleanup scan every N newly saved snapshots (avoids excessive disk scans during high activity).

Cleanup is non-blocking: if deletion fails (e.g., file already deleted by external process), errors are logged and the pipeline continues unaffected.

## Hướng dẫn cài đặt

1. Clone repository về máy.
2. Tùy chọn tạo virtual environment.
3. Cài đặt dependencies nếu có trong các giai đoạn sau:

```bash
pip install -r requirements.txt
```

4. Sao chép biến môi trường mẫu nếu cần:

```bash
cp .env.example .env
```

## Hướng dẫn video test

Đặt file `.mp4` vào `data/videos/`, ví dụ `data/videos/test.mp4`.

## Hướng dẫn camera

Webcam mặc định là thiết bị `0`, nhưng bạn có thể truyền `1`, `2`, ... nếu máy có nhiều camera.

RTSP / IP Camera:

- You can provide an RTSP URL as the `--source` argument, for example:

```
python main.py --source "rtsp://username:password@192.168.1.100:554/stream"
```

- Alternatively, set `RTSP_URL` in your `.env` file and run without `--source` to use the configured RTSP stream by default.

- The application will attempt to open the stream via OpenCV's `VideoCapture`. If it fails to connect, an error will be printed and the program will exit cleanly. Streams are always released on exit.
- The application will attempt to open the stream via OpenCV's `VideoCapture`.
- For RTSP streams the player will automatically attempt to reconnect when the connection drops.
  - Retry delay: 5 seconds between attempts.
  - Logs: `[CAMERA] Connection lost`, `[CAMERA] Reconnecting...`, `[CAMERA] Connected`.
- If it fails to connect initially the program will retry (RTSP only). Streams are always released on exit.

## Hướng dẫn chạy

MP4 file:

```bash
python main.py --source data/videos/test.mp4
```

Webcam:

```bash
python main.py --source 0
```

Tùy chọn confidence threshold:

```bash
python main.py --source 0 --conf-threshold 0.5
```

Tracking bằng ByteTrack:

```bash
python main.py --source 0 --track
```

Logic event `PERSON_DETECTED`:

- Mỗi `track_id` chỉ tạo 1 event trong một lần xuất hiện.
- Chỉ khi `track_id` biến mất khỏi camera, chờ quá `absence_timeout = 10s`, rồi xuất hiện lại mới tạo event mới.
- Event được log ra terminal và vào file (xem phần "Ghi log") theo định dạng `[PERSON_DETECTED] ID=3 | confidence=0.91 | time=22:30:15`.

Khi chạy, ứng dụng sẽ in device đang dùng và hiển thị FPS trực tiếp trên video.

Nhấn `q` trong cửa sổ video để thoát.

Nếu không truyền `--source`, ứng dụng chỉ in thông báo khởi động và thoát.

## Giao diện desktop (GUI)

```bash
python main.py --gui
```

Cửa sổ Tkinter gồm:

- **Nguồn**: chọn `Webcam` (nhập chỉ số, mặc định `0`), `File video` (nút "Chọn file…") hoặc `RTSP / IP camera`
  (nhập URL; nếu đã đặt `RTSP_URL` trong `.env` thì được điền sẵn).
- **Bật tracking (ByteTrack)**: bật để có ID cho từng người và sự kiện `PERSON_DETECTED` (snapshot/Telegram
  hoạt động như chế độ CLI).
- **Confidence**: thanh trượt 0.10–0.95, đổi được ngay khi đang chạy.
- **Bắt đầu / Dừng**: nguồn và tracking bị khóa trong lúc chạy; muốn đổi thì nhấn Dừng trước. Lần đầu nhấn
  Bắt đầu, thanh trạng thái hiện "Đang tải model YOLO…" — cửa sổ vẫn thao tác được.
- **Panel sự kiện** (bên phải): mỗi khi có người mới xuất hiện (`PERSON_DETECTED`) sẽ thêm một dòng
  gồm giờ, ID, confidence và tên file snapshot; dòng mới nhất nằm trên cùng, giữ tối đa 200 dòng, nút
  "Xóa danh sách" để dọn. Chỉ có sự kiện khi bật tracking. Bảng vẫn giữ nguyên sau khi Dừng.
- Lỗi mở nguồn / nạp model hiện thành hộp thoại thay vì làm thoát ứng dụng; video kết thúc thì tự về trạng thái sẵn sàng.

Yêu cầu máy có màn hình và `tkinter` (`sudo apt install python3-tk` trên Debian/Ubuntu; đã kèm sẵn với Python
trên Windows/macOS). `--gui` không dùng chung với `--headless`. Đóng cửa sổ, Ctrl+C hoặc `SIGTERM` đều tắt sạch.

## Ghi log

Log được ghi ra terminal và đồng thời ra file xoay vòng (`RotatingFileHandler`), mặc định tại
`logs/app.log` — cần thiết khi chạy headless/như service vì terminal không còn ai xem trực tiếp.

Cấu hình qua `.env`:

```
LOG_LEVEL=INFO           # DEBUG, INFO, WARNING, ERROR
LOG_FILE=logs/app.log    # đường dẫn file log
LOG_MAX_BYTES=5242880    # dung lượng tối đa trước khi xoay vòng (mặc định 5MB)
LOG_BACKUP_COUNT=3       # số file log cũ giữ lại
```

## Chạy unit test

Repo dùng `pytest` (đã có trong `requirements.txt`). Từ thư mục gốc dự án:

```bash
pytest
```

Bộ test gồm:

- `tests/test_person_detected_event_manager.py` — logic chống trùng lặp sự kiện `PERSON_DETECTED`.
- `tests/test_player.py` — vòng lặp video (`stop_event`, `on_frame`).
- `tests/test_gui_helpers.py`, `tests/test_gui_worker.py` — phần logic của GUI (kiểm tra nguồn, chuyển frame, dòng sự kiện, thread nền).
- `tests/test_person_tracker_events.py` — callback sự kiện của `PersonTracker` (dùng detector giả, không nạp YOLO).

Tất cả dùng video tổng hợp do fixture tạo ra, không cần camera, model YOLO, mạng hay màn hình.

## Kiểm tra CUDA

Bạn có thể kiểm tra PyTorch có nhận CUDA bằng lệnh sau:

```bash
python -c "import torch; print(torch.cuda.is_available())"
```

Nếu kết quả là `True`, ứng dụng sẽ tự động dùng `cuda:0` cho YOLO inference.
