## Giới thiệu

Hệ thống giám sát nhà thông minh dùng AI. Ứng dụng đọc video từ file MP4, webcam hoặc camera RTSP/IP, dùng YOLOv8
để phát hiện người (kèm ByteTrack để theo dõi từng người). Khi có người mới xuất hiện, ứng dụng lưu snapshot và
gửi cảnh báo qua Telegram. Có hai cách chạy: dòng lệnh (CLI) và giao diện desktop (GUI).

## Hướng dẫn cài đặt

1. Clone repository về máy.
2. Tùy chọn: tạo virtual environment.
3. Cài đặt dependencies:

```bash
pip install -r requirements.txt
```

4. Sao chép file cấu hình mẫu:

```bash
cp .env.example .env
```

5. Trên Debian/Ubuntu, nếu muốn dùng GUI: `sudo apt install python3-tk`. Trên Windows và macOS, Tkinter đã có sẵn cùng Python.

Lần chạy đầu tiên, Ultralytics sẽ tự tải model `yolov8n.pt`, nên cần có mạng.

## Kiểm tra CUDA

```bash
python -c "import torch; print(torch.cuda.is_available())"
```

Nếu kết quả là `True`, ứng dụng sẽ dùng `cuda:0` cho YOLO. Nếu không, ứng dụng dùng CPU.

## Chuẩn bị nguồn video

- **File video**: đặt file `.mp4` vào `data/videos/`, ví dụ `data/videos/test.mp4`.
- **Webcam**: mặc định là thiết bị `0`. Nếu máy có nhiều camera, dùng `1`, `2`, ...
- **Camera RTSP/IP**: truyền URL qua `--source`, hoặc đặt `RTSP_URL` trong `.env` rồi chạy mà không cần `--source`.

  ```
  python main.py --source "rtsp://username:password@192.168.1.100:554/stream"
  ```

  Khi mất kết nối, ứng dụng tự thử lại sau mỗi 5 giây (`RECONNECT_DELAY`). Log sẽ có các dòng `[CAMERA] Connection lost`, `[CAMERA] Reconnecting...`, `[CAMERA] Connected`. Nếu lần kết nối đầu tiên thất bại, ứng dụng cũng thử lại như vậy.

## Chạy bằng dòng lệnh (CLI)

Chạy với file video:

```bash
python main.py --source data/videos/test.mp4
```

Chạy với webcam, có tracking:

```bash
python main.py --source 0 --track
```

Chạy với camera RTSP, có tracking:

```bash
python main.py --source "rtsp://username:password@192.168.1.100:554/stream" --track
```

Chạy không mở cửa sổ video (dành cho server/NAS không có màn hình):

```bash
python main.py --source "rtsp://username:password@192.168.1.100:554/stream" --track --headless
```

Đổi ngưỡng confidence:

```bash
python main.py --source 0 --conf-threshold 0.5
```

Không truyền `--source`: ứng dụng dùng `RTSP_URL` trong `.env` nếu có, nếu không thì chỉ in hướng dẫn và thoát.

Nhấn `q` trong cửa sổ video để thoát. Trong khi chạy, terminal hiển thị thiết bị đang dùng, FPS trên video, và các sự kiện `PERSON_DETECTED`.

## Giao diện desktop (GUI)

```bash
python gui.py                          # mở cửa sổ
python gui.py --source 0 --track       # điền sẵn nguồn và bật tracking
python gui.py --conf-threshold 0.6     # điền sẵn ngưỡng confidence
```

`gui.py` là điểm vào dành riêng cho GUI, tương đương `python main.py --gui`. Các tham số khác được truyền nguyên vẹn.
Không dùng `--gui` cùng `--headless`.

Các nhãn, thông báo và hộp thoại trong cửa sổ hiển thị bằng tiếng Anh. Cửa sổ có ba tab:

- **Thanh điều khiển (phía trên)**
  - **Source**: chọn `Webcam` (nhập chỉ số, mặc định `0`), `File video` (nút "Browse…" để chọn file) hoặc `RTSP / IP camera` (nhập URL). Nếu đã đặt `RTSP_URL` trong `.env` thì URL được điền sẵn.
  - **Enable tracking (ByteTrack)**: bật để mỗi người có một ID và để có sự kiện `PERSON_DETECTED`. Tắt thì chỉ phát hiện người.
  - **Confidence**: thanh trượt từ 0.10 đến 0.95. Đổi được ngay khi đang chạy.
  - **Start / Stop**: khi đang chạy, Source và tracking bị khóa. Muốn đổi thì nhấn Stop trước. Lần đầu nhấn Start, thanh trạng thái hiện "Loading YOLO model…", cửa sổ vẫn thao tác được.

- **Tab Live**
  - Khung video ở giữa, có khung và ID cho từng người.
  - Bảng "PERSON_DETECTED events" bên phải: mỗi lần có người mới xuất hiện sẽ thêm một dòng gồm giờ (Time), ID (Person), confidence (Conf) và tên file snapshot (Snapshot). Dòng mới nhất nằm trên cùng, tối đa 200 dòng. Nút "Clear list" xóa bảng. Bảng vẫn giữ nội dung sau khi Stop. Chỉ có sự kiện khi bật tracking.

- **Tab Snapshots**
  - Danh sách ảnh trong `SNAPSHOT_DIR` (mặc định `data/snapshots/`), mới nhất ở trên, kèm thời gian lưu và dung lượng.
  - Chọn một dòng để xem ảnh lớn ngay trong cửa sổ. Ảnh được co vừa khung xem, không mở cửa sổ riêng.
  - Nút "Refresh" quét lại thư mục. Tab cũng tự quét khi được mở.
  - Nếu snapshot đã bị dọn tự động, ứng dụng hiện thông báo và làm mới danh sách.

- **Tab Settings**
  - Bật/tắt Telegram, nhập `TELEGRAM_BOT_TOKEN` và `TELEGRAM_CHAT_ID` ngay trong app, không cần sửa `.env` bằng tay.
  - Token được che mặc định. Tick "Show" để xem.
  - "Save to .env" ghi giá trị vào `.env` và giữ nguyên các dòng và comment khác. "Reload from .env" đọc lại từ file.
  - Khi bật Telegram, phải có đủ token và chat ID.
  - **Lưu ý:** thay đổi chỉ có hiệu lực sau khi khởi động lại app. File `.env` được ghi với quyền `0600` vì chứa token.

- **Thanh trạng thái (dưới cùng)**: hiển thị `Ready`, `Loading YOLO model…`, `Running (tracking) — ...`, `Stopping…`, `Stopped`, `Video finished` hoặc `Error`.

Lỗi khi mở nguồn hoặc nạp model sẽ hiện thành hộp thoại, không làm thoát ứng dụng.

## Logic sự kiện PERSON_DETECTED

- Mỗi `track_id` chỉ tạo một sự kiện trong một lần xuất hiện.
- Khi `track_id` biến mất khỏi camera quá `ABSENCE_TIMEOUT` (mặc định 10 giây) rồi xuất hiện lại, hệ thống tạo sự kiện mới.
- Sự kiện được ghi ra terminal và file log, theo định dạng `[PERSON_DETECTED] ID=3 | confidence=0.91 | time=22:30:15`.
- Với mỗi sự kiện mới, nếu `ENABLE_SNAPSHOT=true` thì ảnh được lưu vào `SNAPSHOT_DIR`. Nếu `ENABLE_TELEGRAM=true` thì gửi ảnh kèm chú thích, hoặc chỉ gửi tin nhắn nếu không có ảnh.

## Cảnh báo Telegram

1. Tạo bot với BotFather và lấy token. Không đưa token lên git.
2. Lấy chat ID (ID người dùng hoặc nhóm). Có thể dùng bot hỗ trợ như `@get_id_bot`, hoặc xem trong `getUpdates`.
3. Trong `.env`, đặt `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` và `ENABLE_TELEGRAM=true`:

   ```
   TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
   TELEGRAM_CHAT_ID=987654321
   ```

   Hoặc điền trong tab Settings của GUI rồi khởi động lại app.

Mỗi lần có sự kiện mới, ứng dụng gửi một ảnh kèm chú thích đến chat đã cấu hình. Nếu gửi lỗi, lỗi được ghi log và video vẫn tiếp tục chạy.

## Quản lý dung lượng snapshot

Khi `ENABLE_SNAPSHOT=true`, ứng dụng tự xóa snapshot cũ để tránh đầy ổ đĩa:

- `SNAPSHOT_RETENTION_DAYS` (mặc định 7): giữ snapshot mới hơn số ngày này, xóa snapshot cũ hơn.
- `SNAPSHOT_CLEANUP_INTERVAL` (mặc định 100): quét thư mục sau mỗi N snapshot mới được lưu, để tránh quét quá thường xuyên khi có nhiều sự kiện.

Việc xóa không chặn pipeline. Nếu xóa lỗi (ví dụ file đã bị xóa từ bên ngoài), lỗi được ghi log và ứng dụng tiếp tục chạy.

## Ghi log

Log được ghi ra terminal và đồng thời ra file xoay vòng (`RotatingFileHandler`), mặc định tại `logs/app.log`. Điều này cần thiết khi chạy headless hoặc như service, vì khi đó không có ai xem terminal.

Cấu hình trong `.env`:

```
LOG_LEVEL=INFO           # DEBUG, INFO, WARNING, ERROR
LOG_FILE=logs/app.log    # đường dẫn file log
LOG_MAX_BYTES=5242880    # dung lượng tối đa trước khi xoay vòng (mặc định 5MB)
LOG_BACKUP_COUNT=3       # số file log cũ giữ lại
```

Khi chạy như service, ứng dụng tắt sạch khi nhận Ctrl+C hoặc `SIGTERM`. Vì vậy có thể dùng `systemctl stop` hoặc `docker stop` mà không cần kill cứng.

## Chạy unit test

Dự án dùng `pytest` (đã có trong `requirements.txt`). Chạy từ thư mục gốc:

```bash
pytest
```

Bộ test gồm:

- `tests/test_person_detected_event_manager.py`: logic chống trùng lặp sự kiện `PERSON_DETECTED`.
- `tests/test_player.py`: vòng lặp video (`stop_event`, `on_frame`).
- `tests/test_gui_helpers.py`, `tests/test_gui_worker.py`: logic của GUI (kiểm tra nguồn, chuyển frame, bảng sự kiện, liệt kê snapshot, thread nền).
- `tests/test_person_tracker_events.py`: callback sự kiện của `PersonTracker`, dùng detector giả nên không nạp YOLO.
- `tests/test_env_file.py`: đọc và ghi `.env`, chỉ dùng file tạm.

Các test dùng video tổng hợp do fixture tạo ra, không cần camera, model YOLO, mạng hay màn hình.
