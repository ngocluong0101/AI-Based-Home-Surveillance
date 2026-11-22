# AI-Based Home Surveillance

**Fall detection on several cameras at once, on one GPU, running locally.**
A person alone at home falls and cannot get up. This system reads body posture from a skeleton (no faces, nobody is identified),
recognises the fall within a second or two, has a local vision-language model double-check it, and sends a Telegram alert.

**[▶ Live demo](https://ngocluong0101.github.io/AI-Based-Home-Surveillance/)** — the real web app in your browser with made-up
cameras, no install. Open **Monitoring → Living room**: about every 70 s a person walks in and falls, a FALL alert arrives.
*(The demo is drawn by code; the numbers below are not from it.)*

SCREENSHOTS: 
<p align="center">
  <img src="docs/images/dashboard.png" width="48%" alt="Dashboard">
  <img src="docs/images/wall.png" width="48%" alt="Wall">
  <img src="docs/images/monitoring.png" width="48%" alt="Monitoring">
  <img src="docs/images/events.png" width="48%" alt="Events">
  <img src="docs/images/cameras.png" width="48%" alt="Cameras">
</p>


## Built with

| | |
|---|---|
| **Video pipeline** | C, NVIDIA DeepStream 7.1 (`nvstreammux`, `nvinfer`, `nvtracker`), GStreamer, RTSP in / H.264 + WebRTC out |
| **Models** | YOLOv8n-pose (TensorRT FP16), a small ST-GCN skeleton network (C++ TensorRT), Qwen3-VL-2B as a local second opinion |
| **Backend** | Python, FastAPI, PostgreSQL, WebSocket, Telegram |
| **Web app** | React, TypeScript, Vite |
| **Ops** | Docker Compose (7 containers), fail-open design, spool-to-disk delivery, benchmarks and soak tests |
| **Evaluation** | `eval/`: metric definitions fixed before measuring, results as files, tests on datasets the system was not built on |

## How it works

```mermaid
flowchart LR
  CAM["📹 Cameras<br/>RTSP / files"]

  subgraph ENGINE["Engine · C · DeepStream · ONE GPU pipeline for N cameras"]
    direction LR
    MUX["nvstreammux<br/>batches N sources"] --> PGIE["nvinfer<br/>YOLOv8n-pose<br/>TensorRT FP16"]
    PGIE --> TRK["nvtracker<br/>NvDCF"]
    TRK --> PROBE["pad probe, per camera<br/>skeleton window → rules<br/>fall · fight · zone"]
    PROBE --> NET["ST-GCN fall network<br/>C++ TensorRT worker"]
    PROBE --> RING["frame ring<br/>JPEGs in tmpfs"]
    NET --> PUB["publisher<br/>disk spool + HTTP"]
    PROBE --> PUB
    TRK --> OSD["demux → OSD → NVENC<br/>skeletons drawn, H.264"]
  end

  MGR["Engine manager · Python<br/>starts ds_engine, applies camera list"]
  BE["Backend · FastAPI<br/>PostgreSQL"]
  VLM["VLM service<br/>Qwen3-VL-2B, local"]
  TG["📱 Telegram"]
  MTX["MediaMTX<br/>WebRTC"]
  WEB["🖥 React web app"]

  CAM --> MUX
  PUB -- "events + heartbeats" --> BE
  RING -. "frames around the alert" .-> VLM
  BE <-- "is it a fall?" --> VLM
  BE -- "alert, waits ≤ 8 s for the VLM" --> TG
  BE -- "REST + WebSocket" --> WEB
  BE -- "camera list" --> MGR
  MGR -. "SIGHUP / restart" .-> ENGINE
  OSD -- "RTMP" --> MTX
  MTX -- "live video" --> WEB

  classDef eng fill:#e8f1fb,stroke:#3b82c4,color:#111
  classDef svc fill:#eef7ee,stroke:#3f9142,color:#111
  classDef ext fill:#fdf3e3,stroke:#d08a1c,color:#111
  class MUX,PGIE,TRK,PROBE,NET,RING,PUB,OSD eng
  class BE,VLM,MGR,MTX svc
  class CAM,TG,WEB ext
```

- **One process, N cameras.** `nvstreammux` batches all cameras into one GPU pipeline: 30 fps on each of 12 cameras on a laptop RTX 4060
  (8 with the VLM running). Eight separate processes for eight cameras (detection only) needed about 4× the RAM and 3× the CPU.
- **Rules first, network second.** Hand-written fall rules are the baseline and the fallback; a small skeleton network beats them
  and runs beside them. Rules exist twice (Python = specification, C = engine) and run the same test vectors.
- **Fail-open everywhere.** No model file, a slow or dead VLM, a missing frame: the alert still goes out. The VLM only adds a note.
- **Nothing is lost.** If the backend is down, events go to a disk spool and are delivered later (idempotent by event id).

## Results

Every number is a file in [`eval/results/`](eval/results/); definitions in [`eval/DEFINITIONS.md`](eval/DEFINITIONS.md).

| | Result | Condition (read this before quoting it) |
|---|---|---|
| **Falls caught** | **28 of 30** (rules: 14 of 30) | UR Fall dataset, one room, cross-validated. On people and rooms it had never seen (GMDCSA-24): **68–75%** |
| **False alarms** | **1 of 40** daily-activity sequences | UR Fall. On unseen people: ~4% of clips (14% before I added that data to training). **Per camera-hour: not measured**, I have no hours of real household footage |
| **Throughput** | **30 fps on every camera at 1, 4, 8, 12 cameras** | 1080p H.264, fake RTSP cameras, RTX 4060 8 GB. With the VLM: 8 cameras |
| **Latency** | fall → alert **0.18 s** after the network decides (~4.5 s with the VLM check) | UR Fall clips replayed in real time through the real engine |

More: [all measurements with caveats](docs/REFERENCE.md#results), [three write-ups of what went wrong](docs/REFERENCE.md#write-ups).

## Limitations (what this is not)

- **Borrowed, acted data.** Trained and tuned on UR Fall and GMDCSA-24 (volunteers acting falls). **No footage of a real home yet**,
  so these numbers say little about one.
- **Fight detection does not work.** On acted fights (AIRTLab) the rule reports 88% of violent clips and 92% of hugs and high fives.
  Unit tests pass; the rule is not a result.
- **The VLM is not a gatekeeper.** On unseen people it would have silenced 16% of real falls, so it only adds a note to the alert.
- **Pose misses small or hidden people** (recall 0.02 for people 100–200 px tall).
- **Not tried** on a physical IP camera, for days (longest run: 1 h), or on Jetson. **No login**: for one machine in a trusted LAN.

## Quick start

Needs Docker with the NVIDIA Container Toolkit and an NVIDIA GPU (8 GB is enough for 8 cameras plus the VLM).

```bash
git clone <this repo> && cd AI-Based-Home-Surveillance
pip install -r requirements.txt
python engine/tools/export_yolov8_pose_onnx.py       # skeleton detector -> engine/models/ (ONNX files are not in the repo)
cp .env.example .env                                 # set POSTGRES_PASSWORD, INTERNAL_TOKEN, PUBLIC_HOST (Telegram optional)
docker compose --env-file .env -f deploy/compose.yml up -d --build
```

Open `http://localhost:8080` → **Cameras → Add camera** → **Test connection** → **Save** → **Start**.
No camera? Put an MP4 in `data/videos/` and use `file:///videos/<name>.mp4`.
The first start builds TensorRT engines (about 3 minutes) and the first VLM start downloads a 4.3 GB model.
The fall network is optional: without it every camera uses the rules ([how to train it](docs/REFERENCE.md#quick-start-docker)).

## Where to look

| | |
|---|---|
| [`docs/REFERENCE.md`](docs/REFERENCE.md) | full documentation: set-up, every setting, engine options, API, tests, troubleshooting |
| [`engine/`](engine/) | the C pipeline (`src/`), pure-C core with tests (`src/core/`), YOLOv8-pose parser, TensorRT ST-GCN |
| [`eval/`](eval/) | how every number was measured, and how to measure it again |
| [`backend/`](backend/), [`frontend/`](frontend/), [`vlm_service/`](vlm_service/), [`deploy/`](deploy/) | the rest of the system |
| [`docs/posts/`](docs/posts/) | three short articles: a detector that found 10% fewer people, a fall rule that scored 0 then 5 of 5, a VLM that said yes to everyone |
