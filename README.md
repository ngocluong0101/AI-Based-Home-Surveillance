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
- Person Tracking, Person Alert, Fall Detection, Telegram Notification: chưa triển khai

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

Project hiện dùng `opencv-python` cho video input và `ultralytics` với model pretrained `yolov8n.pt` cho person detection.

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

Nhấn `q` trong cửa sổ video để thoát.

Nếu không truyền `--source`, ứng dụng chỉ in thông báo khởi động và thoát.
