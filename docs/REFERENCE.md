# Reference: AI-Based Home Surveillance (full documentation)

The short version is the [README](../README.md). This page keeps everything else: all measurements with their caveats, the
Docker and no-Docker set-up, every setting, the engine's command line, the backend API, tests and troubleshooting.

## Results

Four numbers define whether this works (definitions: [`eval/DEFINITIONS.md`](../eval/DEFINITIONS.md); every number is a file in
[`eval/results/`](../eval/results/) and can be re-measured with [`eval/README.md`](../eval/README.md)). Read the last column before
you trust a number.

| | What it measures | Result | How far to trust it |
|---|---|---|---|
| **M1** fall recall | falls caught, per event | skeleton network (ST-GCN, trained on UR Fall + GMDCSA-24, alert threshold 0.95): **28 of 30** falls (27 and 29 in two runs); the hand-written rules: 14 of 30 | UR Fall dataset (30 falls, 40 daily-activity sequences), one room, 5-fold cross-validation by sequence, 2 seeds (GMDCSA-24 is in every training part). The rules' threshold was tuned on 5 of these clips, so the rules' number is **optimistic**. **No footage of my own yet.** |
| **M2** false alarms | alerts with no fall | network: **1 of 40** activity sequences, plus 1 stray alert in the 30 fall clips; rules: 6 of 40. **The VLM check is a trade, and on other data a bad one:** on UR Fall it removed 2 of the rules' 6 false alarms and lost 0 of 14 real falls, but on GMDCSA-24 it would have silenced **9 of the network's 56 real alerts (16%)** to remove 3 false ones (details below), so a doubted alert is sent anyway with a note | same dataset and caveats. **False alarms per camera-hour are not measured**: that needs hours of real, ordinary household footage, which I do not have. |
| **M3** throughput | frames per second per camera with N cameras | **30 fps on every camera at N = 1, 4, 8 and 12** (1080p H.264, pose + fall network + frame ring + video output), on a laptop RTX 4060 8 GB | fake RTSP cameras replaying one crowded clip. With the VLM answering alerts at the same time **8 cameras hold 30 fps; 12 do not fit in 8 GB**: only ~100 MiB are left, and one run in five crashed. The engine now refuses to start when its own estimate of the memory it needs is above what is free; that catches 16 places, but not the borderline 12. |
| **M4** latency | fall to alert | the network alerts **0.18 s after it decides** (0.1 s to see 3 scores in a row, 0.08 s transport), on all 10 trials; the rules alert about 1.1 s after they see the person hit the floor, because they wait to see them stay down, and caught 12 of 15 trials. With the VLM check the Telegram message arrives about 4.5 s after the decision (2.5 s waiting for the frames after the fall, 1.9 s for the VLM) | UR Fall clips replayed in real time through the real engine, backend and WebSocket. The dataset's "impact" labels are coarse. Telegram not timed. |

**A number that moved.** UR Fall is 4:3 and the engine used to stretch every non-16:9 picture to fill a 16:9 frame, which made people 1.33 times too wide and tilted every body angle. I fixed that (the picture now keeps its shape, with black bars) and re-measured. With the old stretch the network scored 29.3 of 30 falls and the rules 20 of 30 (with 11 of 40 false alarms); with correct geometry 27.3 and 14 (6 of 40). The numbers above are the new ones; the old ones are kept in `eval/results/` from 2026-11-19. The cameras this is meant for are 16:9, where nothing changed.

**Tested on recordings it was not built on.** UR Fall is where the network was trained and the rules tuned, so those numbers are the best case. I ran the same system, with nothing retuned, on [GMDCSA-24](https://zenodo.org/records/12921216) (4 actors, 3 home setups, 720p and 480p, 160 clips) and on [AIRTLab](https://github.com/airtlab/A-Dataset-for-Automatic-Violence-Detection-in-Videos) (350 clips of acted fights and look-alikes). These are clip-level counts (did the detector raise an alert anywhere in the clip), with 95% confidence intervals; the dataset papers are cited at the end of this section.

| | UR Fall (built and tuned here, cross-validated) | GMDCSA-24 (never seen) |
|---|---|---|
| network (ST-GCN), falls caught | 27.3 of 30 (91%) | **59 of 79 (75%**, CI 64-83%) |
| network, false alarms in daily-activity clips | 0.7 of 40 (2%) | **11 of 81 (14%**, CI 8-23%) |
| rules (tuned on UR Fall), falls caught | 14 of 30 (47%) | 47 of 79 (59%, CI 48-70%) |
| rules, false alarms | 6 of 40 (15%) | 8 of 81 (10%, CI 5-18%) |

Training on both datasets helps with the false alarms. I trained one network on UR Fall and GMDCSA-24 together (GMDCSA-24 only says "this clip has a fall", not when, so the training picks the most fall-like moment in each fall clip, a multiple-instance setup) and tested it leaving one actor out at a time, with a UR Fall-only network trained the same way as the control (2 seeds each, clip level):

| | UR Fall network | UR Fall + GMDCSA-24 network |
|---|---|---|
| GMDCSA-24, a new actor, alert threshold 0.9: falls caught / false alarms | 73% / 14% (11 of 81 clips) | **73% / 6%** (5 of 81) |
| the same at threshold 0.95 | 67% / 7% | 68% / 4% |
| UR Fall (cross-validation), threshold 0.9: falls caught / false alarms | 90% / 1% | 97% / 7.5% |
| the same at threshold 0.95 | 85% / 1% | 93% / 2.5% |

So the second dataset cut the false alarms on new actors about in half (consistent in both seeds, but 11 versus 5 clips of 81 is a small difference) without losing recall, and at threshold 0.95 UR Fall does not get worse. It is not a test on a new room: the 4 actors of GMDCSA-24 share 3 setups, so the held-out actor was filmed in rooms the network had seen. The network the repository uses now is the second one (UR Fall + GMDCSA-24, threshold 0.95; new cameras start with 0.95, and 0.9 stays available per camera).

**The VLM check on GMDCSA-24.** I asked the VLM about every alert the detectors raised on GMDCSA-24 (6 frames from 2.5 s before to 2 s after the alert, silenced when `p_yes` < 0.1, the deployed setting). An alert counts as true when it is in a fall clip:

| alerts of | alerts (true / false) | false alarms the VLM removes | real alerts it silences |
|---|---|---|---|
| the network trained on both datasets, held-out actors, threshold 0.95 | 56 / 3 | 3 of 3 | **9 of 56 (16%)** |
| the network trained on UR Fall only, threshold 0.9 | 63 / 11 | 10 of 11 | 14 of 63 (22%) |
| the rules (fall_still_s 1.0) | 48 / 8 | 8 of 8 | 13 of 48 (27%) |

On UR Fall the same check lost 0 real falls; here it loses 16 to 27%. Eight of the nine silenced alerts of the network are one actor's recordings, clear falls ("walking, then falling forward", falling backwards onto a striped floor mat) that the 2B model took for lying down on purpose. With a network this precise (3 false alerts in 59) the check prevents 3 false messages and costs 9 real ones, so **as built it makes the system less safe on this data**. A threshold of 0.02 still silences 7 real alerts of 56. The safe use is to show the VLM's doubt next to the alert without withholding it.

On new people and rooms the network keeps most of its recall but loses most of its precision: the false-alarm rate goes from 2% to 14%, mostly lying down on a bed or sofa on purpose (the dataset's daily-activity clips are full of it). 29 of the 160 clips are 15 fps night recordings (the network learned 30 fps windows; I could not tell the effect of the frame rate from the effect of the poor light, see `ROADMAP_V2_NHAT_KY.md`). The two datasets are counted differently (UR Fall: an alert in the fall's time window; GMDCSA-24: an alert anywhere in the clip), so the columns are a trend, not a like-for-like comparison.

**Fights: the rule does not work.** On AIRTLab it reports **202 of 230 (88%)** violent clips and also **111 of 120 (92%)** clips of hugs, high fives, greetings and hand gestures made to look like fights. I then tried 30 pairs of thresholds: no pair separates them (the best one, chosen on half of the clips, reports 39% of violent and 18% of non-violent clips on the other half). "A hand moves fast inside another person's box" is true of a hug as much as of a punch. Fight detection needs a different signal or a learned model; the current rule is not a result.

Data: Alam et al., *GMDCSA24: A dataset for human fall detection in videos*, Data in Brief, 2024 (CC BY 4.0); Bianculli et al., *A dataset for automatic violence detection in videos*, Data in Brief 33, 2020, doi:10.1016/j.dib.2020.106587 ("freely released for research and educational purposes"). The clips are not in this repository.

One-hour soak, 4 cameras, everything switched on: 29.8 fps on the slowest camera, 0 restarts, 0 errors, GPU memory flat;
the engine's RAM grew 936 → 1089 MiB, slowing down but not flat, so **a slow leak is neither shown nor ruled out**
(`ROADMAP_V2_NHAT_KY.md`, section 11.8).

## Limitations (what this is not)

- **Small, borrowed, acted data.** The network was trained and the rules tuned on UR Fall (one room, one camera view, volunteers
  acting falls); the check on GMDCSA-24 (4 actors, 3 setups) is also acted. Nobody fell for real at home. Until it is tested on my
  own footage, M1 and M2 say little about a real home.
- **Fights do not work yet.** The fight rule passes its unit tests but, on acted fights (AIRTLab), it cannot tell a fight from a hug
  (88% of violent clips reported, 92% of non-violent ones too). There is no fight model. Treat fight detection as unfinished.
- **Pose misses small and hidden people.** The skeleton detector found far fewer people 100-200 px tall (recall 0.02, against
  0.27 for a plain person detector at the same threshold). A camera that sees people small, or half behind furniture, will miss
  falls.
- **A false alarm rate per hour is unknown**, see M2. The VLM removes some false alarms but also needs the frames after a fall,
  so it adds about 4.5 s before Telegram, and on GMDCSA-24 it would have silenced 16% of the real alerts of the network (9 of 56), so by default it only adds a note to the message.
- **Not tried on a real IP camera brand, or for days.** The longest run is one hour with fake cameras (see the soak result above).
- **No login.** It is built for one machine in a trusted LAN; do not put it on the internet as it is.

## Write-ups

Three short articles about things that went wrong and what I measured ([`docs/posts/`](../docs/posts/)):

1. [Why my detector found 10% fewer people than Ultralytics (and why it was not a bug)](../docs/posts/01-why-my-detector-found-10-percent-fewer-people.md)
2. [A fall rule that scored 0 of 5, then 5 of 5, and why the second number meant little](../docs/posts/02-a-fall-rule-that-scored-zero-then-five-out-of-five.md)
3. [A language model that said "yes" to everyone, and a GPU that ran out of memory](../docs/posts/03-a-language-model-that-said-yes-and-a-gpu-that-ran-out-of-memory.md)

## What it is made of

- **Engine** (C, NVIDIA DeepStream, TensorRT): N cameras (RTSP or files) in one GPU pipeline: YOLOv8n-pose → tracking →
  rules and a small skeleton network (ST-GCN, C++ TensorRT worker) → events; a ring of recent frames for the VLM.
- **VLM service** (Python, Qwen3-VL-2B, local): looks at a few frames around an alert and answers "fall, lying on purpose,
  or something else". If it is slow, down or unsure the alert goes out anyway (fail-open).
- **Backend** (FastAPI + PostgreSQL): cameras, events, live updates over WebSocket, Telegram, snapshot cleanup.
- **Web app** (React): dashboard, camera wall, live video with skeletons, event gallery, camera management and per-camera rules.
- **Runs on one machine in a LAN** with Docker Compose.

## How it works

```text
 cameras (RTSP / files)
        |
        v
 engine container ─────────────────────────────────────────────────────────────┐
 | ds_engine: N sources -> nvstreammux -> nvinfer (YOLOv8n-pose, TensorRT FP16)|
 |   -> nvtracker (NvDCF) -> pad probe -> per camera: fall / fight / zone rules |
 |        |                    + skeleton window -> ST-GCN worker (TensorRT)    |
 |        +-> frame ring (JPEG in tmpfs) -> events -> publisher (spool + HTTP)  |
 |   -> nvstreamdemux -> skeletons drawn -> H.264 encoder per camera -> RTMP    |
 | Engine manager (Python): starts ds_engine and keeps it in step with the list |
 └──────┬─────────────────────────────────────────────────┬────────────────────┘
   events, heartbeats (HTTP + token)                  RTMP video
        v                                                  v
 backend (FastAPI) ── PostgreSQL ── VLM service      MediaMTX ── WebRTC ──┐
   │  └─ Telegram  (event waits <= 8 s for the VLM)                       │
   └─ REST + WebSocket ── nginx ──> browser (React app) <────────────────┘
```

- **Fail-open everywhere.** No model file, a failed model load, a slow or dead VLM, a missing frame: the alert still goes out,
  marked "not checked". Nothing in the safety path is allowed to swallow a fall.
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

Needs Docker with the NVIDIA Container Toolkit and a GPU (8 GB is enough for 8 cameras plus the VLM, see "VLM and the GPU" below).
Once, on the host, export the models (the ONNX files are not in the repository):

```bash
pip install -r requirements.txt
python engine/tools/export_yolov8_pose_onnx.py   # yolov8n-pose.pt -> engine/models/yolov8n-pose.onnx (skeletons: falls, fights)
python eval/train/train.py --final && python eval/train/export_onnx.py --out engine/models/stgcn_fall.onnx   # optional fall network
cp .env.example .env
```

Edit `.env`: `POSTGRES_PASSWORD` (letters and digits), `INTERNAL_TOKEN` (`openssl rand -hex 24`), `PUBLIC_HOST` (the address
browsers use to reach this machine), and the Telegram settings if wanted. Then, from the repository root:

```bash
docker compose --env-file .env -f deploy/compose.yml up -d --build
```

Open `http://<PUBLIC_HOST>:8080`, go to **Cameras → Add camera**, press **Test connection**, **Save**, then **Start**.
No camera at hand? Put an MP4 in `data/videos/` and use `file:///videos/<name>.mp4`.

The first build pulls the DeepStream image (~32 GB) and builds the VLM image (~9 GB; torch). The first start of the `vlm` container
downloads the model (~4.3 GB) into the `hf-cache` volume (`VLM_HF_CACHE=<host dir>` reuses a model already downloaded, e.g.
`~/.cache/huggingface`); until it has loaded, alerts go out unchecked. The first start with a new number of camera places builds a TensorRT
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
| `mediamtx` | 8889/tcp, 8189/udp (`WEBRTC_PORT`, `WEBRTC_UDP_PORT`) | WebRTC to browsers (`PUBLIC_HOST` goes into its candidates) |
| `vlm`, `backend`, `db`, `engine`, `backup` | nothing | the engine manager (port 8100, no auth) is reachable only inside Docker |

**VLM and the GPU.** `engine` and `vlm` share the one GPU. Measured on an RTX 4060 Laptop (8 GB), 1080p H.264 cameras at 30 fps, pose + fall network
+ frame ring + video, VLM asked every 2 s (`ROADMAP_V2_NHAT_KY.md`, section 11): 8 cameras keep 30 fps (GPU ~100%, VRAM 7.2 GiB); **12 cameras do not fit** (VLM 5.1 GiB + engine ~2.2 GiB + the desktop leave under 120 MiB of 8.2 GiB; when VRAM runs out DeepStream fails with CUDA errors and the engine can crash): with the VLM on an 8 GB GPU use at most 8 cameras (`ENGINE_MAX_CAMERAS`, default 8). Without the VLM 12 cameras hold 30 fps.
The ring of recent frames (`/ring`) is a tmpfs volume shared by `engine` (writes) and `backend` (reads). The ST-GCN fall network is loaded
(`--fall-model`); a camera uses it when its form says so (**Safety rules → Detect falls → How a fall is decided**: rules, skeleton network, or
either; the default is the rules, and a change is applied within a second or two without a restart).

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
| `ENGINE_COMMAND`, `ENGINE_CONFIG`, `ENGINE_TRACKER_CONFIG`, `ENGINE_RUNTIME_DIR`, `ENGINE_EXTRA_ARGS` | see `.env.example` | how the manager starts the engine. **Falls and fights need the pose model**: export `engine/models/yolov8n-pose.onnx` (`engine/tools/export_yolov8_pose_onnx.py`) and set `ENGINE_CONFIG` to `engine/configs/pgie_yolov8n_pose.txt` (in Docker `/app/engine/configs/pgie_yolov8n_pose.txt`); the first start builds its TensorRT engine (~4 min) |
| `ENGINE_BACKEND_URL`, `ENGINE_HEARTBEAT_URL` | `http://127.0.0.1:8000/internal/events`, derived | where the engine posts events and heartbeats |
| `ENGINE_STREAM_URL`, `MEDIAMTX_WEBRTC_URL`, `MEDIAMTX_PATH_PREFIX` | empty = no video, –, `live` | publishing and watching live video |
| `VLM_URL`, `VERIFY_TIMEOUT`, `FRAME_RING_DIR`, `VLM_SUPPRESS` | unset, `8`, unset, `false` | the VLM check of falls and fights (see below): where the VLM service listens (unset = no check), the most seconds an alert waits for it, the engine's frame ring (`--frame-ring-dir`; unset = the VLM sees only the event's snapshot), and whether an alert the VLM doubts is withheld (`true`) or sent with a note (`false`) |
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

## Checking falls and fights with a vision-language model

A fall or fight can be looked at by a small VLM (Qwen3-VL-2B-Instruct, Apache 2.0, run locally, ~4.3 GB of GPU memory) before anybody is alerted. It sees six frames
from just before to two seconds after the event and chooses what the person does: falls / lies down on purpose / crouches or bends / normal / nobody. **If it is
sure the event is not a fall (probability of "fall" below 0.1), the Telegram message is still sent, with a line "The VLM doubts this alert: <what it saw>", and the event
is shown under the filter "Not confirmed by the VLM".** Withholding the message is opt-in (`VLM_SUPPRESS=true`), because on GMDCSA-24 the VLM silenced 16% of the real falls (see below). Anything else - the VLM unsure, down, slow (more than `VERIFY_TIMEOUT`), or no picture - sends the alert as if there were no VLM.

```bash
pip install -r vlm_service/requirements.txt                       # on the machine with the GPU
uvicorn vlm_service.app:create_app --factory --host 127.0.0.1 --port 8200   # first start downloads the model (~4 GB)
# .env of the backend:  VLM_URL=http://127.0.0.1:8200   VERIFY_TIMEOUT=8   FRAME_RING_DIR=/dev/shm/ring
# engine (ENGINE_EXTRA_ARGS): --frame-ring-dir /dev/shm/ring    (the same directory; a tmpfs: ~2 JPEGs/s per camera with a fall or fight rule are rewritten all the time)
```
The frame ring is what lets the VLM tell a fall from lying down on purpose; without it the VLM gets the single snapshot of the event. Measured on UR Fall
(one room, 40 daily-activity sequences, **thresholds read off the same data: optimistic; no false-alarms-per-hour figure yet**): see `eval/results/2026-11-20_v2-8_vlm_urfall_padded.txt` (current) and `ROADMAP_V2_NHAT_KY.md` section 10 (first round, before the picture-stretch fix).
**That result did not hold on other people:** on GMDCSA-24 the check silenced 16 to 27% of the real alerts (table in "Tested on recordings it was not built on", `eval/results/2026-11-22_v2-9_vlm_gmdcsa24.json`). That is why a doubted alert is sent anyway, with a note (`VLM_SUPPRESS=false`, the default).

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
| `--dump-detections FILE` | measuring tool: CSV of every detection (after `nvinfer`) and tracked object (after `nvtracker`) per frame, written by a separate thread; see `eval/` |
| `--config configs/pgie_yolov8n_pose.txt` | the pose model (YOLOv8n-pose, 640x384 input): every tracked person also carries 17 keypoints, kept per track in the engine (`src/core/pose.c`) and drawn on the video of `--output` / `--stream-url`; export it with `engine/tools/export_yolov8_pose_onnx.py` |
| `--fall-model FILE.onnx` (`--fall-model-window T`, `--fall-model-batch N`) | the skeleton network that scores falls (ST-GCN, ~0.1 M parameters; train/export: `eval/train/`). TensorRT builds an FP16 engine from it on first use and a worker thread (C++, TensorRT API) scores windows in batches, so the pad probe never waits. A camera opts in with `fall_net=1` (network decides) or `fall_net=2` (network or rules) in the cameras file, plus `fall_net_thr` / `fall_net_k`; without the model, or when it cannot be loaded, every camera runs the rules |
| `--stretch-sources` | stretch a picture that is not 16:9 to fill the 1920x1080 frame, as the engine used to; by default it keeps its shape and gets black bars (a stretched 4:3 camera makes people 1.33 times too wide and tilts every body angle). Only needed to reproduce the 2026-11-19 numbers |
| `--no-vram-check` | the engine refuses to start (exit code 4) when the free GPU memory is below an estimate for its camera places (fitted to the measurements: ~0.4 GiB for 1 place, ~1.5 GiB for 8, ~2.6 GiB for 12 with the video, +15 %), because DeepStream crashes instead of failing cleanly when the GPU runs out of memory. This turns the check off |
| `--frame-ring-dir DIR` (`--frame-ring-fps N`, `--frame-ring-seconds SEC`) | keep the last 15 s of every camera that has a fall or fight rule as JPEG files `ring_cam<ID>_<epoch ms>.jpg` (2 per second); the backend takes the pictures for the VLM check from there. Use a tmpfs |
| `--absence-timeout SEC`, `--rtsp-timeout SEC`, `--fps-interval SEC` | default re-alert delay (10), RTSP reconnect after SEC without frames (10), FPS print interval (5, 0 = off) |

Exit codes: `0` all sources ended or stopped by SIGINT/SIGTERM, `1` pipeline error, `2` bad arguments, `3` pipeline not built.

- **Cameras file** (`[camera.<id>]` with `uri=`; optional `conf_threshold=`, `absence_timeout=`, `ai_enabled=`, and the safety rules
  below). A bad value exits with code 2 without printing the address.
- **Safety rules (falls, fights, restricted areas).** Per camera in the cameras file: `behaviors=fall,fight,zones` and zones as
  `zone.1=restricted|5|0.45,0.0 1.0,0.0 1.0,1.0 0.45,1.0` (`kind|id|x,y x,y ...`, kind `restricted`, `lie_ok` (a bed or sofa: lying
  there is no fall) or `ignore` (a TV, a mirror), coordinates relative to the frame, at most 8 zones of 3-32 points). Thresholds
  have the names of `BehaviorConfig` in `src/events/behavior.py` (`fall_still_s`, `fight_speed`, `zone_dwell_s`, ...), which is also the
  specification of every rule; `engine/src/core/behavior.c` is its C port and both pass the same vectors (`tests/vectors/behavior_*.json`).
  Falls and fights need the pose model (`--config configs/pgie_yolov8n_pose.txt`); restricted areas also work with the detection model.
  They are all off unless `behaviors=` says so, and a SIGHUP applies a change without interrupting the camera. With `--output` or
  `--stream-url` the zones are drawn (red, blue, grey) and the people of an event get a label for 5 s.
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
- **Event schema** (one JSON per event; v1 for `PERSON_DETECTED`, v2 for the safety events):

```json
{"schema_version":1,"event_id":"<uuid>","camera_id":1,"pipeline_run_id":"<uuid>","type":"PERSON_DETECTED",
 "track_id":12,"confidence":0.9100,"timestamp":"2026-01-01T12:00:00.123Z","bbox":[0.1,0.2,0.3,0.4],"snapshot":null}
```

`timestamp` is UTC (stream start + PTS), `bbox` is x, y, w, h relative to the frame, `snapshot` is a file name like
`PERSON_DETECTED_cam1_id12_20260101T120000_123e4567.jpg` or `null`. A person is `(pipeline_run_id, camera_id, track_id)`:
track IDs restart with each engine run.

A safety event (`"type":"FALL"`, `"FIGHT"` or `"RESTRICTED_AREA"`, `"schema_version":2`) has the same fields plus three,
so a reader of v1 still understands it: `"track_ids":[12]` (a fight has two), `"occurred_at":"..."` (when the behaviour *began*; a fall
is reported `fall_still_s` after the impact and `occurred_at` is the impact) and `"zone_id":3` or `null`. `confidence` is the score of the
rule, the snapshot is named after the type (`FALL_cam1_id12_...jpg`) and `bbox` is the person (a fight: the box around both).

Model note: on a 1442-frame test video boxes match Ultralytics (IoU 0.95-0.99) but the engine finds ~10% fewer people at the same
0.25 threshold (boxes near the threshold). NvDCF gave 21 tracks vs 36 for ByteTrack; there is no ground truth yet to measure ID switches.

## Backend API

FastAPI + SQLAlchemy 2 + Alembic on PostgreSQL; interactive docs at `/docs`.

| Endpoint | |
|---|---|
| `GET /api/health` | 200, or 503 when the database is unreachable |
| `GET/POST /api/cameras`, `GET/PATCH/DELETE /api/cameras/{id}` | cameras (soft delete); `behaviors` (`fall`, `fight`, `zones`) and `zones` (`{kind, zone_id?, points}`, a missing `zone_id` gets the next free number) are the safety rules of the camera and reach the engine within a second or two |
| `POST /api/cameras/test-connection` | body `{source_type, source_uri}` or `{camera_id}`; checks connect, login (Basic/Digest), stream, codec (H.264/H.265), resolution. A broken camera is `ok:false` with a reason, not an HTTP error |
| `POST /api/cameras/{id}/start`, `/stop` | set the wanted state (applied in the background; a disabled camera answers 409) |
| `GET /api/cameras/{id}/status` | wanted state, engine process state, and what the engine reports: `state` (`RUNNING`/`RECONNECTING`/`ENDED`), `fps`, `people`, `last_frame_age` |
| `GET /api/cameras/{id}/stream` | `{whep_url, player_url, available}` for live video; 503 until `MEDIAMTX_WEBRTC_URL` is set |
| `GET /api/engine` | engine as a whole: manager reachability, `capacity`, `running`, `wanted`, free `room` |
| `GET/PATCH /api/settings` | what the Settings page changes: `alerts_enabled`, `quiet_hours_enabled`, `quiet_start`, `quiet_end` (`"HH:MM"`, server's local time), `snapshot_retention_days` (1-365), `default_conf_threshold`, `default_absence_timeout`; plus read-only `telegram_configured`, `snapshot_count`, `snapshot_bytes`, `version`. PATCH takes only the fields to change (unknown field, bad value, or quiet hours that start and end at the same minute: 422) |
| `POST /api/settings/test-notification` | sends a test message to Telegram (whatever the alert switch says); `{ok, message}`, a Telegram that is not set up or refuses is `ok: false`, not an HTTP error |
| `GET /api/events` | newest first; filters `camera_id`, `from`, `to`, `type` (one or several, comma separated: `FALL,FIGHT`; an unknown type is 422), `status`; `limit` (1-200), `offset`; answers `{items, total, limit, offset}`; each event has `track_ids`, `zone_id`, `occurred_at`, `severity` (`info` / `medium` / `high`) |
| `GET /api/events/{id}`, `.../snapshot` | one event; its JPEG |
| `PATCH /api/events/{id}` | `{"status": "new" \| "acknowledged"}` |
| `WS /api/ws` | server → client `{type, data}`: `event.created`, `event.updated`, `camera.status`. No replay (read current state over REST); a client 100 messages behind is closed (1013) |
| `POST /internal/events`, `/internal/heartbeat` | for the engine, with header `X-Internal-Token` when `INTERNAL_TOKEN` is set. Events are idempotent on `event_id`: 201 stored, 200 duplicate, 422 can never be stored (engine drops it) |

The browser talks to one origin, so no CORS setup is needed. Engine manager API (loopback, no auth): `PUT /cameras`, `GET /status`.

## Tests

```bash
pytest                                  # backend, manager, event logic, shared vectors (backend tests start a throwaway PostgreSQL; need its server programs installed)
cd engine && make test-core             # pure-C core under ASan/UBSan, no GPU
cd frontend && npm test                 # unit tests (vitest)
cd frontend && npm run e2e              # end-to-end + accessibility in Chrome (own backend, DB and fake engine on ports 18765/18766/5174)
cd frontend && npm run e2e:demo         # the same for the demo build (made-up cameras, no backend), incl. the scripted fall; Playwright's own Chromium, e.g. in mcr.microsoft.com/playwright:v1.64.1-noble
RUN_GPU_TESTS=1 pytest tests/engine_real -v   # the real engine on the GPU, ~5 min, GPU must be idle
```

`tests/vectors/event_manager_*.json` are generated from the Python `PersonDetectedEventManager` and replayed on both the Python and the
C implementation so the two cannot drift apart. No test touches your database, your `.env` Telegram settings or Telegram itself.
Tools in `engine/tools/`: `fake_receiver.py` (fake backend), `rtsp_test_server.c` (fake RTSP camera: `make rtsp-test-server`),
`benchmark.py`, `check_reload.sh`, `check_dynamic.sh`. `deploy/soak.py` runs the whole stack for hours and reports drift.

**Soak (long run).** `python3 deploy/soak.py video --hours 1.2` once, then `python3 deploy/soak.py run --hours 1 --cameras 4 --out logs/soak-1h`
(own compose project and ports, Telegram off; the machine must stay on and awake). One hour with 4 fake 1080p cameras, pose, the fall network,
the frame ring, video output and the VLM asked every 10 s: every camera held 29.8 fps, 0 restarts, 0 errors, VRAM and the frame ring stayed flat;
the engine's RAM grew from 936 to 1089 MiB, slowing down (+73, +31, +18, +14, +17 MiB per 10 min) but not flat. **One hour cannot tell whether
that is a leak**; a longer run has not been done (`ROADMAP_V2_NHAT_KY.md`, 11.8).

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
| `engine/` | `src/` (DeepStream engine in seven modules: `ds_engine.c` pipeline and `main`, `config.c`, `sources.c`, `probes.c`, `delivery.c`, `stream_out.c`, `report.c`; shared types in `app.h`), `src/core/` (pure C with tests), `configs/`, `parser/` (YOLOv8 box parser), `tools/` |
| `engine_manager/` | service that supervises `ds_engine` |
| `backend/` | FastAPI app, models, Alembic migrations, WebSocket hub, Telegram notifier, snapshot janitor |
| `frontend/` | React + TypeScript + Vite app (`src/{api,websocket,hooks,components,layouts,pages,utils}`), `e2e/` |
| `src/` | shared Python: config, logging, `PERSON_DETECTED` specification, Telegram notifier, snapshot cleanup |
| `deploy/` | Dockerfiles, `compose.yml`, nginx and MediaMTX config, backup and soak scripts |
| `tests/`, `scripts/` | pytest suites and shared vectors; manual helper scripts |

The DeepStream sample sources are under NVIDIA's proprietary license and are not copied into this repository.
