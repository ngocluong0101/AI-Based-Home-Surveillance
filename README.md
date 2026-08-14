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

Hiện tại project chưa dùng dependency ngoài nào.

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

## Hướng dẫn chạy

```bash
python main.py
```

## Development Status

Hiện tại mới hoàn thành phần project setup và tổ chức cấu trúc thư mục. Các tính năng xử lý video, detection, tracking, alerting và notification sẽ được triển khai ở các bước tiếp theo.
