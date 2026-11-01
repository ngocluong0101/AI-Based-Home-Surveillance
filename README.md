## Introduction

An AI-powered smart home surveillance system. The application reads video from an MP4 file, a webcam, or an RTSP/IP
camera, and uses YOLOv8 to detect people (with ByteTrack to follow each person). When a new person appears, it saves a
snapshot and sends an alert via Telegram. It runs from the command line (CLI).

## Installation

1. Clone the repository.
2. Optional: create a virtual environment.
3. Install the dependencies:

```bash
pip install -r requirements.txt
```

4. Copy the sample configuration file:

```bash
cp .env.example .env
```

On the first run, Ultralytics downloads the `yolov8n.pt` model automatically, so a network connection is required.

## Check CUDA

```bash
python -c "import torch; print(torch.cuda.is_available())"
```

If the result is `True`, the application uses `cuda:0` for YOLO. Otherwise it falls back to the CPU.

## Prepare a video source

- **Video file**: put an `.mp4` file in `data/videos/`, for example `data/videos/test.mp4`.
- **Webcam**: the default device is `0`. If the machine has several cameras, use `1`, `2`, ...
- **RTSP/IP camera**: pass the URL with `--source`, or set `RTSP_URL` in `.env` and run without `--source`.

  ```
  python main.py --source "rtsp://username:password@192.168.1.100:554/stream"
  ```

  When the connection drops, the application retries every 5 seconds (`RECONNECT_DELAY`). The log shows the lines `[CAMERA] Connection lost`, `[CAMERA] Reconnecting...` and `[CAMERA] Connected`. If the very first connection fails, the application retries in the same way.

## Run from the command line (CLI)

Run with a video file:

```bash
python main.py --source data/videos/test.mp4
```

Run with a webcam, with tracking:

```bash
python main.py --source 0 --track
```

Run with an RTSP camera, with tracking:

```bash
python main.py --source "rtsp://username:password@192.168.1.100:554/stream" --track
```

Run without opening a video window (for servers/NAS boxes without a display):

```bash
python main.py --source "rtsp://username:password@192.168.1.100:554/stream" --track --headless
```

Change the confidence threshold:

```bash
python main.py --source 0 --conf-threshold 0.5
```

Without `--source`, the application uses `RTSP_URL` from `.env` if it is set; otherwise it prints the usage help and exits.

Press `q` in the video window to quit. While running, the terminal shows the device in use, the FPS on the video, and the `PERSON_DETECTED` events.

## PERSON_DETECTED event logic

- Each `track_id` produces one event per appearance.
- When a `track_id` disappears from the camera for longer than `ABSENCE_TIMEOUT` (10 seconds by default) and then reappears, the system creates a new event.
- Events are written to the terminal and the log file in the format `[PERSON_DETECTED] ID=3 | confidence=0.91 | time=22:30:15`.
- For each new event, if `ENABLE_SNAPSHOT=true` the image is saved to `SNAPSHOT_DIR`. If `ENABLE_TELEGRAM=true`, the image is sent with a caption, or only a text message is sent when there is no image.

## Telegram alerts

1. Create a bot with BotFather and get its token. Never commit the token to git.
2. Get the chat ID (a user or group ID). You can use a helper bot such as `@get_id_bot`, or look at `getUpdates`.
3. In `.env`, set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` and `ENABLE_TELEGRAM=true`:

   ```
   TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
   TELEGRAM_CHAT_ID=987654321
   ```

For every new event, the application sends one photo with a caption to the configured chat. If sending fails, the error is logged and the video keeps running.

## Snapshot storage management

When `ENABLE_SNAPSHOT=true`, the application deletes old snapshots automatically to avoid filling the disk:

- `SNAPSHOT_RETENTION_DAYS` (default 7): keep snapshots newer than this many days and delete older ones.
- `SNAPSHOT_CLEANUP_INTERVAL` (default 100): scan the directory after every N newly saved snapshots, to avoid scanning too often when there are many events.

Deletion does not block the pipeline. If a deletion fails (for example the file was removed externally), the error is logged and the application keeps running.

## Logging

Logs go to the terminal and also to a rotating file (`RotatingFileHandler`), by default `logs/app.log`. This matters when running headless or as a service, because nobody is watching the terminal.

Configure it in `.env`:

```
LOG_LEVEL=INFO           # DEBUG, INFO, WARNING, ERROR
LOG_FILE=logs/app.log    # log file path
LOG_MAX_BYTES=5242880    # maximum size before rotation (default 5 MB)
LOG_BACKUP_COUNT=3       # number of old log files to keep
```

When run as a service, the application shuts down cleanly on Ctrl+C or `SIGTERM`, so you can use `systemctl stop` or `docker stop` without a hard kill.

## Run the unit tests

The project uses `pytest` (already in `requirements.txt`). Run it from the repository root:

```bash
pytest
```

The test suite contains:

- `tests/test_person_detected_event_manager.py`: the `PERSON_DETECTED` de-duplication logic.
- `tests/test_player.py`: the video loop (`stop_event`, `on_frame`).
- `tests/test_person_tracker_events.py`: the `PersonTracker` event callback, using a fake detector so YOLO is not loaded.
- `tests/test_env_file.py`: reading and writing `.env`, using temporary files only.

The tests use synthetic videos created by a fixture and need no camera, YOLO model, network or display.

## DeepStream engine (work in progress)

The `engine/` directory holds a C engine built on NVIDIA DeepStream, intended to eventually replace the Python
inference/tracking path with one pipeline that processes several cameras in a single batch. It is currently a
skeleton: it parses `--camera ID=URI` and handles the GStreamer bus, but the pipeline itself is not built yet, so
`ds_engine` exits with code 3 ("pipeline not implemented").

Build it inside the DeepStream container (`nvcr.io/nvidia/deepstream:7.1-triton-multiarch`, run with `--gpus all`):

```bash
cd engine && make CUDA_VER=12.6
./build/ds_engine --camera 1=file:///path/to/a.mp4
```

Run the tests of the pure-C code on the host (no DeepStream or GPU needed; built with AddressSanitizer and UBSan):

```bash
cd engine && make test-core
```

The DeepStream sample sources are under NVIDIA's proprietary license, so they are not copied into this repository.
