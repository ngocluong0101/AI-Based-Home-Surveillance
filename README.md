# AI-Based Home Surveillance

Detects and tracks people on several cameras at once, raises one `PERSON_DETECTED` event per person with a snapshot,
shows everything live in a web app and sends Telegram alerts.

**[Live demo](https://ngocluong0101.github.io/AI-Based-Home-Surveillance/)**: the real web app in your browser with made-up
cameras and people, so nothing has to be installed (no GPU, no backend). Add and start cameras, open events, try the wall; a
new detection arrives every 15-30 s. The code is in `frontend/src/demo/`.

- **Engine** (C, NVIDIA DeepStream): N cameras (RTSP or video files) in one GPU pipeline, YOLOv8n + NvDCF tracking.
- **Backend** (FastAPI + PostgreSQL): cameras, events, live updates over WebSocket, Telegram, snapshot cleanup.
- **Web app** (React): dashboard, camera wall, live video with boxes, event gallery, camera management.
- **Runs on one machine in a LAN** with Docker Compose. No login (do not expose it to the internet as it is).

## How it works

```text
 cameras (RTSP / files)
        |
        v
 engine container ─────────────────────────────────────────────────────────────┐
 | ds_engine: N sources -> nvstreammux -> nvinfer (YOLOv8n, TensorRT FP16)     |
 |   -> nvtracker (NvDCF) -> pad probe -> event manager per camera -> publisher |
 |   -> nvstreamdemux -> boxes drawn -> H.264 encoder per camera -> RTMP        |
 | Engine manager (Python): starts ds_engine and keeps it in step with the list |
 └──────┬─────────────────────────────────────────────────┬────────────────────┘
   events, heartbeats (HTTP + token)                  RTMP video
        v                                                  v
 backend (FastAPI) ── PostgreSQL                    MediaMTX ── WebRTC ──┐
   │  └─ Telegram                                                       │
   └─ REST + WebSocket ── nginx ──> browser (React app) <───────────────┘
```

- **One process = one pipeline = N cameras.** Batching all cameras into one inference call beats N engines of one camera
  (8 cameras: a quarter of the RAM, a third of the CPU).
- **The pad probe never blocks.** It copies what it needs and queues it; a publisher thread does HTTP, disk and retries.
  A dead backend never slows the video.
- **No event is lost or doubled.** Each event has a UUID made by the engine; the backend stores it once. While the backend
  is unreachable events wait in a spool file and are delivered later.
- **A person is reported once per track**, and again only after being out of sight for `absence_timeout` seconds.
  `src/events/person_detected.py` is the reference; the C port passes the same test vectors.
- **The backend decides, the manager applies.** Cameras and their wanted state live in PostgreSQL. Adding, removing or
  re-configuring a camera reaches the running engine in a second or two (SIGHUP) without interrupting the others; the engine
  restarts only when there are more cameras than places (`ENGINE_MAX_CAMERAS`).

## Quick start (Docker)

Needs Docker with the NVIDIA Container Toolkit and a GPU. Once, on the host, export the model:

```bash
pip install -r requirements.txt
python engine/tools/export_yolov8_onnx.py      # yolov8n.pt -> engine/models/yolov8n.onnx
cp .env.example .env
```

Edit `.env`: `POSTGRES_PASSWORD` (letters and digits), `INTERNAL_TOKEN` (`openssl rand -hex 24`), `PUBLIC_HOST` (the address
browsers use to reach this machine), and the Telegram settings if wanted. Then, from the repository root:

```bash
docker compose --env-file .env -f deploy/compose.yml up -d --build
```

Open `http://<PUBLIC_HOST>:8080`, go to **Cameras → Add camera**, press **Test connection**, **Save**, then **Start**.
No camera at hand? Put an MP4 in `data/videos/` and use `file:///videos/<name>.mp4`.

The first build pulls the DeepStream image (~32 GB). The first start with a new number of camera places builds a TensorRT
engine (2-3 min; `docker compose logs -f engine`); later starts take seconds.

| Task | Command (prefix: `docker compose --env-file .env -f deploy/compose.yml`) |
|---|---|
| Apply code changes | `up -d --build <service>` (e.g. `frontend`); `start` only restarts the old image |
| Pause / resume | `stop` / `start` |
| Remove, keep data | `down` (`up -d` brings it back) |
| Remove **and delete** database + snapshots | `down -v` (dumps in `data/backups/` survive) |
| Logs | `logs -f engine` |

| Container | Published | Notes |
|---|---|---|
| `frontend` (nginx) | `HTTP_PORT` (8080) | the app; forwards only `/api` (REST + WebSocket), `/internal` answers 404 |
| `mediamtx` | 8889/tcp, 8189/udp | WebRTC to browsers (`PUBLIC_HOST` goes into its candidates) |
| `backend`, `db`, `engine`, `backup` | nothing | the engine manager (port 8100, no auth) is reachable only inside Docker |

Every container restarts by itself (`unless-stopped`) except ones you stopped yourself. Stop the engine gracefully (it flushes
pending events, up to 45 s); do not kill it. A `file://` camera stops at the end of the file ("Ended"); press Start to replay.

**Backups.** The `backup` container dumps the database daily into `data/backups/` (keeps 7, verified after writing; snapshots are
not backed up). Copy that folder to another disk now and then. Restore into an empty database:

```bash
docker compose --env-file .env -f deploy/compose.yml stop backend
docker compose --env-file .env -f deploy/compose.yml exec -T db pg_restore -U surveillance -d surveillance --clean --if-exists --no-owner < data/backups/surveillance-<time>.dump
docker compose --env-file .env -f deploy/compose.yml start backend
```

## Web app

| Page | What it does |
|---|---|
| **Dashboard** | cameras / online / people now / events today, recent events, one card per camera with its last snapshot (click to watch live), people and FPS chips, Start/Stop |
| **Wall** | all cameras in a 1×1, 2×2 or 3×3 grid; at most 4 tiles play live (bandwidth), the rest show a snapshot and *Go live* |
| **Monitoring** | live WebRTC video with boxes and track IDs, events today, last detection, latest events, engine details under *Diagnostics* |
| **Events** | gallery of snapshots grouped by day (or a table), filters by camera / status / date, detail drawer with *Acknowledge* |
| **Cameras** | add / edit / delete, Start/Stop, **Test connection** (reachable, login, stream, codec, resolution), per-camera detection settings, free engine places |
| **Settings** | **Alerts on/off** (off: events are still saved and shown, no Telegram message) and **quiet hours**; Telegram status with a *Send test message* button (the token is never shown); how long snapshots are kept and how much space they use; what a new camera starts with; engine and version (read-only) |

New events and camera states arrive over `WS /api/ws` without reloading (toast + highlight). Every screen has loading, empty and
error states. Dark theme, keyboard and screen-reader friendly (axe WCAG 2.1 A/AA checked).

## Cameras and detection settings

Cameras are managed in the web app (or `POST /api/cameras`), not in `.env`. The camera `id` is the `camera_id` in the engine's
events. Addresses are stored as given (the engine needs the password) but every API response masks them
(`rtsp://***@host/path?***`). Deleting a camera is a soft delete: its events and snapshots stay.

| Setting | Default | Meaning |
|---|---|---|
| `conf_threshold` (0-1) | 0.25 | detections below it are dropped before tracking: no alert, not counted, no box. The detector already ignores < 0.25 (`pre-cluster-threshold` in `engine/configs/pgie_yolov8n.txt`), so lower values change nothing |
| `absence_timeout` (s) | 10 | out-of-sight time before the same track raises a new alert |
| `ai_enabled` | on | off keeps the picture but detects nobody ("people" reads unknown, not 0). The GPU still runs the frames |

A change reaches the running engine within a second or two; renaming a camera changes nothing in the engine.

## Configuration

Environment variables, loaded from `.env` (`.env.example` lists them; `src/config/__init__.py` is the only place that reads them).
Under Docker, compose sets paths and URLs itself and takes only those marked *compose*.

| Variable | Default | Meaning |
|---|---|---|
| `POSTGRES_PASSWORD`, `INTERNAL_TOKEN` | required | *compose*: database password; secret the engine sends as `X-Internal-Token` |
| `PUBLIC_HOST`, `HTTP_PORT` | `localhost`, `8080` | *compose*: address browsers use (WebRTC); port of the web app |
| `DATABASE_URL` | required by the backend | e.g. `postgresql+psycopg://surveillance@127.0.0.1:5432/surveillance` |
| `ENABLE_TELEGRAM`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | `false`, – | Telegram alerts (*compose*) |
| `ABSENCE_TIMEOUT` | `10` | engine default for a camera without its own value (*compose*) |
| `ENABLE_SNAPSHOT`, `SNAPSHOT_DIR` | `true`, `data/snapshots` | the engine writes one JPEG per event frame; the backend serves them from the same directory |
| `SNAPSHOT_RETENTION_DAYS`, `SNAPSHOT_CLEANUP_INTERVAL` | `7`, `100` | older snapshots are deleted (events keep their data, lose the picture); cleanup every N new snapshots and at least hourly |
| `BACKUP_INTERVAL_SECONDS`, `BACKUP_KEEP`, `BACKUP_PATH` | `86400`, `7`, `../data/backups` | *compose*: database dumps |
| `ENGINE_MANAGER_URL` | – | where the backend finds the manager; unset = the backend only records wanted states |
| `ENGINE_MAX_CAMERAS` | `8` | places in the running engine; cameras come and go without a restart while they fit. Empty places cost a little GPU/RAM (4 cameras in a batch of 12: +4 GPU points, +260 MiB VRAM) and need a TensorRT engine of that batch size. `0` = no spare places |
| `ENGINE_COMMAND`, `ENGINE_CONFIG`, `ENGINE_TRACKER_CONFIG`, `ENGINE_RUNTIME_DIR`, `ENGINE_EXTRA_ARGS` | see `.env.example` | how the manager starts the engine |
| `ENGINE_BACKEND_URL`, `ENGINE_HEARTBEAT_URL` | `http://127.0.0.1:8000/internal/events`, derived | where the engine posts events and heartbeats |
| `ENGINE_STREAM_URL`, `MEDIAMTX_WEBRTC_URL`, `MEDIAMTX_PATH_PREFIX` | empty = no video, –, `live` | publishing and watching live video |
| `HEARTBEAT_STALE_AFTER`, `NOTIFY_MAX_AGE` | `15`, `600` | seconds of silence before a camera counts offline; age after which a pending Telegram alert is dropped |
| `LOG_LEVEL`, `LOG_FILE`, `LOG_MAX_BYTES`, `LOG_BACKUP_COUNT` | `INFO`, `logs/app.log`, 5 MB, `3` | rotating log file + terminal |

`SNAPSHOT_RETENTION_DAYS` and `ABSENCE_TIMEOUT` are starting values: once someone changes the same setting on the **Settings** page,
the stored value (in the database, table `app_settings`) wins. Nothing is stored until then, so an existing installation behaves as before.

What counts as a person (`pre-cluster-threshold`, NMS) is in `engine/configs/pgie_yolov8n.txt`; tracking behaviour is in
`engine/configs/tracker_nvdcf.yml`. Old `.env` lines `RTSP_URL`, `VIDEO_SOURCE`, `CONFIDENCE_THRESHOLD`, `RECONNECT_DELAY`,
`APP_ENV`, `MP4_PATH` are read by nothing; delete them.

**Telegram.** Create a bot with BotFather, get your chat ID, set the three variables above. The backend sends the alerts (photo if
the snapshot exists, text otherwise), never the engine. A Telegram outage never loses or delays an event: alerts are retried with
a growing pause (1 s → 60 s) and given up after `NOTIFY_MAX_AGE`. Try it: `python scripts/test_telegram_snapshot.py`, or press *Send test message* on the Settings page.
When alerts are switched off, or an event arrives in the quiet hours, the event is stored and shown as usual and its `notify_status`
is `skipped` (the other values: `pending`, `sent`, `disabled` = Telegram not set up, `failed`).

## Development without Docker

The host has no DeepStream, so the engine runs in the DeepStream container and the rest on the host.

```bash
# 1. PostgreSQL (once; keep the password out of the URL: ~/.pgpass or PGPASSWORD)
sudo -u postgres psql -c "CREATE ROLE surveillance LOGIN PASSWORD '<password>'" \
                      -c "CREATE DATABASE surveillance OWNER surveillance"
# 2. set DATABASE_URL in .env, then
alembic -c backend/alembic.ini upgrade head
uvicorn backend.main:create_app --factory --port 8000                      # API docs at /docs
uvicorn engine_manager.service:create_app --factory --host 127.0.0.1 --port 8100
cd frontend && npm install && npm run dev                                  # http://localhost:5173, proxies /api to :8000
```

Build the engine once, inside the DeepStream container (`nvcr.io/nvidia/deepstream:7.1-triton-multiarch`, `--gpus all`, repo mounted):

```bash
cd engine && make CUDA_VER=12.6 && make -C parser CUDA_VER=12.6
```

To let the manager start the engine in the container, make `ENGINE_COMMAND` the whole prefix, with the runtime dir, snapshot dir,
`engine/models` (writable) and configs mounted at the same absolute paths:

```bash
ENGINE_COMMAND="docker run --rm --network host --gpus all -u 1000:1000 -e DS_ENGINE_TOKEN -v $PWD:$PWD -w $PWD --entrypoint $PWD/engine/build/ds_engine nvcr.io/nvidia/deepstream:7.1-triton-multiarch"
```

For live video run MediaMTX (`docker run -d --rm --network host bluenviron/mediamtx:latest`; RTMP 1935, WebRTC 8889) and set
`ENGINE_STREAM_URL=rtmp://127.0.0.1:1935/live` and `MEDIAMTX_WEBRTC_URL=http://<address the browser uses>:8889`.
`BACKEND_URL=http://host:8000 npm run dev` points the dev server at another backend.

The **demo** (`npm run dev:demo`, `npm run build:demo` → `frontend/dist-demo`) is the same app with a small backend written in
the browser (`frontend/src/demo/`): five made-up cameras, events with drawn snapshots, people walking through drawn rooms
instead of WebRTC video. It is published to GitHub Pages by `.github/workflows/pages.yml` on every push to `main` that touches
`frontend/` (once: repository Settings → Pages → Source: GitHub Actions). A normal build never loads the demo code.

TensorRT engines are per batch size (`engine/models/yolov8n.onnx_b<N>_gpu0_fp16.engine`); the manager picks the right one.
**Delete them whenever you export the ONNX again.**

## Engine (`ds_engine`)

Normally the manager starts it. By hand:

```bash
./build/ds_engine --config configs/pgie_yolov8n.txt --tracker-config configs/tracker_nvdcf.yml \
                  --camera 1=file:///a.mp4 --camera 2=file:///b.mp4 --output build/out.mp4
```

| Option | Meaning |
|---|---|
| `--config FILE`, `--tracker-config FILE` | `nvinfer` and `nvtracker` configs (required) |
| `--camera ID=URI` | a source; repeat per camera |
| `--cameras-file FILE` | sources with passwords and per-camera settings, mode 600 (keeps secrets out of `ps`); read again on SIGHUP |
| `--max-cameras N` | places for N cameras (0-32) so cameras can be added while running |
| `--backend-url URL`, `--spool-file FILE` | POST events (retry + spool); both or neither. Token in env `DS_ENGINE_TOKEN`, never in argv |
| `--heartbeat-url URL`, `--heartbeat-interval SEC` | send each camera's state, FPS and people count (default 5 s) |
| `--snapshot-dir DIR`, `--snapshot-quality Q` | one JPEG of the frame per event (default quality 85) |
| `--stream-url BASE` (`--stream-bitrate KBPS`) | publish each camera with boxes over RTMP to `BASE/cam<ID>` (default 4000 kbit/s); not with `--output` |
| `--output FILE` | debug: tiled MP4 with boxes; stop with Ctrl-C so the file is finalized |
| `--absence-timeout SEC`, `--rtsp-timeout SEC`, `--fps-interval SEC` | default re-alert delay (10), RTSP reconnect after SEC without frames (10), FPS print interval (5, 0 = off) |

Exit codes: `0` all sources ended or stopped by SIGINT/SIGTERM, `1` pipeline error, `2` bad arguments, `3` pipeline not built.

- **Cameras file** (`[camera.<id>]` with `uri=`; optional `conf_threshold=`, `absence_timeout=`, `ai_enabled=`). A bad value exits with
  code 2 without printing the address.
- **Changing cameras while running (SIGHUP).** The engine re-reads the file: new cameras take free places, removed ones are
  released, the settings of the rest are updated. All or nothing: if the file is invalid or needs more places than are free,
  nothing changes and it prints `reload: nothing applied, ...` (the manager then restarts it).
- **RTSP.** A lost camera is `RECONNECTING` while the others keep their frame rate; `nvurisrcbin` retries every `--rtsp-timeout`.
  A video file reports `ENDED` at its end; the engine exits 0 when all sources ended. An unreadable *file* still stops the engine.
- **Delivery.** HTTP 2xx or 409 = delivered; 400/413/422 = refused for good; anything else is retried with back-off 1 s → 60 s,
  and meanwhile events go to the spool (fsync'd, max 256 MB, delivered oldest first, also after a restart). Snapshots are written
  to disk by the engine; the backend serves them from the same directory.
- **Live video.** One H.264 encoder per camera (the GPU has 12 NVENC sessions, so ~12 cameras with video). Each camera publishes
  through a small pipeline of its own; if MediaMTX dies, only that publisher is rebuilt every 5 s and detection is untouched.
- **Event schema v1** (one JSON per event):

```json
{"schema_version":1,"event_id":"<uuid>","camera_id":1,"pipeline_run_id":"<uuid>","type":"PERSON_DETECTED",
 "track_id":12,"confidence":0.9100,"timestamp":"2026-01-01T12:00:00.123Z","bbox":[0.1,0.2,0.3,0.4],"snapshot":null}
```

`timestamp` is UTC (stream start + PTS), `bbox` is x, y, w, h relative to the frame, `snapshot` is a file name like
`PERSON_DETECTED_cam1_id12_20260101T120000_123e4567.jpg` or `null`. A person is `(pipeline_run_id, camera_id, track_id)`:
track IDs restart with each engine run.

Model note: on a 1442-frame test video boxes match Ultralytics (IoU 0.95-0.99) but the engine finds ~10% fewer people at the same
0.25 threshold (boxes near the threshold). NvDCF gave 21 tracks vs 36 for ByteTrack; there is no ground truth yet to measure ID switches.

## Backend API

FastAPI + SQLAlchemy 2 + Alembic on PostgreSQL; interactive docs at `/docs`.

| Endpoint | |
|---|---|
| `GET /api/health` | 200, or 503 when the database is unreachable |
| `GET/POST /api/cameras`, `GET/PATCH/DELETE /api/cameras/{id}` | cameras (soft delete) |
| `POST /api/cameras/test-connection` | body `{source_type, source_uri}` or `{camera_id}`; checks connect, login (Basic/Digest), stream, codec (H.264/H.265), resolution. A broken camera is `ok:false` with a reason, not an HTTP error |
| `POST /api/cameras/{id}/start`, `/stop` | set the wanted state (applied in the background; a disabled camera answers 409) |
| `GET /api/cameras/{id}/status` | wanted state, engine process state, and what the engine reports: `state` (`RUNNING`/`RECONNECTING`/`ENDED`), `fps`, `people`, `last_frame_age` |
| `GET /api/cameras/{id}/stream` | `{whep_url, player_url, available}` for live video; 503 until `MEDIAMTX_WEBRTC_URL` is set |
| `GET /api/engine` | engine as a whole: manager reachability, `capacity`, `running`, `wanted`, free `room` |
| `GET/PATCH /api/settings` | what the Settings page changes: `alerts_enabled`, `quiet_hours_enabled`, `quiet_start`, `quiet_end` (`"HH:MM"`, server's local time), `snapshot_retention_days` (1-365), `default_conf_threshold`, `default_absence_timeout`; plus read-only `telegram_configured`, `snapshot_count`, `snapshot_bytes`, `version`. PATCH takes only the fields to change (unknown field, bad value, or quiet hours that start and end at the same minute: 422) |
| `POST /api/settings/test-notification` | sends a test message to Telegram (whatever the alert switch says); `{ok, message}`, a Telegram that is not set up or refuses is `ok: false`, not an HTTP error |
| `GET /api/events` | newest first; filters `camera_id`, `from`, `to`, `type`, `status`; `limit` (1-200), `offset`; answers `{items, total, limit, offset}` |
| `GET /api/events/{id}`, `.../snapshot` | one event; its JPEG |
| `PATCH /api/events/{id}` | `{"status": "new" \| "acknowledged"}` |
| `WS /api/ws` | server → client `{type, data}`: `event.created`, `event.updated`, `camera.status`. No replay (read current state over REST); a client 100 messages behind is closed (1013) |
| `POST /internal/events`, `/internal/heartbeat` | for the engine, with header `X-Internal-Token` when `INTERNAL_TOKEN` is set. Events are idempotent on `event_id`: 201 stored, 200 duplicate, 422 can never be stored (engine drops it) |

The browser talks to one origin, so no CORS setup is needed. Engine manager API (loopback, no auth): `PUT /cameras`, `GET /status`.

## Performance

RTX 4060 Laptop (8 GB), 1080p H.264 at 30 fps, `engine/tools/benchmark.py` (fake RTSP cameras, 25 s window):

| Setup | Cameras | FPS each | GPU / NVDEC | RAM |
|---|---|---|---|---|
| one engine, detection only | 8 | 30 | 33% / 31% | 1.2 GiB |
| one engine, detection only | 16 | 30 | 57% / 56% | 1.5 GiB |
| one engine, detection only | 20 | 30 (dips to 24) | 75% / 74% | 1.8 GiB |
| one engine, detection only | 24 | 30 (dips to 22.5) | 93% / 99% | 2.0 GiB |
| one engine, detection only | 28 | 25.5, fails | 92% / 100% | 2.2 GiB |
| 8 engines of one camera | 8 | 30 | 41% / 25% | 5.1 GiB |
| one engine, detection + live video | 12 | 30 | 44% / 40% (NVENC 27%) | 2.2 GiB |

The decoder (NVDEC) is the limit, not the network. With live video the limit is 12 cameras (NVENC sessions). Every camera showed the
same video on localhost and latency was not measured: take these as an upper bound for 1080p H.264 at 30 fps.
A 4-hour soak with 4 cameras (`deploy/soak.py`) kept 30 fps and never restarted anything, but engine RAM grew ~55 MiB/hour
(930 → 1081 MiB, slowing at the end); cause not found yet, so watch it on 24/7 use.

## Tests

```bash
pytest                                  # backend, manager, event logic, shared vectors (backend tests start a throwaway PostgreSQL; need its server programs installed)
cd engine && make test-core             # pure-C core under ASan/UBSan, no GPU
cd frontend && npm test                 # unit tests (vitest)
cd frontend && npm run e2e              # end-to-end + accessibility in Chrome (own backend, DB and fake engine on ports 18765/18766/5174)
RUN_GPU_TESTS=1 pytest tests/engine_real -v   # the real engine on the GPU, ~5 min, GPU must be idle
```

`tests/vectors/event_manager_*.json` are generated from the Python `PersonDetectedEventManager` and replayed on both the Python and the
C implementation so the two cannot drift apart. No test touches your database, your `.env` Telegram settings or Telegram itself.
Tools in `engine/tools/`: `fake_receiver.py` (fake backend), `rtsp_test_server.c` (fake RTSP camera: `make rtsp-test-server`),
`benchmark.py`, `check_reload.sh`, `check_dynamic.sh`. `deploy/soak.py` runs the whole stack for hours and reports drift.

## Troubleshooting

| Symptom | Fix |
|---|---|
| compose says "required variable ... is missing" | set `POSTGRES_PASSWORD` and `INTERNAL_TOKEN` in `.env` and pass `--env-file .env` |
| No change after editing the frontend | `up -d --build frontend`, then hard-refresh (Ctrl+Shift+R) |
| "Connection lost" in the app, `/api/ws` gives 404 | `websockets` is missing; reinstall requirements / rebuild the backend image |
| Camera silent for 2-3 min after Start | a TensorRT engine for this number of places is being built (first time only) |
| "Could not open resource", "Unauthorized" | wrong address or login; use **Test connection**. Free public RTSP links with `?token=` expire within a day |
| Video black or stuck on "connecting" | `PUBLIC_HOST` is not the address the browser uses, or 8889/tcp / 8189/udp is blocked |
| No events in the app | the camera must exist in the backend with the id the engine uses (else 422, dropped); a wrong `INTERNAL_TOKEN` gives 401 and events wait in `events.spool` |
| No Telegram message | `ENABLE_TELEGRAM` false or token / chat ID empty; try `scripts/test_telegram_snapshot.py` |
| All cameras blink when one is added | more cameras than `ENGINE_MAX_CAMERAS` places: the engine restarted once with room for the new count |
| `file://` camera stopped and Start does nothing | the file ended; Stop, then Start |
| `--output` MP4 cannot be played | stop with a single Ctrl-C and wait up to 5 s |
| nginx 502 right after the backend was recreated | wait ~10 s |
| A stopped container stays down | by design for `docker stop/kill`; `up -d` starts it |

## Repository layout

| Path | Content |
|---|---|
| `engine/` | `src/ds_engine.c` (DeepStream pipeline), `src/core/` (pure C with tests), `configs/`, `parser/` (YOLOv8 box parser), `tools/` |
| `engine_manager/` | service that supervises `ds_engine` |
| `backend/` | FastAPI app, models, Alembic migrations, WebSocket hub, Telegram notifier, snapshot janitor |
| `frontend/` | React + TypeScript + Vite app (`src/{api,websocket,hooks,components,layouts,pages,utils}`), `e2e/` |
| `src/` | shared Python: config, logging, `PERSON_DETECTED` specification, Telegram notifier, snapshot cleanup |
| `deploy/` | Dockerfiles, `compose.yml`, nginx and MediaMTX config, backup and soak scripts |
| `tests/`, `scripts/` | pytest suites and shared vectors; manual helper scripts |

The DeepStream sample sources are under NVIDIA's proprietary license and are not copied into this repository.
