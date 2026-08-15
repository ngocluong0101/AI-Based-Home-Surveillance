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
- Snapshot images are not sent in this step (planned later).
- Notification failures are logged to the terminal and do not crash the video pipeline.

# AI-Based-Home-Surveillance

Project Python nền tảng cho hệ thống giám sát tại nhà, được tổ chức theo hướng module hóa để dễ mở rộng ở các giai đoạn sau.

## Mục tiêu

Xây dựng một nền tảng sạch và tối giản cho các chức năng giám sát video, thông báo và phân tích sự kiện trong tương lai. Ở giai đoạn hiện tại, repository chỉ hoàn thành phần khởi tạo project và cấu trúc thư mục.

## Chức năng dự kiến

- Person Detection
- Person Tracking
- Person Alert
- Fall Detection
- Telegram Notification
- MP4/Webcam/RTSP input

## Development Status

- Project setup: hoàn thành
- MP4 input: hoàn thành
- Webcam input: hoàn thành
- Person Detection: hoàn thành
- Person Tracking: hoàn thành
- PERSON_DETECTED event: hoàn thành
- Person Alert, Fall Detection: chưa triển khai
- Telegram Notification: hoàn thành (text-only alerts)
- GPU/CUDA support: hoàn thành
- FPS display: hoàn thành

## Cấu trúc thư mục

```text
AI-Based-Home-Surveillance/
├── main.py
├── src/
│   ├── __init__.py
│   ├── video/
│   │   └── __init__.py
│   ├── detection/
│   │   └── __init__.py
│   ├── tracking/
│   │   └── __init__.py
│   ├── events/
│   │   └── __init__.py
│   └── notifications/
│       └── __init__.py
├── config/
├── data/
│   ├── videos/
│   └── snapshots/
├── tests/
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

## Yêu cầu môi trường

- Python 3.10 trở lên
- `pip`

Project hiện dùng `opencv-python` cho video input và `ultralytics` với model pretrained `yolov8n.pt` cho person detection/tracking. Tracking dùng ByteTrack của Ultralytics. Ứng dụng tự chọn `cuda` nếu PyTorch/CUDA khả dụng, ngược lại dùng `cpu`.

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
- Event chỉ được log ra terminal theo định dạng `[PERSON_DETECTED] ID=3 | confidence=0.91 | time=22:30:15`.

Khi chạy, ứng dụng sẽ in device đang dùng và hiển thị FPS trực tiếp trên video.

Nhấn `q` trong cửa sổ video để thoát.

Nếu không truyền `--source`, ứng dụng chỉ in thông báo khởi động và thoát.

## Kiểm tra CUDA

Bạn có thể kiểm tra PyTorch có nhận CUDA bằng lệnh sau:

```bash
python -c "import torch; print(torch.cuda.is_available())"
```

Nếu kết quả là `True`, ứng dụng sẽ tự động dùng `cuda:0` cho YOLO inference.
