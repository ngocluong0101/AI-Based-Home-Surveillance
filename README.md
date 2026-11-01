## Introduction

An AI-powered smart home surveillance system. The goal is a C engine on NVIDIA DeepStream that reads several video
sources (MP4 files, RTSP/IP cameras) in one batched pipeline, detects and tracks people with YOLOv8, turns the
detections into `PERSON_DETECTED` events, and hands them to a backend that stores them and sends Telegram alerts.

Current state of the repository:

- `engine/`: the DeepStream engine in C. The N-source pipeline (`nvstreammux` -> `nvinfer` with YOLOv8n -> `nvtracker`)
  runs and raises `PERSON_DETECTED` events per source (queued and printed as JSON lines; not yet sent to a backend), and can write a debug video with the boxes and track IDs drawn; snapshots and the publisher are not built yet (see [DeepStream engine](#deepstream-engine)).
- `src/events/`: the Python `PERSON_DETECTED` de-duplication logic. It is the reference specification; the C engine
  has a port of it (`engine/src/core/event_manager.c`) that passes the same test vectors.
- `src/notifications/`, `src/snapshot_management.py`, `src/config/`, `src/logging_config.py`: Python building blocks
  (Telegram notifier, snapshot cleanup, `.env` configuration, logging) for the future backend. No Python program
  runs a video pipeline any more: the old Ultralytics/OpenCV command line was removed.

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

`ultralytics` stays in `requirements.txt` to export `yolov8n.pt` to ONNX for the engine; `opencv-python` is used by
`scripts/test_telegram_snapshot.py`.

## PERSON_DETECTED event logic

`PersonDetectedEventManager` (`src/events/person_detected.py`) is a pure state machine with no I/O:

- Each `track_id` produces one event per appearance.
- When a `track_id` disappears for longer than `ABSENCE_TIMEOUT` (10 seconds by default) and then reappears, a new
  event is created.
- The caller passes the current time in, so the logic can be tested with synthetic timestamps.
- `tests/vectors/event_manager_*.json` are test vectors generated from this class (`tests/vectors/generate_event_manager_vectors.py`):
  a list of steps (clock, detections) with the expected events. `pytest` replays them on the Python class and
  `make test-core` replays the same files on the C port, so the two cannot drift apart.

## Telegram alerts

`TelegramNotifier` (`src/notifications/telegram_notifier.py`) calls the Telegram Bot API directly. It is safe to
create when unconfigured: it disables itself, every send becomes a no-op returning `False`, and it never raises.

1. Create a bot with BotFather and get its token. Never commit the token to git.
2. Get the chat ID (a user or group ID). You can use a helper bot such as `@get_id_bot`, or look at `getUpdates`.
3. In `.env`, set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` and `ENABLE_TELEGRAM=true`:

   ```
   TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
   TELEGRAM_CHAT_ID=987654321
   ```

Try it with a fake snapshot:

```bash
python scripts/test_telegram_snapshot.py
```

## Snapshot storage management

`SnapshotCleaner` (`src/snapshot_management.py`) deletes old snapshots to avoid filling the disk:

- `SNAPSHOT_RETENTION_DAYS` (default 7): keep snapshots newer than this many days and delete older ones.
- `cleanup(snapshot_dir)` only looks at files named `PERSON_DETECTED_*.jpg` in the directory it is given
  (`SNAPSHOT_DIR` in `.env`), so the engine must name its snapshots that way.

A failed deletion is logged and never raises.

## Logging

`setup_logging()` (`src/logging_config.py`) logs to the terminal and to a rotating file, by default `logs/app.log`.
Configure it in `.env`:

```
LOG_LEVEL=INFO           # DEBUG, INFO, WARNING, ERROR
LOG_FILE=logs/app.log    # log file path
LOG_MAX_BYTES=5242880    # maximum size before rotation (default 5 MB)
LOG_BACKUP_COUNT=3       # number of old log files to keep
```

## Run the unit tests

The project uses `pytest` (already in `requirements.txt`). Run it from the repository root:

```bash
pytest
```

The test suite contains:

- `tests/test_person_detected_event_manager.py`: the `PERSON_DETECTED` de-duplication logic.
- `tests/test_event_manager_vectors.py`: the shared event manager vectors (see above).
- `tests/test_env_file.py`: reading and writing `.env`, using temporary files only.

The tests need no camera, model, network or display.

## DeepStream engine

The `engine/` directory holds a C engine built on NVIDIA DeepStream: one process, one pipeline, N cameras
(`nvstreammux` batches the N sources, so `nvinfer` runs once per batch).

```
source 0 ─┐
source 1 ─┼─> nvstreammux (batch-size = N) ─> nvinfer ─> nvtracker (NvDCF) ─┬─> fakesink
source N ─┘                                                    │            │
                                                               │            └─> with --output: tiler -> OSD -> H.264 -> MP4
                                                               └─ probe: Detection[] and statistics per source_id
```

What works now: the pipeline above for any N >= 1 with YOLOv8n (person only, TensorRT FP16, dynamic batch) and the
NvDCF tracker, per-source state keyed by `source_id`, the bus handling (ERROR/WARNING/EOS) and exit codes. A probe on
the `nvtracker` source pad copies every tracked person of every frame into a plain `Detection` struct (source, track ID,
confidence, relative box, stream time; `engine/src/core/detection.h`) and gathers statistics per `source_id`: frames,
objects, distinct track IDs, tracks seen in fewer than 10 frames, frame rate. The same probe feeds the `Detection` values of
each source to that source's event manager (`engine/src/core/event_manager.c`, a C port of `PersonDetectedEventManager`),
which emits one `PERSON_DETECTED` event per appearance of a track; its clock is the stream start time plus the frame's
PTS, not the wall clock. Each event becomes an `EventRecord` (schema below), is pushed on a bounded queue (1024) and a
worker thread prints it as a line `event {json}` on stdout, so nothing slow runs in the probe; if the worker falls
behind, new events are dropped and counted (`dropped=` in the summary). With `--snapshot-dir DIR` the probe also asks the DeepStream JPEG encoder for the whole frame of each event; events that
start in the same frame share one file. The probe only copies the JPEG bytes (it waits a few milliseconds, only on frames
that have an event); the worker thread writes the file and only then prints the event, so the file is on disk when the
event goes out. A file that cannot be written (directory not writable, disk full) is logged and the event is published
with `"snapshot":null`. What does not exist yet: the HTTP publisher with retry and spool file.

Prepare the model once. On the host, export YOLOv8n to ONNX (the file is git-ignored):

```bash
python engine/tools/export_yolov8_onnx.py      # yolov8n.pt -> engine/models/yolov8n.onnx
```

Build inside the DeepStream container (`nvcr.io/nvidia/deepstream:7.1-triton-multiarch`, run with `--gpus all`; mount
`engine/` into it). The second command builds the YOLOv8 bbox parser that `nvinfer` loads:

```bash
cd engine && make CUDA_VER=12.6 && make -C parser CUDA_VER=12.6
./build/ds_engine --config configs/pgie_yolov8n.txt \
                  --tracker-config configs/tracker_nvdcf.yml \
                  --output build/out.mp4 \
                  --camera 1=file:///path/to/a.mp4 \
                  --camera 2=file:///path/to/b.mp4
```

At the end it prints one line per source, for example `source_id=0 camera_id=1 frames=1443 objects=4993 untracked=0 tracks=29 short_tracks=4 events=29 fps=351.2`, followed by
a line `events published=58 dropped=0 pipeline_run_id=...`, with `--snapshot-dir` a line `snapshots written=56 failed=0 events_with_snapshot=58 probe_blocked_ms total=... max=...`, and the elapsed time and the overall frame rate (it includes the start-up). While it runs it prints the frame rate of every
source each 5 seconds. The first run
spends about 2.5 minutes building the TensorRT engine; it is saved next to the ONNX and reused (a run then starts in
seconds). `configs/pgie_yolov8n.txt` points at the engine for N = 2; for another N, `nvinfer` builds a new engine
(`yolov8n.onnx_b<N>_gpu0_fp16.engine`) on every run until you change the `model-engine-file` line. `configs/pgie_sample.txt`
is a smoke-test config for the sample detector shipped in the DeepStream image.

On a 1442-frame test video the boxes match Ultralytics (IoU 0.95-0.99), but the engine finds about 10% fewer people at
the same 0.25 threshold, mostly low-confidence boxes near the threshold. On the same video NvDCF gives 21 tracks (3 short)
where Ultralytics with ByteTrack gives 36 (9 short); without ground truth this shows fewer split IDs, not that the IDs are
all correct.

Event schema v1, one JSON object per event (`engine/src/core/event_record.h`):

```json
{"schema_version":1,"event_id":"<uuid>","camera_id":1,"pipeline_run_id":"<uuid>","type":"PERSON_DETECTED",
 "track_id":12,"confidence":0.9100,"timestamp":"2026-01-01T12:00:00.123Z","bbox":[0.1000,0.2000,0.3000,0.4000],"snapshot":null}
```

`event_id` is unique per event (so a repeated delivery can be ignored). `pipeline_run_id` is new each time the engine
starts, because track IDs restart then: a person is `(pipeline_run_id, camera_id, track_id)`. `timestamp` is UTC, stream
start plus PTS. `bbox` is x, y, w, h relative to the frame. `snapshot` is a file name (no directory) like `PERSON_DETECTED_cam1_id12_20260101T120000_123e4567.jpg`, which matches what
`SnapshotCleaner` deletes, or `null`.

Options:

- `--camera ID=URI`: a source, repeat it for each camera. `ID` is a non-negative integer, `source_id` is the
  declaration order.
- `--cameras-file FILE`: sources whose URI contains a password (for example `rtsp://user:pass@host/stream`), so the
  password stays out of the command line, where `ps` would show it. The file must be mode 600 and has one group per
  camera:

  ```
  [camera.3]
  uri=rtsp://user:pass@192.168.1.100:554/stream
  ```

- `--config FILE`: the `nvinfer` config file (required), for example `configs/pgie_yolov8n.txt`.
- `--output FILE`: debug only. Writes an MP4 with all sources tiled in one video (rows x columns grid), the boxes drawn and
  labelled `person <track ID>`. Without it nothing is rendered. If one source ends earlier than the others, the tile of a
  source that is still running shows only about half of its frames (every frame is still processed and counted).
- `--snapshot-dir DIR`: save a JPEG of the whole frame for each event in DIR (created if missing). Without it no snapshots are taken.
- `--snapshot-quality Q`: JPEG quality 1-100 (default 85).
- `--absence-timeout SEC`: how long a person must be out of sight before the same track ID raises a new event (default 10, same as `ABSENCE_TIMEOUT`).
- `--fps-interval SEC`: how often the frame rate of each source is printed (default 5, 0 turns it off).
- `--tracker-config FILE`: the `nvtracker` low-level config (required), `configs/tracker_nvdcf.yml` for NvDCF. The values in
  it are a starting point, not tuned.

Exit codes: `0` all sources reached EOS, `1` a pipeline error, `2` bad arguments, `3` the pipeline could not be built.
The program never prints a full URI, only its scheme.

Run the tests of the pure-C code (`engine/src/core/`: the `Detection` box normalisation, the event manager, which replays the shared vectors of `tests/vectors/`, and the event JSON) on the host, no
DeepStream or GPU needed; built with AddressSanitizer and UBSan:

```bash
cd engine && make test-core
```

The DeepStream sample sources are under NVIDIA's proprietary license, so they are not copied into this repository.
