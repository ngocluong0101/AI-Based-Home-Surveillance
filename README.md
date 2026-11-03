## Introduction

An AI-powered smart home surveillance system. The goal is a C engine on NVIDIA DeepStream that reads several video
sources (MP4 files, RTSP/IP cameras) in one batched pipeline, detects and tracks people with YOLOv8, turns the
detections into `PERSON_DETECTED` events, and hands them to a backend that stores them and sends Telegram alerts.

Current state of the repository:

- `engine/`: the DeepStream engine in C. The N-source pipeline (`nvstreammux` -> `nvinfer` with YOLOv8n -> `nvtracker`)
  runs and raises `PERSON_DETECTED` events per source (with a JPEG snapshot of the frame each; queued and then POSTed to a backend with retry and a spool file, or printed as JSON lines when no backend is given), and can write a debug video with the boxes and track IDs drawn (see [DeepStream engine](#deepstream-engine)).
- `backend/`: the FastAPI backend (REST API for cameras and events, the endpoint the engine posts events to), storing
  everything in PostgreSQL, starts/stops the engine for the cameras that should run through `engine_manager/`, shows the
  camera states the engine reports, pushes events and states to browsers over a WebSocket, sends the Telegram alerts and
  deletes old snapshots (see [Backend](#backend)). There is no frontend yet.
- `src/events/`: the Python `PERSON_DETECTED` de-duplication logic. It is the reference specification; the C engine
  has a port of it (`engine/src/core/event_manager.c`) that passes the same test vectors.
- `src/notifications/`, `src/snapshot_management.py`, `src/config/`, `src/logging_config.py`: Python building blocks
  (Telegram notifier, snapshot cleanup, `.env` configuration, logging), used by the backend. No Python program
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

The backend sends the alerts (the engine never talks to Telegram): `POST /internal/events` only stores the event, and a
background thread (`backend/notifier.py`) sends the pending ones, oldest first, as a photo when the snapshot file exists and as
text otherwise. The result is in the event's `notify_status`: `sent`; `disabled` when Telegram is not configured; `failed` when
the alert stayed undeliverable for more than `NOTIFY_MAX_AGE` seconds (default 600), because an alert that late is noise. While
Telegram fails the event stays `pending` and is retried with a growing pause (1 s doubling to 60 s), so a Telegram outage never
loses or delays an event, and a restart of the backend picks up the events still pending. The time in the message is the
server's local time.

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

The backend runs it for you (`backend/janitor.py`): once at start-up, then after every `SNAPSHOT_CLEANUP_INTERVAL` (default 100)
newly stored snapshots, and at least once an hour. Afterwards the events older than the retention stop pointing at the deleted
images (`snapshot_url` becomes `null` instead of leading to a 404).

## Logging

`setup_logging()` (`src/logging_config.py`) logs to the terminal and to a rotating file, by default `logs/app.log`.
Configure it in `.env`:

```
LOG_LEVEL=INFO           # DEBUG, INFO, WARNING, ERROR
LOG_FILE=logs/app.log    # log file path
LOG_MAX_BYTES=5242880    # maximum size before rotation (default 5 MB)
LOG_BACKUP_COUNT=3       # number of old log files to keep
```

## Backend

FastAPI + SQLAlchemy 2 + Alembic on **PostgreSQL**. Layout: `backend/main.py` (`create_app`), `models.py` (tables),
`schemas.py`, `routers/` (`health`, `cameras`, `events`, `internal`), `migrations/` (Alembic).

Create the database once (as the PostgreSQL superuser; pick your own password, and keep it out of the URL by putting it
in `~/.pgpass` or `PGPASSWORD`):

```bash
sudo -u postgres psql -c "CREATE ROLE surveillance LOGIN PASSWORD '<password>'" \
                      -c "CREATE DATABASE surveillance OWNER surveillance"
```

Set `DATABASE_URL` in `.env` (see `.env.example`), create the tables, and run the server:

```bash
alembic -c backend/alembic.ini upgrade head
uvicorn backend.main:create_app --factory --host 0.0.0.0 --port 8000     # API docs at /docs
```

| Endpoint | |
|---|---|
| `GET /api/health` | 200, or 503 when the database cannot be reached |
| `GET/POST /api/cameras`, `GET/PATCH/DELETE /api/cameras/{id}` | cameras. The id is the `camera_id` the engine puts in its events. `source_uri` is stored as given (the engine needs the password) but every response shows it masked (`rtsp://***@host/path?***`). DELETE is a soft delete that frees the name. |
| `GET /api/events` | newest first. Filters `camera_id`, `from` (inclusive), `to` (exclusive), `type`, `status`; pagination `limit` (1-200, default 50) and `offset`; answers `{items, total, limit, offset}` |
| `GET /api/events/{id}`, `GET /api/events/{id}/snapshot` | one event; its JPEG from `SNAPSHOT_DIR` (404 when the event has none or the cleaner removed the file) |
| `PATCH /api/events/{id}` | body `{"status": "new" \| "acknowledged"}` (nothing else may be changed); answers the event and pushes `event.updated` when the status changed |
| `POST /internal/events` | for the engine, not for browsers. Idempotent on `event_id`: 201 stored, 200 already had it, 422 can never be stored (unknown camera, bad values), which makes the engine drop it instead of retrying. With `INTERNAL_TOKEN` set it needs the header `X-Internal-Token`. |
| `WS /api/ws` | live updates for the browser, server to client only (what the client sends is ignored, there is no replay: read the current state with the REST calls). Each message is `{"type", "data"}`. `event.created`: a newly stored event, `data` the same object as `GET /api/events/{id}` (not sent for a repeated delivery). `camera.status`: `data` is `{camera_id, reporting, state, fps, last_frame_age, heartbeat_age}`, sent for each camera in every heartbeat, and once with `reporting: false` when a camera stops reporting (it left the heartbeat, or the heartbeats stopped, found within about a second). `event.updated`: the same object after a change of `status` (PATCH) or of `notify_status` (the Telegram result). A client more than 100 messages behind is closed with code 1013, so a stuck browser cannot slow the backend down |
| `POST /internal/heartbeat` | for the engine too: every few seconds it says which cameras it serves and what each is doing (`{"schema_version":1,"run_id":...,"cameras":[{"camera_id","state":"RUNNING"\|"RECONNECTING","fps","last_frame_age"}]}`). 204 on success, 422 for a malformed body. Kept in memory only (nothing is stored) and shown by `GET /api/cameras/{id}/status`; the same token rule as for events. |

Run the engine against it with `--backend-url http://HOST:8000/internal/events --spool-file FILE --snapshot-dir DIR`, where
`DIR` is the backend's `SNAPSHOT_DIR` (same machine for now). When `INTERNAL_TOKEN` is set, give the engine the same value in
the environment variable `DS_ENGINE_TOKEN` (not on the command line, where `ps` would show it); it sends it as `X-Internal-Token`.
Events of a camera that does not exist in the backend are refused, so create the cameras first, with ids equal to the
`--camera ID=URI` ids. You normally do not start the engine yourself: the engine manager does (next section).

### Live video

The engine can publish every camera, with the boxes and track IDs drawn on it, to [MediaMTX](https://github.com/bluenviron/mediamtx),
which serves it to browsers over WebRTC. Run MediaMTX on the same machine (its default configuration is enough on a LAN; it
lets anyone who can reach it publish and watch):

```bash
docker run -d --rm --name mediamtx --network host bluenviron/mediamtx:latest   # RTMP 1935, WebRTC 8889, RTSP 8554
```

and set, in `.env`:

```
ENGINE_STREAM_URL=rtmp://127.0.0.1:1935/live     # the engine publishes camera N at <this>/cam<N>
MEDIAMTX_WEBRTC_URL=http://192.168.1.20:8889     # how the BROWSER reaches MediaMTX
```

`GET /api/cameras/{id}/stream` then answers `{camera_id, whep_url, player_url, available}`: `whep_url` for a player that speaks
WHEP, `player_url` for MediaMTX's own page with a player (try it in a browser, with the engine running:
`http://HOST:8889/live/cam1`), `available` is true while the engine reports that camera in its heartbeat. The answer never
holds the camera's own address; it answers 503 until `MEDIAMTX_WEBRTC_URL` is set. Measured on the development machine
(RTX 4060 Laptop, two 1080p cameras at 30 fps, detection and streaming together): Chrome shows 29.8 frames/s without dropping any,
the GPU is 12-22 % busy (NVENC 7 %, NVDEC 5-8 %), the engine uses about 360 MiB of VRAM more than an idle GPU, 489 MiB of RAM and
38 % of one CPU core, MediaMTX 27 MiB and under 2 % of a core. Twelve simultaneous NVENC sessions all ran; of 24 attempted
only 12 started (the others failed with "Error in initializing nvenc context"), so about 12 cameras can be streamed with this card. The glass-to-glass latency, Firefox and a phone have not been measured yet.

### Engine manager

`engine_manager/` runs the engine for the backend: **one `ds_engine` process serves all running cameras**, so the manager
keeps the process in step with the list of cameras that should run. It is a separate small service (it can stay up while the
backend restarts), listening on the loopback interface only, without authentication:

```bash
uvicorn engine_manager.service:create_app --factory --host 127.0.0.1 --port 8100
```

| Backend endpoint | |
|---|---|
| `POST /api/cameras/test-connection` | tries a camera without starting the engine: body is either `{source_type, source_uri}` or `{camera_id}` (a saved camera, whose password the API never shows), plus an optional `timeout` (1-15 s, default 5). Answers 200 with `{ok, summary, checks[], codec, width, height, latency_ms}`; `checks` run in order (`connect`, `auth`, `stream`, `codec`, `resolution`) and the last one is the one that failed. A camera that does not work is `ok: false` with the reason in `summary`, not an HTTP error. RTSP: connects, answers a Basic or Digest login, reads the stream description for the codec and, for H.264, the announced SPS for the resolution (H.265 and cameras that do not announce it give no resolution, which is not a failure; only H.264 and H.265 pass the codec check). A `file://` source is checked for being readable by the backend. Neither the address nor the password appears in the answer; the backend connects to whatever host it is given, so this is for a LAN |
| `POST /api/cameras/{id}/start`, `POST .../stop` | record that the camera should (not) run. A disabled camera cannot be started (409). The answer comes at once; the engine is (re)started in the background, a few seconds later |
| `GET /api/cameras/{id}/stream` | where to watch the camera live: `{camera_id, whep_url, player_url, available}` (see [Live video](#live-video)); 503 when `MEDIAMTX_WEBRTC_URL` is not set |
| `GET /api/cameras/{id}/status` | `desired_state`, `should_run`, and `engine`: whether the manager is reachable, the state of the engine process (`stopped`, `running`, `stopping`, `backoff`, `exited`), `in_engine` (the running process serves this camera), `applied` (the manager has the current list), `starts`, `last_exit_code`; and `stream`, what the engine itself reports in its heartbeat: `reporting` (a heartbeat no older than `HEARTBEAT_STALE_AFTER`, default 15 s, names this camera), `state` (`RUNNING` or `RECONNECTING`), `fps`, `last_frame_age` (seconds without a frame), `heartbeat_age`, `people` (tracked persons in the camera's latest frame; `null` when the engine cannot tell, e.g. no frame for the last 2 s, or an older engine). `reporting: false` means the engine is down, still starting, or not serving the camera; the other fields are then empty |

How it works. The database says which cameras should run (enabled, not deleted, `desired_state = running`). A background thread in
the backend compares that list with the one the manager reports (by a hash) and pushes it when they differ, right after a camera
changes and every few seconds anyway, so a manager that was down or restarted gets the list when it is back, and the API calls
never wait for it. The manager starts the engine with the list, and when the list changes (a camera started, stopped, disabled,
deleted, or its URI changed) it stops the engine with SIGTERM (the engine prints its summary and flushes its spool, up to
`stop_timeout` 25 s, then SIGKILL) and starts it again; everything that is not a camera setting (renaming, thresholds) does not restart
it. All cameras lose a few seconds of video at each change. If the engine crashes it is started again after 1 s, 2 s, ... up to 60 s
(a run longer than 30 s resets the delay). Exit code 0 means every source ended (video files): it is not restarted until the list changes.

RTSP URLs hold passwords, so they never appear on the engine's command line: the manager writes them to
`ENGINE_RUNTIME_DIR/cameras.ini` (mode 600) and passes `--cameras-file`; the token goes in `DS_ENGINE_TOKEN`. What the engine prints
is logged (passwords in URLs replaced) to `logs/engine_manager.log`. The settings are the `ENGINE_*` entries of `.env.example`.
`ABSENCE_TIMEOUT` is passed to the engine as one value for all cameras; the per-camera `absence_timeout` and `conf_threshold` of the
camera table are stored but not applied yet (the engine has no per-camera settings).

To run the engine in the DeepStream container, make `ENGINE_COMMAND` the whole prefix, with the runtime directory, the snapshot directory
and the config files mounted at the same absolute paths, for example:

```bash
ENGINE_COMMAND="docker run --rm --network host --gpus all -u 1000:1000 -e DS_ENGINE_TOKEN -v $PWD:$PWD -w $PWD --entrypoint $PWD/engine/build/ds_engine nvcr.io/nvidia/deepstream:7.1-triton-multiarch"
```

A TensorRT engine is built for one batch size, so there is one file per number of cameras
(`engine/models/yolov8n.onnx_b<N>_gpu0_fp16.engine`). The Engine manager writes a copy of the `nvinfer` config for the current number
of cameras (`ENGINE_RUNTIME_DIR/pgie_b<N>.txt`, with absolute paths and `model-engine-file` pointing at that file) at every start.
The first start with a number of cameras that has no engine yet builds it (about 2-3 minutes, logged as a warning, the cameras
report nothing meanwhile), later starts load it in about a second. `ENGINE_COMMAND` in a container needs `engine/models` mounted
writable at the same absolute path. Delete the `*_b*_gpu0_fp16.engine` files when the ONNX model is exported again.

The backend tests (`tests/backend_api/`) start a throwaway PostgreSQL cluster of their own (`initdb` + `pg_ctl` from the
installed PostgreSQL, a unix socket in a temp directory, removed afterwards) and never touch an existing server. To
use another server set `TEST_DATABASE_URL` to a database whose name ends with `_test`: its tables are emptied before
every test.

## Frontend

`frontend/` is a dark, sidebar-based React + TypeScript + Vite app (TanStack Query for REST, one WebSocket for pushes,
`motion` for animation, `lucide-react` icons, `sonner` toasts). Pages:

- **Dashboard**: cameras / online / events today / events to review, recent events, one card per camera (state, FPS, events
  today, Start/Stop) and a bar chart of events today by camera.
- **Monitoring**: camera list with a status dot, live WebRTC video (boxes and track IDs drawn by the engine), FPS, events today,
  pipeline status, last error, last update time and the latest events of the selected camera.
- **Cameras**: list with Start/Stop, Test, Edit and Delete (with a confirmation), and an *Add camera* drawer: name, IP camera (RTSP) or video file, address,
  optional username and password in their own fields (special characters need no escaping; the password is stored on the server and never shown),
  and **Test connection**, which shows what works (reachable, login, video stream, codec, resolution). When editing, an empty address keeps the saved one.
- **Events**: filters (camera, status, date range), paging, confidence bars and an event drawer with the snapshot, the detection
  box and Acknowledge. Opening any event (from any list or a toast) shows the same drawer.

New events arrive through `WS /api/ws` without reloading: they appear at the top of the lists, are highlighted for a few seconds
and raise a toast; a duplicate message for the same `event_id` is ignored. The "Live" pill in the sidebar shows the socket state,
and a banner appears while it reconnects. Every screen has loading (skeleton), empty and error states; when a refresh fails the
last data stays on screen. Animations follow the OS "reduce motion" setting.

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173, forwards /api (REST and WebSocket) to http://127.0.0.1:8000
BACKEND_URL=http://192.168.1.20:8000 npm run dev   # backend elsewhere
npm test             # unit tests (vitest)
npm run build        # type-check, then bundle into frontend/dist
```

Source layout: `src/api` (REST calls), `src/websocket` (socket, message handling, duplicate filter), `src/hooks` (queries, route),
`src/types`, `src/utils`, `src/components` (+ `ui/` building blocks), `src/layouts` (sidebar shell), `src/pages`.

The browser only talks to one origin, so the backend needs no CORS setup; `npm run dev` does the forwarding. Live video needs
MediaMTX and `MEDIAMTX_WEBRTC_URL` (see "Live video"); that URL must be reachable from the browser itself. The built `frontend/dist` is not served by the backend yet.
"People now" is the number of people in the latest frame of each camera, as the engine reports it in its heartbeat (see "Heartbeat" below); it shows "-" while a camera has no recent frame, never a false 0. Events today counts people detected since midnight.

## Docker

The whole system runs as five containers (`deploy/compose.yml`): PostgreSQL, MediaMTX, the backend, the engine together
with the Engine manager (one GPU container), and the web app behind nginx.

Needs Docker with the NVIDIA Container Toolkit, and the ONNX model on the host (once: `python engine/tools/export_yolov8_onnx.py`,
it lands in `engine/models/`, which is mounted into the engine container). Then, from the repository root:

```bash
cp .env.example .env          # set POSTGRES_PASSWORD (letters and digits), INTERNAL_TOKEN (openssl rand -hex 24),
                              # PUBLIC_HOST (the address browsers use to reach this machine), and Telegram if wanted
docker compose --env-file .env -f deploy/compose.yml up -d --build
```

Open `http://<PUBLIC_HOST>:8080` (`HTTP_PORT` changes it), add the cameras on the Cameras page and press Start. Compose stops with a
message when `POSTGRES_PASSWORD` or `INTERNAL_TOKEN` is missing. `--env-file .env` is needed because compose would look for `.env`
next to the compose file.

| Container | Published on the host | Notes |
|---|---|---|
| `frontend` (nginx) | `HTTP_PORT` (8080) | serves the app and forwards only `/api` (REST + WebSocket); `/internal` answers 404 |
| `mediamtx` | 8889/tcp, 8189/udp | WebRTC for the browsers; `PUBLIC_HOST` goes into its ICE candidates. RTMP (1935) stays inside |
| `backend`, `db`, `engine` | nothing | the engine posts events to `backend` with `INTERNAL_TOKEN`; the Engine manager (port 8100, no authentication) is reachable only from the other containers |

- Cameras with `file://` sources must point at files inside the engine container: mount the folder yourself (a compose override file).
- The first start with a number of cameras that has no TensorRT engine yet builds it (2-3 minutes, in `engine/models/`, which must be
  writable by uid 1000); `docker compose logs engine` shows it. Delete `engine/models/*_b*_gpu0_fp16.engine` when the ONNX model changes.
- Snapshots live in the `snapshots` volume (shared by engine and backend), the database in `pgdata`. `down` keeps them, `down -v` deletes them.
  Backup: `docker compose --env-file .env -f deploy/compose.yml exec db pg_dump -U surveillance surveillance > backup.sql`.
- Every container has `restart: unless-stopped` and a healthcheck (not MediaMTX). A container whose main process exits is started again;
  one you stopped yourself (`docker stop`, `docker kill`) is not. Telegram settings, `ABSENCE_TIMEOUT` and `SNAPSHOT_RETENTION_DAYS`
  are read from `.env`; the token is passed to the backend as an environment variable (visible with `docker inspect`).
- The engine image is based on the DeepStream image (31.7 GB); what this repository adds is a few MB.

## Run the unit tests

The project uses `pytest` (already in `requirements.txt`). Run it from the repository root:

```bash
pytest
```

The test suite contains:

- `tests/test_person_detected_event_manager.py`: the `PERSON_DETECTED` de-duplication logic.
- `tests/test_event_manager_vectors.py`: the shared event manager vectors (see above).
- `tests/test_env_file.py`: reading and writing `.env`, using temporary files only.
- `tests/backend_api/`: the backend (cameras, events, the internal endpoints, heartbeat, the WebSocket, Telegram alerts, snapshot cleanup, health, migrations, start/stop and the reconciler) against a throwaway PostgreSQL.
- `tests/engine_manager_tests/`: the engine manager, with a stand-in program for the engine.

The tests need no camera, model, network or display; the backend tests need the PostgreSQL server programs installed
(they are skipped otherwise).

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
worker thread delivers it (see below), so nothing slow runs in the probe; if the worker falls
behind, new events are dropped and counted (`dropped=` in the summary). With `--snapshot-dir DIR` the probe also asks the DeepStream JPEG encoder for the whole frame of each event; events that
start in the same frame share one file. The probe only copies the JPEG bytes (it waits a few milliseconds, only on frames
that have an event); the worker thread writes the file and only then prints the event, so the file is on disk when the
event goes out. A file that cannot be written (directory not writable, disk full) is logged and the event is published
with `"snapshot":null`.

Delivery. Without `--backend-url` the worker prints each event as a line `event {json}` on stdout (for debugging). With
`--backend-url URL --spool-file FILE` it POSTs the JSON to the backend with libcurl (connect timeout 2 s, 5 s in total; the environment variable `DS_ENGINE_TOKEN`, if set, is sent as the header `X-Internal-Token`).
HTTP 2xx, or 409 (the backend already has this `event_id`), counts as delivered; 400/413/422 means the backend refuses the
event for good (counted as `rejected`); anything else (no connection, 5xx, 429, even 404/401/403 from a misconfigured
backend) is retried later. After a failure the backend is not tried again for 1 s, then 2 s, 4 s ... up to 60 s, and in the
meantime every event is appended to the spool file (one JSON per line, flushed to disk), so a dead backend costs nothing but
the file. Once the backend answers again the spool is emptied oldest first, also after a restart of the engine; the backend
must ignore an event it already has (same `event_id`). When the engine stops it keeps trying for at most 10 s; what is left
stays in the spool. Errors are logged without the URL. The snapshot files are not sent: the JSON only names the file.
The test backend `engine/tools/fake_receiver.py` can answer slowly (`--delay`) or with an error (`--status`, `--status-for`), and with `--heartbeat-log FILE` also takes the heartbeats.

Heartbeat. With `--heartbeat-url URL` (needs `--backend-url`; same `DS_ENGINE_TOKEN`) the engine POSTs the state of every camera each `--heartbeat-interval` seconds (default 5): `RUNNING` or `RECONNECTING`, the frame rate over the last interval, the seconds since the last frame, and `people`: how many tracked persons the latest frame held (the people the events are made from). `people` is left out when the latest frame is older than 2 s or there was none yet (a 0 would claim an empty room), and an older engine never sends it; the backend accepts both. The main loop builds it (that is where the source states live) and the publisher thread sends it on a connection of its own, with shorter timeouts (1 s to connect, 2 s in all). It is never spooled or retried, since a late heartbeat is worth nothing, and it does not take part in the back-off of the events: the backend may answer heartbeats while its database refuses events. A tick is skipped while the previous heartbeat is still waiting. Only the first failure after a success is logged; the totals are in the line `heartbeat sent=... failed=...`.

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
a line `events published=58 dropped=0 pipeline_run_id=...`, with `--backend-url` a line `backend spooled=0 rejected=0 lost=0`, with `--snapshot-dir` a line `snapshots written=56 failed=0 events_with_snapshot=58 probe_blocked_ms total=... max=...`, and the elapsed time and the overall frame rate (it includes the start-up). While it runs it prints the frame rate of every
source each 5 seconds. The first run
spends about 2.5 minutes building the TensorRT engine; it is saved next to the ONNX and reused (a run then starts in
seconds). `configs/pgie_yolov8n.txt` points at the engine for N = 2; started by hand with another N, `nvinfer` builds a new engine
(`yolov8n.onnx_b<N>_gpu0_fp16.engine`) on every run until you change the `model-engine-file` line (the Engine manager and
`engine/tools/benchmark.py` do that for you, one engine file per N). `configs/pgie_sample.txt`
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
  RTSP sources never end, so stop the engine with Ctrl-C (SIGINT) or SIGTERM: it then sends EOS into the muxer so that the
  MP4 is finalized and playable (it waits up to 5 s for that; a second signal stops at once, leaving a file that cannot be played).
- `--snapshot-dir DIR`: save a JPEG of the whole frame for each event in DIR (created if missing). Without it no snapshots are taken.
- `--snapshot-quality Q`: JPEG quality 1-100 (default 85).
- `--backend-url URL`: POST every event as JSON to URL (e.g. `http://127.0.0.1:8000/internal/events`); must be given together with `--spool-file`. Without both, events are printed on stdout.
- `--spool-file FILE`: JSON-lines file in which events wait while the backend cannot be reached (at most 256 MB, then events are lost and counted as `lost=`).
- `--stream-url BASE`: publish every camera, with boxes and track IDs, over RTMP to `BASE/cam<ID>` (e.g. `rtmp://127.0.0.1:1935/live`; the URL must have an application part such as `/live`, `rtmp2sink` refuses a bare host). One H.264 encoder per camera; cannot be combined with `--output`. See [Streaming](#streaming).
- `--stream-bitrate KBPS`: bit rate of each published stream, 100-50000 (default 4000).
- `--heartbeat-url URL`: POST the state of every camera to URL (e.g. `http://127.0.0.1:8000/internal/heartbeat`) every `--heartbeat-interval` seconds (1-3600, default 5); needs `--backend-url`. Off without it.
- `--absence-timeout SEC`: how long a person must be out of sight before the same track ID raises a new event (default 10, same as `ABSENCE_TIMEOUT`).
- `--fps-interval SEC`: how often the frame rate of each source is printed (default 5, 0 turns it off).
- `--tracker-config FILE`: the `nvtracker` low-level config (required), `configs/tracker_nvdcf.yml` for NvDCF. The values in
  it are a starting point, not tuned.

- `--rtsp-timeout SEC`: an RTSP camera that delivers no frame for SEC seconds is reconnected (default 10, 0 = only react to errors). See [RTSP cameras](#rtsp-cameras).

Exit codes: `0` all sources reached EOS, or the engine was stopped with SIGINT/SIGTERM, `1` a pipeline error, `2` bad arguments, `3` the pipeline could not be built.
The program never prints a full URI, only its scheme.

### Streaming

With `--stream-url` the tail of the pipeline is not a `fakesink` but `nvstreamdemux`, which splits the batch into one branch per
camera: `queue ! nvvideoconvert ! nvdsosd ! nvvideoconvert ! I420 ! nvv4l2h264enc ! h264parse ! appsink`. `nvdsosd` draws the
boxes and track IDs from the metadata. The branch ends in an `appsink` that hands the encoded frames to a small **publisher pipeline of
its own** per camera (`appsrc ! h264parse ! flvmux ! rtmp2sink`), so nothing that happens at the media server can reach detection:

- MediaMTX down, unreachable or restarted: the publisher reports an error, is thrown away and built again every 5 s (it joins at the next
  key frame, within a second). Detection, events and the frame rate of every camera are untouched (measured: 30 fps throughout), and the
  stream is back by itself when the server is (measured, with the boxes and track IDs).
- The first version put `rtspclientsink` in the branch; it deadlocks in its state change after a failed connection (GStreamer 1.20.3), and a
  branch that is restarted in place does not work with `nvstreamdemux` either (after the new bin is linked to the same pad only one frame
  arrives). Hence RTMP, and a publisher that lives outside the main pipeline.
- Frame buffers: every frame of the queue is a whole `nvstreammux` batch buffer, and that pool holds 4 by default, so the queue keeps one
  and the muxer pool is made `4 + number of cameras` (at most 16). With the first sizes (4 per queue) a stuck branch starved the pool and
  stopped the detection of all cameras for several seconds.
- Pacing: with live (RTSP) sources frames go out as they come and an overloaded stream drops the oldest. With only files the `appsink`
  waits for the clock, so a file plays in real time for the viewer (48 s for a 48 s video) instead of finishing in 4 s, also while the
  server is down.

### RTSP cameras

An RTSP source never "ends": the engine keeps running until it is stopped with SIGINT/SIGTERM (Ctrl-C), which stops the
pipeline in order, prints the summary and lets the publisher write what is left to the spool. When a camera goes away
(connection refused, network cut, a camera that hangs without closing the connection, or a stream that ends), only that
source is affected: the others keep their frame rate. `nvurisrcbin` restarts its RTSP client every `--rtsp-timeout`
seconds until the camera answers; the engine notes the loss, reports the source as `RECONNECTING` in the periodic
`fps source_id=... RUNNING|RECONNECTING` lines, logs `reconnected` when frames flow again, and ends with a line
`source_id=... state=RUNNING reconnects=N`. A camera that is down when the engine starts is handled the same way. After
a reconnect the event clock of that source is re-anchored, so a person who comes back after more than the absence
timeout raises a new event. Errors of a source that is not RTSP (a file that cannot be read) still stop the engine
with exit code 1.

To try it without a camera, build the fake RTSP server inside the DeepStream container (`cd engine && make
rtsp-test-server`), serve a video with `build/rtsp_test_server FILE.mp4 8554 /cam`, point `--camera 1=rtsp://127.0.0.1:8554/cam`
at it, and stop and start the server, or pause it with `kill -STOP`, to make the camera disappear.

To find out how many cameras one GPU carries, run `engine/tools/benchmark.py` inside the same container (usage in its
docstring). It serves one 1080p video as N real-time RTSP cameras, runs either one engine with N cameras or N engines with one
camera each, and prints FPS per camera, GPU/NVDEC/VRAM, CPU and RAM. On an RTX 4060 Laptop (8 GB) one engine held 30 fps on
20 cameras (GPU 75%), was at its edge on 24 (NVDEC 99%) and failed on 28; one engine per camera used about 4x more RAM and 3x
more CPU at 8 cameras. Latency is not measured, and all cameras show the same video, so treat it as an upper bound for 1080p H.264.

Run the tests of the pure-C code (`engine/src/core/`: the `Detection` box normalisation, the event manager, which replays the shared vectors of `tests/vectors/`, the event JSON, the heartbeat JSON, the spool file and the retry delays) on the host, no
DeepStream or GPU needed; built with AddressSanitizer and UBSan:

```bash
cd engine && make test-core
```

The DeepStream sample sources are under NVIDIA's proprietary license, so they are not copied into this repository.
