# ROADMAP — AI-Based-Home-Surveillance

> Roadmap tối ưu hóa cho project cá nhân: chuyển hệ thống CLI YOLOv8 + ByteTrack hiện tại thành nền tảng giám sát camera AI chạy trên trình duyệt.
>
> **Nguyên tắc:** AI core trước → backend sau → frontend sau → production/advanced cuối. Không rewrite toàn bộ project. Không thêm công nghệ chỉ vì "có thể dùng".
>
> **Mục tiêu MVP:** một engine C (DeepStream) xử lý **nhiều video cùng lúc trong 1 pipeline batch** → detect + track → event dedupe theo từng camera → snapshot → FastAPI lưu event → WebSocket realtime → Telegram (do Backend gửi) → browser xem video có bounding box.
>
> **Ngoài phạm vi MVP:** auth, Kubernetes, microservices, face recognition, re-ID, mobile app, cloud, training model.

---

# 1. Mục tiêu cuối cùng

```text
Camera / MP4 / Webcam
        │
        ▼
┌──────────────────────────────┐
│ DeepStream Engine            │
│                              │
│ source                       │
│   ↓                          │
│ nvstreammux                  │
│   ↓                          │
│ nvinfer (YOLOv8n TensorRT)   │
│   ↓                          │
│ nvtracker                    │
│   ↓                          │
│ nvdsosd                      │
│   ↓                          │
│ H264 encoder                 │
└───────┬──────────────────────┘
        │
        ├── Detection metadata
        │       ↓
        │   Pad Probe
        │       ↓
        │   EventManager
        │       ↓
        │   Snapshot
        │       ↓
        │   EventPublisher
        │
        ▼
     MediaMTX
        │
        ▼
     WebRTC
        │
        ▼
     React Frontend


EventPublisher
      │
      ▼
 FastAPI Backend
      │
      ├── PostgreSQL
      ├── WebSocket ──────► React
      └── Telegram
```

## Mục tiêu kỹ thuật

1. Thay inference/tracking của đường CLI bằng DeepStream. **Engine viết bằng C** (GStreamer + DeepStream C API, build bằng Makefile như các sample `deepstream-test*` của NVIDIA); không dùng Python/`pyds` cho pipeline và pad probe. **Một process engine xử lý N nguồn video trong 1 pipeline** (`nvstreammux` gom N khung hình thành 1 batch → 1 lần `nvinfer`). Kỳ vọng nhanh hơn chạy N process riêng nhờ batching, nhưng **phải đo** (mục 10), không mặc định tin. Điểm xuất phát: các sample C có sẵn trong image DeepStream (`deepstream-test1`, `deepstream-test3` nhiều nguồn, `deepstream-image-meta-test`). Repo **chưa có** model/engine/parser nào — ONNX, TensorRT engine và parser đều tự làm ở Phase 1.
2. Hiểu và sử dụng được:
   - `GstElement`
   - `GstPad`
   - `GstBuffer`
   - `GstCaps`
   - `NvDsBatchMeta`
   - `NvDsFrameMeta`
   - `NvDsObjectMeta`
   - pad probe
   - `nvinfer`
   - `nvtracker`
   - `nvdsosd`
3. Chuyển detection/track thành event nghiệp vụ.
4. Backend quản lý camera + event.
5. Frontend giao tiếp với backend qua REST/WebSocket và xem video qua WebRTC.
6. Đo được FPS, latency, GPU/VRAM và số camera chạy ổn trên RTX 4060 8GB.
7. Có một project đủ hoàn chỉnh để đưa vào CV/interview.

---

# 2. Nguyên tắc kiến trúc

## 2.1 Phần Python giữ lại

Đường CLI Python cũ (`main.py`, `src/detection`, `src/tracking`, `src/video`) đã bị xóa (commit `0a2f3ab`). Giữ lại:

```text
src/events/                 # Python — đặc tả tham chiếu cho EventManager bản C
src/notifications/          # Python — Backend dùng để gửi Telegram
src/snapshot_management.py  # Python — Backend dùng để dọn snapshot cũ
src/config/
src/logging_config.py
tests/                      # test của các phần trên (34 test) phải luôn xanh
```

Không có bước so sánh hiệu năng/độ chính xác với đường CLI cũ (đã quyết định không cần).

## 2.2 Hệ quả của việc engine viết bằng C

Engine C **không import được** code Python. Quy ước:

| Thành phần | Nơi chạy | Ngôn ngữ |
|---|---|---|
| Pipeline N nguồn (`nvstreammux` → `nvinfer` → `nvtracker`) + pad probe + tracker metadata | Engine | C |
| `EventManager` (dedupe), **1 instance cho mỗi source** | Engine | **C** (port từ `src/events/person_detected.py`) |
| Snapshot (`nvds_obj_enc_*`) | Engine | C |
| `EventPublisher` / heartbeat (HTTP) | Engine | C + libcurl, chạy ở worker thread |
| Engine Manager (start/stop/restart process engine) | Engine container | Python (chỉ giám sát process, **không chạm DeepStream**) |
| Telegram, dọn snapshot, DB, API | Backend | Python (tái dùng `src/notifications`, `src/snapshot_management.py`) |
| Export YOLOv8 → ONNX (Ultralytics), script benchmark, receiver giả để test | Máy dev / CI | Python — **công cụ offline, không nằm trong đường xử lý frame** |

**Quy tắc ranh giới:** mọi thứ chạm tới frame/metadata (decode → infer → track → probe → event → snapshot → publish) là **C**. Python chỉ xuất hiện ở 3 chỗ: Engine Manager (giám sát process), Backend, và công cụ offline/test. Không có chỗ nào Python nằm giữa đường xử lý video.

Để hai bản `EventManager` (Python và C) không lệch nhau:

- `src/events/person_detected.py` là **đặc tả chuẩn**.
- Tạo **một bộ test vector dùng chung** (file JSON: chuỗi `(now, [(track_id, conf)])` → danh sách event mong đợi) trong `tests/vectors/`.
- `pytest` chạy bản Python với vector đó; `make test-core` chạy bản C với đúng vector đó. Sai lệch ở bất kỳ bên nào là lỗi.

Phần C thuần (không phụ thuộc DeepStream) — `EventManager`, dựng JSON event, che URL/secret — được tách riêng để **build và test trên host bằng `gcc`**, không cần container GPU.

---

# 3. Kiến trúc repository

```text
AI-Based-Home-Surveillance/
│
├── src/
│   ├── events/                # GIỮ LÂU DÀI: đặc tả chuẩn của EventManager (engine C port từ đây)
│   ├── notifications/         # GIỮ LÂU DÀI: Backend dùng để gửi Telegram
│   ├── snapshot_management.py # GIỮ LÂU DÀI: Backend dùng để dọn snapshot
│
├── engine/                    # DeepStream — viết bằng C
│   ├── Makefile               # build engine + `make test-core` (theo mẫu deepstream-test1)
│   ├── include/
│   ├── src/
│   │   ├── core/              # C thuần, KHÔNG phụ thuộc DeepStream → test được trên host
│   │   │   ├── event_manager.c    # port từ src/events/person_detected.py
│   │   │   ├── event_json.c       # dựng JSON event schema v1
│   │   │   └── mask_url.c         # che credential trong log
│   │   ├── ds_engine.c        # phụ thuộc GStreamer + DeepStream. Khởi đầu là MỘT file theo deepstream_test3_app.c;
│   │   │                      # đã tách thành 7 module ngày 08/11/2026 khi file dài 2528 dòng. File này giữ:
│   │   │                      # build_pipeline (N nguồn → nvstreammux → ...), bus, tín hiệu, main.
│   │   ├── app.h              # SourceCtx / AppCtx, kiểu dùng chung
│   │   ├── config.c/.h        # CLI (--camera, --cameras-file, ...), kiểm tra tham số, cài đặt theo camera
│   │   ├── sources.c/.h       # ô camera (source_id ↔ camera_id), thêm/gỡ lúc chạy (SIGHUP), phát hiện mất RTSP
│   │   ├── probes.c/.h        # pad probe (NvDsBatchMeta → Detection), snapshot (nvds_obj_enc_*), đẩy hàng đợi
│   │   ├── delivery.c/.h      # thread publisher (libcurl + spool + ghi file snapshot)
│   │   ├── stream_out.c/.h    # nhánh mã hóa + publisher RTMP lên MediaMTX
│   │   └── report.c/.h        # thống kê, FPS, heartbeat
│   ├── configs/               # nvinfer, tracker, labels
│   ├── parser/                # custom bbox parser (C/C++, chỉ khi parser có sẵn không phù hợp)
│   ├── manager/               # Python: giám sát/restart process engine, control API
│   ├── tools/                 # Python offline: export_yolov8_onnx.py, receiver giả (không chạy trong đường xử lý frame)
│   └── tests/                 # test C (core) + test chạy binary trong container
│
├── backend/                   # FastAPI
│   ├── api/
│   ├── services/
│   ├── db/
│   └── schemas/
│
├── frontend/                  # React + TypeScript
│
├── deploy/
│   ├── Dockerfile.engine
│   ├── Dockerfile.backend
│   ├── mediamtx.yml
│   └── compose.yml
│
├── tests/
├── data/
├── models/
├── requirements.txt
└── ROADMAP.md
```

---

# 4. Quyết định kỹ thuật chính

| Thành phần | Quyết định |
|---|---|
| AI engine | DeepStream 7.1 |
| Engine language | **C** (GStreamer + DeepStream C API), **không dùng `pyds`** |
| Build engine | **Makefile** (theo mẫu `deepstream-test1`; cần `CUDA_VER=12.6`), build trong DeepStream container |
| Engine HTTP/JSON | libcurl + json-glib (hoặc jansson) — **[CẦN XÁC MINH]** header có trong image |
| Engine Manager | Python, chỉ giám sát process engine; không import/gọi DeepStream |
| Custom parser | C/C++ (`nvdsinfer_custom_impl.h`), chỉ khi parser có sẵn không phù hợp |
| Nguồn video | `nvurisrcbin` (file/RTSP, có reconnect) → pad `sink_%u` của `nvstreammux`; mẫu `deepstream-test3` — đã xác minh `nvurisrcbin` có trong image 7.1 (2026-10-31); reconnect RTSP thực tế còn phải kiểm chứng (2.7) |
| Output nhiều camera | `nvstreamdemux` → nhánh riêng mỗi camera (osd → encoder → MediaMTX path `cam<ID>`); `nvmultistreamtiler` (1 video lưới) chỉ dùng để debug |
| Model | YOLOv8n |
| Model format | ONNX → TensorRT FP16 |
| Tracker | NvDCF (chủ repo chọn 2026-11-01; không so sánh NvSORT) |
| Kiến trúc process | **1 process = 1 pipeline = N camera** (`nvstreammux` batch). Cô lập lỗi bằng reconnect từng nguồn + Engine Manager restart khi process chết |
| Event logic | Engine (bản C của `EventManager`, 1 instance/source, đặc tả = `src/events`, test vector dùng chung) |
| Database | **PostgreSQL** + SQLAlchemy 2 (psycopg 3) + Alembic (chủ repo chọn 2026-11-02, thay cho SQLite ở bản kế hoạch đầu) |
| Backend | FastAPI |
| Frontend | React + TypeScript + Vite |
| Realtime | WebSocket |
| Video gateway | MediaMTX |
| Browser video | WebRTC/WHEP |
| Overlay | `nvdsosd`, burned-in |
| Notification | Telegram từ Backend |
| Container | DeepStream container cho Engine |
| Scaling | chỉ tối ưu sau khi đo thực tế |

---

# 5. Quy tắc quan trọng

### Rule 1 — Không gửi metadata từng frame qua network

```text
DeepStream
   ↓
probe
   ↓
EventManager
   ↓
chỉ gửi EVENT
```

Không:

```text
frame 1 → backend
frame 2 → backend
frame 3 → backend
...
```

Heartbeat chỉ gửi thông tin tổng hợp như:

```text
fps
camera state
last frame
people count
error
```

---

### Rule 2 — Pad probe không được block

Không được:

```text
probe
 ↓
Telegram API
 ↓
chờ 5 giây
 ↓
return
```

Phải:

```text
probe
 ↓
EventManager
 ↓
queue
 ↓
worker
 ↓
HTTP
```

Trong engine C: `queue` = `GAsyncQueue` (hoặc hàng đợi có giới hạn tự viết), `worker` = `GThread` gọi libcurl. Hàng đợi đầy thì **bỏ + log**, không chờ. Telegram không nằm trong engine — Backend lo.

---

### Rule 3 — Event ID phải idempotent

Mỗi event có:

```text
event_id
camera_id
pipeline_run_id
track_id
timestamp
```

Backend nhận event trùng `event_id` thì không tạo bản ghi thứ hai.

---

### Rule 4 — Thời gian event

Lưu timestamp dạng UTC.

Logic liên quan đến video phải dựa trên:

```text
stream_start_time[source_id] + PTS     (mỗi source có mốc stream_start riêng)
```

không dùng đơn thuần:

```text
datetime.now() / g_get_real_time()
```

vì MP4 có thể chạy nhanh hơn real-time.

---

# 6. ROADMAP

---

# Phase 1 — DeepStream AI Core

**Mục tiêu:** thay YOLO + ByteTrack của CLI bằng DeepStream.

**Môi trường (chủ repo xác nhận chạy được):** RTX 4060 Laptop 8 GB, container `nvcr.io/nvidia/deepstream:7.1-triton-multiarch` chạy với `--gpus all`; trong image có gcc 11.4, make, GStreamer 1.20.3, CUDA 12.6, các plugin `nvinfer`/`nvtracker`/`nvstreammux`/`nvurisrcbin`/`nvdsosd`/`nvstreamdemux`/`nvmultistreamtiler`, NVDEC/NVENC, header `nvdsmeta.h`/`gstnvdsmeta.h`/`nvds_obj_encode.h`/`nvdsinfer_custom_impl.h`, `pkg-config libcurl json-glib-1.0` (header `curl.h` ở `/usr/include/x86_64-linux-gnu/curl/`). Tracker có sẵn: IOU, NvDCF, NvDeepSORT, NvSORT (không có ByteTrack). Sample C (`deepstream-test1..5`, `deepstream-image-meta-test`) nằm trong `/opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/` để tham khảo. Sample là `LicenseRef-NvidiaProprietary`: chỉ đọc để học, **không chép nguyên văn vào repo**; `ds_engine.c` tự viết theo cấu trúc mẫu.

## 1.0 Khung project C

- [x] `engine/Makefile` theo mẫu `deepstream-test1` (đã có 2026-10-31, tự viết, không chép Makefile của NVIDIA): `CUDA_VER ?= 12.6`, `pkg-config gstreamer-1.0 libcurl json-glib-1.0`, link `-lnvdsgst_meta -lnvds_meta`, rpath tới `/opt/nvidia/deepstream/deepstream/lib`.
- [x] Target: `make` (build engine), `make test-core` (build + chạy test C thuần bằng `gcc` trên host, không cần DeepStream), `make clean`.
- [x] Bật `-Wall -Wextra`; chạy `make test-core` với `-fsanitize=address,undefined` để bắt lỗi bộ nhớ sớm.
- [x] Quy tắc C cần giữ ngay từ đầu (nguồn bug phổ biến của GStreamer/DeepStream; đã rà lại trong `ds_engine.c` (lúc đó còn là một file) ở cuối Phase 1: mọi `gst_element_factory_make` đều qua `make_element` kiểm tra NULL (nay ở `app.h`), `GstCaps`/`GstPad`/`GstBus` unref cân bằng, request pad giữ 1 ref và unref trong `free_sources` (nay ở `sources.c`), probe chỉ copy sang `Detection`):
  - mọi `gst_element_factory_make` kiểm tra NULL; mọi `gst_object_unref`/`g_free`/`gst_caps_unref` cân bằng;
  - **không giữ con trỏ `NvDsObjectMeta`/`NvDsFrameMeta` ra ngoài callback probe** — copy giá trị sang struct riêng ngay trong probe;
  - probe luôn `return GST_PAD_PROBE_OK` nhanh, không I/O mạng/đĩa nặng;
  - trạng thái theo từng nguồn (EventManager, đếm FPS, state `RECONNECTING`…) nằm trong `SourceCtx[N]` cấp phát theo số `--camera`, không dùng biến global đơn lẻ.

**Xong khi (đã đạt 2026-10-31: khung `engine/` build sạch trong container, `make test-core` in "0 tests, 0 failed" cả trên host lẫn trong container; `ds_engine` mới parse `--camera` và in bảng nguồn, chưa dựng pipeline):** `make` ra binary, `make test-core` chạy được (kể cả khi chưa có test nào, in "0 tests").

## 1.1 Pipeline cơ bản (N nguồn)

```text
source_0 ─┐
source_1 ─┼─► nvstreammux (batch-size = N) ─► nvinfer ─► fakesink
source_N ─┘
```

Làm với N = 1 trước, rồi 2, rồi 4 — **cùng một đường code**, không có bản "1 camera" riêng. Mỗi source nối vào một pad `sink_%u` xin từ `nvstreammux` (cách làm tương tự sample `deepstream-test3` trong image).

- [x] N file MP4 chạy đến EOS (pipeline chỉ thoát khi **tất cả** nguồn EOS). Đã chạy N = 1, 2, 4 file khác nhau, exit 0 (2026-11-01). `source_id` đã kiểm chứng: probe đếm frame/object theo `frame_meta->source_id` và mỗi nguồn ra số riêng, đổi thứ tự `--camera` thì số đi theo nguồn.
- [x] Xử lý bus ERROR/WARNING/EOS (`gst_bus_add_watch` + `GMainLoop`).
- [x] Exit code đúng: 0 khi EOS, 1 khi ERROR/không chuyển được sang PLAYING, 2 sai tham số, 3 không dựng được pipeline.
- [x] Cờ CLI: `--camera ID=URI` lặp lại cho mỗi nguồn và `--config` (bắt buộc), đọc bằng `GOptionContext`. `source_id` = thứ tự khai báo; engine giữ bảng `source_id ↔ camera_id` (`SourceCtx[N]` trong `app.h`, điền ở `sources.c`); `batch-size` của `nvstreammux`/`nvinfer` = số nguồn. `nvinfer` dùng `engine/configs/pgie_sample.txt` (model mẫu của image) cho tới khi có YOLO ở 1.2.
- [x] RTSP có mật khẩu: `--cameras-file FILE` (key-file, mỗi camera một group `[camera.<ID>]` với `uri=...`), từ chối file có quyền group/others, thông báo lỗi không in URI. Không để mật khẩu trong argv.
- [x] Kiểm tra rò bộ nhớ (2026-11-01): image không có `valgrind`, và AddressSanitizer không dùng được với CUDA (`cudaGetDeviceCount` lỗi `cudaErrorMemoryAllocation`), nên dùng: `compute-sanitizer --tool memcheck --leak-check full` trên clip 163 frame → 0 lỗi, 0 byte rò (bộ nhớ GPU); RSS của tiến trình đỉnh ~700 MB ở cả 1605 và 2884 frame (không tăng theo số frame). Phần C thuần chạy dưới ASan/UBSan trong `make test-core`. Rò chậm theo giờ để soak test ở Phase 8.

## 1.2 YOLOv8n

- [x] Export `yolov8n.pt` → ONNX bằng `engine/tools/export_yolov8_onnx.py` (Ultralytics, công cụ offline, `dynamic=True`, opset 17) → `engine/models/yolov8n.onnx` (input `images [batch,3,h,w]`, output `output0 [batch,84,anchors]`; `.onnx` và `.engine` nằm trong `.gitignore`).
- [x] `nvinfer` load YOLOv8n: `engine/configs/pgie_yolov8n.txt`.
- [x] TensorRT FP16 (`network-mode=2`), `batch-size` = số nguồn. **Đã xác minh (2026-11-01):** ONNX dynamic batch chạy được với `nvinfer` khi thêm `infer-dims=3;640;640` (export `dynamic=True` làm cả h/w động). `nvinfer` build engine mất ~150 s lần đầu rồi lưu `yolov8n.onnx_b<N>_gpu0_fp16.engine`; nó chỉ dùng lại engine mà `model-engine-file` trỏ tới (config trỏ tới bản b2: chạy lại mất 3 s). Engine batch 2 chạy được cho N = 1; N = 3 tự build engine b3.
- [x] Chỉ lấy class `person`: làm trong parser (chỉ báo `person`, và chỉ khi `person` là lớp có điểm cao nhất của anchor, giống Ultralytics), `num-detected-classes=1`.
- [x] Preprocessing: RGB, `net-scale-factor=1/255`, letterbox (`maintain-aspect-ratio=1`, `symmetric-padding=1`) như Ultralytics.
- [x] So sánh bbox với Ultralytics (cùng `conf=0.25`, `iou=0.45`, `imgsz=640`, video `sample_720p.mp4` 1442 frame): box rõ ràng khớp IoU 0.95–0.99, confidence lệch ≤ 0.02. Tổng số người: DeepStream 5322 so với Ultralytics 5912 (thấp hơn ~10%); chênh lệch nằm ở các box độ tin cậy thấp sát ngưỡng (DeepStream có box ~0.26 mà Ultralytics không có, và có box ~0.46 chỉ Ultralytics có). Video `sample_office.mp4`: cả hai ra 0 người. Chưa tìm nguyên nhân chênh (FP16 so với FP32, nội suy ảnh khi letterbox, decoder NVDEC so với OpenCV); chỉ cần xem lại nếu ảnh hưởng độ chính xác event ở Phase 2.
- [x] Custom bbox parser: tự viết `engine/parser/nvdsinfer_yolov8_parser.cpp` (hàm `NvDsInferParseYoloV8`), build bằng `make -C engine/parser CUDA_VER=12.6` → `engine/build/libnvds_yolov8_parser.so`. Không dùng DeepStream-Yolo nên không cần clone và không phụ thuộc việc dự án đó có hỗ trợ DS 7.1 hay không. NMS do `nvinfer` làm (`cluster-mode=2`, `nms-iou-threshold=0.45`).

## 1.3 Tracker

**Quyết định (chủ repo, 2026-11-01): dùng NvDCF**, không so sánh với NvSORT. Làm xong:

- [x] `nvtracker` đặt sau `nvinfer` trong `ds_engine.c`: `ll-lib-file=libnvds_nvmultiobjecttracker.so`, `tracker-width/height` = 960x544 (bội số của 32), `ll-config-file` = `--tracker-config` (bắt buộc), mẫu `engine/configs/tracker_nvdcf.yml` (tự viết, giá trị khởi đầu chưa tinh chỉnh; file mẫu của NVIDIA là `LicenseRef-NvidiaProprietary` nên không chép).
- [x] Probe chuyển sang src pad của `nvtracker`, đọc `object_id` (đếm `UNTRACKED_OBJECT_ID`), thống kê theo từng `source_id`: số track và số track ngắn (< 10 frame).
- [x] Đo (2026-11-01, `sample_720p.mp4` 1442 frame, N = 2, YOLOv8n FP16 + NvDCF): 4793 object, 21 track, 3 track ngắn, 0 object chưa có ID; chạy lặp 3 lần ra đúng cùng số; hai nguồn cùng video ra cùng kết quả (ID độc lập theo nguồn). Tốc độ ~430-730 frame/s tổng (tính cả khởi động ~1-2 s, decode bằng NVDEC, `sync=false`); VRAM tăng ~400 MB so với lúc rảnh (đỉnh ~1 GB gồm cả ~570 MB của tiến trình khác trên máy); GPU util đỉnh 77-100%.
- [x] Đối chứng thô với Ultralytics + ByteTrack (cách của CLI cũ) trên cùng video: 36 track, 9 track ngắn, 5535 object. NvDCF ít track và ít track ngắn hơn (ID ổn định hơn, ít cắt một người thành nhiều ID), nhưng cũng ít object hơn do tracker không xuất box đang trong `probationAge`/shadow và bỏ box yếu. **Chưa có ground truth** nên chưa biết số track ít hơn có phải do gộp nhầm hai người thành một ID hay không, và chưa đo được ID switch thật; đánh giá lại trên video thật ở Phase 2 (mục 2.4: "ID switch → đo event thừa").
- [ ] Còn lại: tinh chỉnh `tracker_nvdcf.yml` nếu thấy mất track hoặc ID nhảy trên video thật (RTSP camera).

## 1.4 Metadata

Đã implement (C API) trong `probes.c` (lúc làm Phase 1 còn nằm trong `ds_engine.c`), hàm `extract_person_detections`:

```text
gst_buffer_get_nvds_batch_meta()  →  NvDsBatchMeta
      ↓  (duyệt batch_meta->frame_meta_list)
NvDsFrameMeta
      ↓  (duyệt frame_meta->obj_meta_list)
NvDsObjectMeta
```

Lấy:

```text
obj_meta->object_id        (bỏ qua nếu == UNTRACKED_OBJECT_ID)
obj_meta->class_id         (chỉ giữ person = 0)
obj_meta->confidence
obj_meta->rect_params      (left, top, width, height — theo khung của nvstreammux, 1920x1080)
frame_meta->source_id
frame_meta->buf_pts
```

- [x] Đọc đủ các trường trên (2026-11-01). `UNTRACKED_OBJECT_ID` đã đếm riêng (`untracked`), trên video thử luôn bằng 0.

## 1.5 Pad probe

Probe gắn bằng `gst_pad_add_probe(pad, GST_PAD_PROBE_TYPE_BUFFER, ...)` trên **src pad của `nvtracker`**, sau khi đã có `object_id`, trước tiler/demux.

```text
DeepStream metadata
        ↓
pad probe (C)
        ↓
Detection[]  (mảng trên stack, tối đa 256/frame)
```

Struct C (copy giá trị, không giữ con trỏ meta) nằm ở `engine/src/core/detection.h` (C thuần, không phụ thuộc DeepStream/GLib):

```text
typedef struct {
    uint32_t source_id;
    uint64_t track_id;
    float    confidence;
    float    bbox[4];     // chuẩn hóa 0..1: x, y, w, h
    uint64_t pts;         // ns
} Detection;
```

- [x] `extract_person_detections(...)` (đọc `NvDs*Meta`, nằm trong `probes.c`) và `detection_bbox_normalize(...)` (cắt box vào khung rồi chuẩn hóa, C thuần ở `src/core/detection.c`). `make test-core` chạy 3 test cho `detection_bbox_normalize` (trong khung, bị cắt, box không dùng được) dưới ASan/UBSan.
- [x] Probe nhận **cả batch**: mỗi `NvDsFrameMeta` là một source, gom `Detection[]` cho `SourceCtx[source_id]`; `object_id` chỉ duy nhất trong từng source nên bảng thống kê track nằm trong từng `SourceCtx`. `camera_id` tra từ `source_id` khi in/dựng event.
- [x] Đưa `Detection[]` vào `EventManager[source_id]` (làm ở 2.1, 2026-11-01).

## 1.6 OSD + output

Probe luôn gắn **trước** tiler/demux, khi batch còn nguyên.

Giai đoạn debug (Phase 1) — 1 video lưới N ô, ghi ra file MP4 (`--output FILE`):

```text
nvtracker ─► nvmultistreamtiler ─► nvvideoconvert ─► nvdsosd ─► nvvideoconvert ─► I420 ─► nvv4l2h264enc ─► h264parse ─► qtmux ─► filesink
```

Không có `--output` thì cuối pipeline là `fakesink` như trước.

Giai đoạn streaming (Phase 4) — mỗi camera một nhánh, một stream riêng:

```text
nvtracker ─► nvstreamdemux ─┬─ src_0 ─► nvvideoconvert ─► nvdsosd ─► encoder ─► MediaMTX /cam<ID0>
                            ├─ src_1 ─► ...                                  ─► MediaMTX /cam<ID1>
                            └─ src_N ─► ...
```

- [x] Mở video và xác nhận (2026-11-01, frame 60 của `--output` với `sample_720p.mp4` + `sample_office.mp4`): hai ô xếp dọc, ô đầu có hộp đỏ quanh từng người, nhãn `person 0`, `person 1`, ... (nhãn lớp + track ID do `nvinfer`/`nvtracker` đặt, `nvdsosd` vẽ); ô thứ hai (video không có người) không có hộp.
- [x] FPS: `--fps-interval SEC` (mặc định 5, 0 = tắt) in fps từng nguồn định kỳ; cuối lượt chạy in `fps=` từng nguồn và tổng (tính cả khởi động).
- Lưu ý video lưới: khi các nguồn dài bằng nhau, video ra đủ số frame (1442/1442). Khi một nguồn hết sớm trong lúc nguồn khác còn chạy (batch-size lớn hơn số nguồn còn sống), `nvstreammux` ghép 2 frame cùng nguồn vào một batch và ô lưới của nguồn còn lại chỉ vẽ 1 frame mỗi batch (thử: 803/1442 frame hình, thời gian vẫn đúng). Chỉ ảnh hưởng video debug; mọi frame vẫn qua `nvinfer`/`nvtracker`/probe (đếm đủ 1442).

### Definition of Done

```bash
cd engine && make CUDA_VER=12.6 && make -C parser CUDA_VER=12.6
./build/ds_engine --config configs/pgie_yolov8n.txt --tracker-config configs/tracker_nvdcf.yml \
                  --output build/out.mp4 \
                  --camera 1=file://$PWD/../data/videos/sample_1080p_h264.mp4 \
                  --camera 2=file://$PWD/../data/videos/sample_1080p_h264_copy_1.mp4
```

(trong DeepStream container) chạy được và (**đạt hết, 2026-11-01**):

- [x] detect person (YOLOv8n FP16, N = 1, 2, 3);
- [x] track person (NvDCF);
- [x] probe đọc được metadata (`Detection[]`);
- [x] output có bounding box + track ID (`--output`);
- [x] có FPS (`--fps-interval` và dòng tổng kết);
- [x] 2+ nguồn trong 1 pipeline, track tách đúng theo camera (hai nguồn cùng video ra cùng 21 track; "event" tách theo camera làm ở Phase 2);
- [x] không crash (thoát mã 0; `compute-sanitizer` 0 lỗi). Chạy đúng lệnh trên với 2 video 1080p trong `data/videos/`: 1443 frame mỗi nguồn, 29 track (4 ngắn) mỗi nguồn, ~290 frame/s mỗi nguồn, không warning khi build.

---

# Phase 2 — Event System + Snapshot

**Mục tiêu:** biến detection thành nghiệp vụ.

## 2.1 Port EventManager sang C + test vector dùng chung

Engine là C nên **không dùng lại được** `PersonDetectedEventManager` (Python) trong process. Thay vì tách `EventDispatcher` trong Python (không còn là điều kiện tiên quyết cho DeepStream), làm:

- [x] Viết bộ **test vector chung** `tests/vectors/event_manager_*.json`: mỗi vector là chuỗi bước `{now_ns, detections:[{track_id, conf}]}` và danh sách event mong đợi tại từng bước. Sinh/kiểm tra bằng `PersonDetectedEventManager` hiện tại (nó là đặc tả).
- [x] `pytest` chạy bản Python với các vector đó (thêm test, **không sửa test cũ**).
- [x] `engine/src/core/event_manager.c`: port logic `last_seen` theo `track_id`, `absence_timeout`, dọn track cũ. Nhận `now` truyền vào (không tự gọi đồng hồ) — giống tham số `now` của bản Python.
- [x] `make test-core` chạy bản C với **đúng các vector đó**, dưới ASan/UBSan.
- [x] Mỗi **source** một instance (mảng `EventManager[N]` trong cùng process); ID reset khi pipeline restart ⇒ tạo lại toàn bộ instance và `pipeline_run_id` mới. Đã làm: `EventManager events` trong `SourceCtx[N]`, khởi tạo trong `init_sources`. Chưa làm (để cùng 2.3, khi có `pipeline_run_id`): tạo lại toàn bộ instance khi pipeline restart.

Không động tới `PersonTracker`/CLI; CLI giữ nguyên behavior.

### Bắt buộc

```bash
pytest          # toàn bộ test cũ + test vector Python vẫn pass
make test-core  # C pass cùng vector
```

---

## 2.2 Event flow

**Trạng thái (2026-11-01):** khung đã chạy: probe → `EventManager[source_id]` → `EventRecord` → `GAsyncQueue` (giới hạn 1024, đầy thì bỏ + đếm) → worker thread `publisher` (hiện in `event {json}` ra stdout). Còn thiếu: snapshot (2.5) và publisher libcurl/retry/spool (2.6). Schema 2.3 đã làm (`engine/src/core/event_record.*`, trường `snapshot` = `null` tới khi có 2.5); `pipeline_run_id` sinh một lần lúc khởi động.

```text
Pad Probe (C)
   ↓
Detection[]
   ↓
EventManager[source_id] (C)   ← now = stream_start[source_id] + PTS
   ↓
PersonDetectedEvent
   ├── Snapshot (nvds_obj_enc_*, trong probe — không chặn)
   └── GAsyncQueue → worker thread → EventPublisher (libcurl → POST /internal/events)
```

## 2.3 Event schema v1

```json
{
  "schema_version": 1,
  "event_id": "uuid",
  "camera_id": 1,
  "pipeline_run_id": "uuid",
  "type": "PERSON_DETECTED",
  "track_id": 12,
  "confidence": 0.91,
  "timestamp": "UTC",
  "bbox": [0.1, 0.2, 0.3, 0.4],
  "snapshot": "PERSON_DETECTED_cam1_....jpg"
}
```

## 2.4 Dedupe

Test:

- [x] đứng yên lâu → 1 event (`stationary_for_ten_minutes`);
- [x] rời ngắn rồi quay lại → không event (`flicker_within_timeout`, `timeout_boundary_is_exclusive`);
- [x] rời quá timeout → event mới (`reappear_after_timeout`);
- [x] hai người → 2 event (`several_new_tracks_one_frame`, `tracks_are_independent`);
- [ ] ID switch → đo event thừa.

Bốn mục đầu là test vector (đã xong; còn lại mục ID switch và so sánh `sync=false` với real-time, chưa làm) (xác định, đúng/sai). Mục cuối đo trên video thật, ghi vào bảng benchmark. Chạy cả `sync=false` lẫn real-time: kết quả event phải **giống nhau** (vì dùng `stream_start + PTS`, không dùng giờ tường).

## 2.5 Snapshot

**Trạng thái (2026-11-01): xong.** Điểm `[CẦN XÁC MINH]` đã kiểm chứng (xem CLAUDE.md): dùng `saveImg=FALSE` + `attachUsrMeta=TRUE` thay vì để thư viện tự ghi file, vì `jpeg_write_to_file` crash khi `fopen` thất bại; thread publisher ghi file. Sơ đồ bên dưới là bản gốc.

Dùng DeepStream object-encoder (đã xác minh có trong `nvds_obj_encode.h`):

```text
event
 ↓
nvds_obj_enc_create_context()        (1 lần, lúc khởi động)
 ↓
nvds_obj_enc_process(ctx, &args, surface, obj_meta, frame_meta)
   args: isFrame = true (ảnh cả khung hình), saveImg = true,
         fileNameImg = data/snapshots/PERSON_DETECTED_cam<ID>_id<N>_<ts>_<event_id[:8]>.jpg,
         quality = ...
 ↓
nvds_obj_enc_finish(ctx)             (gọi sau khi enqueue xong trong probe)
 ↓
JPEG trong data/snapshots/
```

- `nvds_obj_enc_process` được header ghi là **không chặn**; ảnh được encode bất đồng bộ.
- Tên file khớp glob `PERSON_DETECTED_*.jpg` để `SnapshotCleaner` (Backend) dọn được.
- Chỉ chụp khi có event mới (hiếm), không chụp mỗi frame.
- **[CẦN XÁC MINH]** cách lấy `NvBufSurface*` hợp lệ trong probe và chi tiết vòng đời `args`/`NvDsObjEncUsrArgs` — đối chiếu `deepstream-image-meta-test` (đã có trong image) trước khi viết.

Không làm snapshot/network blocking probe.

## 2.6 Publisher (thay cho Telegram trong engine)

Telegram **không** chạy trong engine C: token chỉ nằm ở Backend, và `TelegramNotifier` là Python. Engine chỉ phát event qua HTTP; Backend gửi Telegram (Phase 3).

**Trạng thái (2026-11-01): xong.** Chi tiết ở CLAUDE.md. Snapshot chưa gửi qua HTTP (JSON chỉ có tên file); cách backend lấy file bàn ở Phase 3.

- [x] `EventPublisher`: `GAsyncQueue` có giới hạn + 1 worker thread + libcurl, timeout rõ ràng (2 s connect / 5 s tổng), retry có backoff (1 s → 60 s), spool file JSON-lines khi backend chết (`--backend-url`, `--spool-file`).
- [x] Lỗi mạng chỉ log (che URL/secret), **không bao giờ** chặn probe.

Test:

```text
receiver giả (script) trả lời chậm 3s / lỗi 500
```

nhưng pipeline vẫn chạy bình thường, FPS không tụt, không mất event (spool + `event_id` idempotent).

Kết quả (2 video 1080p, 108 event, mỗi kịch bản một lần): backend chết 703 fps / 108 vào spool; chạy lại với backend sống → 216 event nhận được (cả spool cũ), 216 `event_id` khác nhau; 500 trong 3 s đầu → nhận đủ 108; trả chậm 3 s → 5 nhận + 103 trong spool (đủ 108, FPS 711, thoát sau ≤ 10 s ân hạn); 422 → 108 `rejected`. Chưa thử: backend chết giữa chừng rồi sống lại trong cùng một lần chạy dài, và RTSP thật.

## 2.7 RTSP reconnect

**Trạng thái (2026-11-01): xong.** Chi tiết và những gì đã thử/không dùng được ở CLAUDE.md. Reconnect do `nvurisrcbin` (`rtsp-reconnect-interval`, `rtsp-reconnect-attempts=-1`) làm; engine theo dõi trạng thái `RUNNING`/`RECONNECTING` theo nguồn, không để lỗi của 1 camera RTSP dừng pipeline. Lỗi của nguồn không phải RTSP vẫn dừng pipeline (giới hạn đã biết). Đã chạy thật với camera RTSP giả (`engine/tools/rtsp_test_server.c`): tắt/bật server, server treo (`SIGSTOP`), camera chưa chạy lúc khởi động, và Definition of Done bên dưới (có snapshot + POST receiver giả).

Sau khi MP4 ổn. Làm **theo từng nguồn**: 1 camera mất kết nối không được kéo sập các camera còn lại. Dùng reconnect của `nvurisrcbin` (**[CẦN XÁC MINH]**) hoặc tự xử lý; lỗi/EOS của 1 source không được `g_main_loop_quit` khi còn source khác đang chạy. Trạng thái của từng camera:

```text
RTSP
 ↓
mất kết nối
 ↓
RECONNECTING
 ↓
kết nối lại
 ↓
RUNNING
```

### Definition of Done

DeepStream có thể:

```text
video
 ↓
detect
 ↓
track
 ↓
event
 ↓
snapshot
 ↓
POST event (receiver giả)
```

mà pipeline không bị block. (Telegram được nghiệm thu ở Phase 3, vì do Backend gửi.)

---

# Phase 3 — FastAPI Backend

**Mục tiêu:** đưa hệ thống từ script thành application.

## 3.1 Backend foundation

**Trạng thái (2026-11-02):** 3.1, 3.2, 3.3 (cả start/stop/status ở mức tiến trình engine), 3.4, 3.5 (Engine Manager) và `POST /internal/events` xong, engine gửi `X-Internal-Token` từ biến môi trường `DS_ENGINE_TOKEN`; 3.6 heartbeat, 3.7 WebSocket và 3.8 Telegram + dọn snapshot xong (2026-11-02). Database là **PostgreSQL** (chủ repo chọn, thay cho SQLite).

- [x] FastAPI (`backend/main.py`, `create_app` factory).
- [x] Pydantic (v2; `backend/schemas.py`).
- [x] SQLAlchemy 2 + psycopg 3 (`backend/db.py`, `backend/models.py`).
- [x] Alembic (`backend/alembic.ini`, `backend/migrations/`).
- [x] PostgreSQL (thay cho "SQLite WAL").
- [x] `/api/health` (200, hoặc 503 khi không tới được database).

## 3.2 Database

### cameras

```text
id
name
source_type
source_uri
enabled
ai_enabled
desired_state
conf_threshold
absence_timeout
created_at
updated_at
deleted_at
```

### events

```text
event_id
camera_id
pipeline_run_id
type
track_id
confidence
timestamp
bbox
snapshot_file
status
notify_status
```

Không lưu metadata từng frame.

---

## 3.3 Camera API

```text
GET    /api/cameras
POST   /api/cameras
GET    /api/cameras/{id}
PATCH  /api/cameras/{id}
POST   /api/cameras/{id}/start
POST   /api/cameras/{id}/stop
GET    /api/cameras/{id}/status
```

## 3.4 Event API

```text
GET /api/events
GET /api/events/{id}
GET /api/events/{id}/snapshot
```

Có:

- filter;
- pagination;
- date range;
- camera filter.

## 3.5 Engine ↔ Backend

**Trạng thái (2026-11-02): xong.** Engine Manager là service riêng `engine_manager/` (loopback, không auth); backend đẩy danh sách camera qua bộ đối soát nền (so băm revision), start/stop trả ngay và áp dụng bất đồng bộ. Chi tiết ở CLAUDE.md. Restart khi danh sách đổi: mọi camera mất vài giây hình.

MVP dùng HTTP.

```text
Backend
   ↓  HTTP (start / stop / status / probe)
Engine Manager (Python, giám sát process)
   ↓  spawn / kill / restart có backoff
ds_engine (C), 1 process = N camera (1 pipeline batch)
```

và:

```text
ds_engine (C, libcurl)
   ↓
POST /internal/events      (header X-Internal-Token, idempotent theo event_id)
POST /internal/heartbeat
```

Engine Manager **không** chạm DeepStream: chỉ truyền tham số (`--camera ID=URI`, `--config`…) cho binary C và theo dõi mã thoát. Không dùng Redis/Kafka/MQTT ở MVP.

Thêm/xoá/bật/tắt camera: MVP = Manager ghi lại danh sách camera đang bật rồi **restart process** với danh sách `--camera` mới (đơn giản; đổi lại, mọi camera mất vài giây hình). Thêm/xoá nguồn lúc đang chạy (không restart) để sau, chỉ làm nếu restart gây phiền — **[CẦN XÁC MINH]** sample `runtime_source_add_delete` có trong image 7.1. Vì vậy `POST /api/cameras/{id}/start|stop` = đổi danh sách + restart.

## 3.6 Heartbeat

**Trạng thái (2026-11-02): xong.** Engine `--heartbeat-url` + `--heartbeat-interval` (mặc định 5 s); backend `POST /internal/heartbeat` (204), giữ trong RAM (`backend/heartbeats.py`), hiện ở `stream` của `GET /api/cameras/{id}/status`; một heartbeat cũ hơn `HEARTBEAT_STALE_AFTER` (15 s) thì `reporting=false`. Chi tiết ở CLAUDE.md.

Do chính process C gửi (worker thread của publisher), khoảng vài giây/lần. **1 heartbeat cho cả process**, chứa trạng thái từng camera:

```json
{
  "run_id": "...",
  "cameras": [
    {"camera_id": 1, "state": "RUNNING",      "fps": 28.4, "last_frame_age": 0.03},
    {"camera_id": 2, "state": "RECONNECTING", "fps": 0.0,  "last_frame_age": 12.5}
  ]
}
```

## 3.7 WebSocket

**Trạng thái (2026-11-02): xong.** `WS /api/ws`, một chiều server → client; chi tiết ở CLAUDE.md và README.

```text
WS /api/ws
```

Event:

```text
event.created
camera.status
```

## 3.8 Telegram + dọn snapshot (Backend, Python)

**Trạng thái (2026-11-02): xong** (chưa thử với Telegram thật, chỉ với bản giả). Chi tiết ở CLAUDE.md.

- [ ] Worker nền dùng lại `src/notifications/telegram_notifier.py`; ghi `notify_status`; lỗi Telegram không làm mất event.
- [ ] Job định kỳ dùng lại `src/snapshot_management.py` (`SnapshotCleaner`); file bị dọn thì API snapshot trả 404 gọn.
- [ ] Test: `sleep 3s` ở Telegram không làm chậm `POST /internal/events`.

### Definition of Done

```text
DeepStream (C)
    ↓
HTTP event
    ↓
FastAPI
    ↓
PostgreSQL
    ↓
WebSocket
```

và:

```text
Telegram
```

được gửi từ backend.

---

# Phase 4 — Video Streaming

**Mục tiêu:** đưa video DeepStream lên browser.

**Trạng thái (2026-11-02):** 4.1, 4.2, 4.3 xong và Definition of Done đạt (DeepStream → MediaMTX → Chrome, thấy video + bbox + track ID); 4.4 đo một phần (xem CLAUDE.md); 4.5 không cần (WebRTC chạy). **Khác kế hoạch:** engine đẩy **RTMP**, không phải RTSP (`rtspclientsink` của GStreamer 1.20.3 deadlock khi chuyển NULL sau lỗi kết nối), và mỗi camera có publisher riêng (appsrc ! rtmp2sink) ngoài pipeline chính; path MediaMTX là `live/cam<ID>` (rtmp2sink đòi có phần application trong URL).

## 4.1 MediaMTX

Mỗi camera một path riêng (`cam<ID>`), lấy từ nhánh `nvstreamdemux` tương ứng (xem 1.6). **[CẦN XÁC MINH]** giới hạn số phiên NVENC đồng thời của RTX 4060: ảnh hưởng số camera có stream overlay. Nếu chạm trần, chỉ encode camera đang được xem hoặc dùng encoder CPU.

```text
DeepStream
 ↓
H264
 ↓
RTSP publish
 ↓
MediaMTX
```

## 4.2 Browser

```text
MediaMTX
 ↓
WebRTC/WHEP
 ↓
Browser
```

## 4.3 API

```text
GET /api/cameras/{id}/stream
```

trả URL stream.

Frontend không bao giờ nhận RTSP credential của camera.

## 4.4 Đo

Đo:

- glass-to-glass latency;
- FPS;
- GPU;
- CPU;
- VRAM.

Thử:

- Chrome;
- Firefox;
- điện thoại cùng LAN.

## 4.5 Fallback

Nếu WebRTC trở thành blocker:

```text
MediaMTX + HLS/LL-HLS
```

được dùng làm phương án tạm thời.

**Không làm WebRTC và HLS song song chỉ để "cho đủ".**

### Definition of Done

Có thể:

```text
DeepStream → MediaMTX → Browser
```

và nhìn thấy:

```text
video + bbox + track ID
```

---

# Phase 5 — Frontend MVP

**Mục tiêu:** xây dựng một frontend **đẹp, hiện đại, trực quan và có cảm giác như một sản phẩm thực tế**.

```text
React
  ↓
REST API
  ↓
FastAPI
```

và:

```text
React
  ↓
WebSocket
  ↓
Realtime events
```

Stack:

```text
React
TypeScript
Vite
TanStack Query
WebSocket
```

Có thể sử dụng thêm các UI/component/icon/animation libraries phù hợp để tạo giao diện hiện đại.

---

## 5.1 UI/UX Design

Frontend phải được thiết kế theo hướng:

- Modern;
- Clean;
- Professional;
- Dark mode ưu tiên vì đây là hệ thống camera monitoring;
- Responsive;
- Có hierarchy rõ ràng;
- Thông tin quan trọng dễ nhìn;
- Không tạo cảm giác như một project CRUD cơ bản.

Có thể sử dụng:

- component library;
- icon library;
- chart library;
- animation/motion library;
- toast/notification library;

miễn là chúng giúp giao diện đẹp và dễ sử dụng.

### Animation

Được phép và nên sử dụng animation ở mức hợp lý:

- page transition;
- sidebar transition;
- modal;
- dropdown;
- hover effect;
- card interaction;
- realtime event xuất hiện;
- notification/toast;
- loading/skeleton;
- trạng thái Online/Offline;
- camera switching.

**Không lạm dụng animation.**

Animation phải phục vụ UX, không làm giao diện rối hoặc ảnh hưởng performance.

---

## 5.2 Dashboard

Dashboard là màn hình chính.

Hiển thị:

```text
┌─────────────────────────────────────────────┐
│ Dashboard                                   │
├─────────────────────────────────────────────┤
│                                             │
│ Cameras     Online       People      Events │
│    4           3            5          12   │
│                                             │
├─────────────────────────────────────────────┤
│                                             │
│ Recent Events                               │
│                                             │
├─────────────────────────────────────────────┤
│                                             │
│ Camera Overview / Statistics                │
│                                             │
└─────────────────────────────────────────────┘
```

Dashboard cần có:

- Camera count;
- Online/Offline count;
- Current people count;
- Events today;
- Recent events;
- Camera status;
- FPS;
- Realtime updates.

Các statistic cards nên có:

- icon;
- visual hierarchy;
- hover effect;
- subtle animation;
- loading state;
- empty state.

Nếu phù hợp, sử dụng chart để hiển thị statistics nhưng **không cần xây dựng hệ thống analytics phức tạp**.

---

## 5.3 Monitoring

Màn hình Monitoring là chức năng quan trọng nhất.

Layout đề xuất:

```text
┌──────────────┬────────────────────────────────┐
│              │                                │
│ Camera List  │        Video Player            │
│              │                                │
│ Camera 01 ●  │                                │
│ Camera 02 ●  │                                │
│ Camera 03 ○  │                                │
│              │                                │
│              ├────────────────────────────────┤
│              │ FPS   People   Status          │
└──────────────┴────────────────────────────────┘
```

Hiển thị:

- Camera selector;
- Camera name;
- Online/Offline;
- Video player;
- Pipeline status;
- FPS;
- People count;
- Last error;
- Last update time.

Camera đang được chọn phải có visual state rõ ràng.

Khi chuyển camera:

- hiển thị loading state;
- transition mượt;
- xử lý camera offline;
- hiển thị error state rõ ràng.

---

## 5.4 Event History

Hiển thị lịch sử event.

Thông tin:

```text
Timestamp
Camera
Event type
Track ID
Confidence
Snapshot
```

Có:

- filter;
- pagination hoặc infinite scroll;
- event detail;
- snapshot preview;
- loading state;
- empty state;
- error state.

Event mới nhận qua WebSocket phải xuất hiện realtime.

Ví dụ:

```text
DeepStream
    ↓
Event
    ↓
FastAPI
    ↓
Database
    ↓
WebSocket
    ↓
React
    ↓
New Event animation
```

Event mới có thể:

- xuất hiện ở đầu danh sách;
- highlight trong thời gian ngắn;
- hiển thị toast/notification nhẹ.

---

## 5.5 Camera Detail / Event Detail

Khi người dùng click vào camera hoặc event, có thể mở:

- detail page;
- modal;
- drawer;

tùy thiết kế UI.

Event detail nên hiển thị:

```text
Event
Camera
Timestamp
Confidence
Track ID
Snapshot
```

Không cần xây dựng quá nhiều chức năng quản trị.

---

## 5.6 Realtime WebSocket

Frontend phải có WebSocket client.

Backend gửi event:

```json
{
  "event_id": "...",
  "camera_id": "...",
  "event_type": "PERSON_DETECTED",
  "timestamp": "...",
  "confidence": 0.91,
  "snapshot_url": "..."
}
```

Frontend nhận event và cập nhật UI mà không cần reload page.

Cần xử lý:

- connect;
- reconnect;
- disconnect;
- connection status;
- duplicate event;
- error state.

Có thể hiển thị trạng thái:

```text
● Live
```

hoặc:

```text
○ Reconnecting...
```

---

## 5.7 Loading / Empty / Error States

Không được chỉ thiết kế trạng thái "mọi thứ hoạt động".

Mỗi màn hình cần có:

### Loading

```text
Skeleton
Spinner
Loading indicator
```

### Empty

Ví dụ:

```text
No cameras found
No events yet
No recent activity
```

### Error

Ví dụ:

```text
Failed to load cameras
Connection lost
Camera unavailable
```

UI phải xử lý các trạng thái này một cách đẹp và nhất quán.

---

## 5.8 Responsive

Không cần tối ưu mobile quá sâu.

Ưu tiên:

```text
Desktop
Laptop
Tablet
```

Vì đây là hệ thống monitoring.

Desktop là màn hình chính.

---

## 5.9 Frontend Architecture

Frontend cần có structure rõ ràng nhưng **không over-engineer**.

Ví dụ:

```text
frontend/
├── src/
│   ├── components/
│   ├── pages/
│   ├── layouts/
│   ├── hooks/
│   ├── api/
│   ├── websocket/
│   ├── types/
│   └── utils/
```

Tách rõ:

```text
UI components
API calls
WebSocket logic
Types
Pages
```

Không cần:

- Redux;
- GraphQL;
- Next.js;
- SSR;
- micro-frontends;
- design system tự xây dựng;
- state management phức tạp.

TanStack Query được dùng cho server state/API data.

---

# 5.10 Visual Direction

UI nên có cảm giác giống một **AI video surveillance dashboard hiện đại**, thay vì một admin dashboard CRUD.

Ưu tiên:

- Dark theme;
- Sidebar navigation;
- Card-based layout;
- Clear typography;
- Consistent spacing;
- Modern icons;
- Subtle gradients nếu phù hợp;
- Subtle glow/effects cho trạng thái realtime;
- Smooth transitions;
- Clear status indicators;
- Video là visual element quan trọng nhất.

Tuy nhiên:

> **Không biến UI thành một "demo AI flashy".**

Ưu tiên usability và readability.

---

# 5.11 Frontend không học lan man

Người học **không cần học frontend chuyên sâu**.

Chỉ cần hiểu:

```text
React component
        ↓
API request
        ↓
FastAPI
        ↓
Database
```

và:

```text
FastAPI
   ↓
WebSocket
   ↓
React
   ↓
Realtime UI
```

Cần hiểu được:

- component;
- props/state cơ bản;
- API request;
- JSON;
- WebSocket;
- loading/error state;
- cách frontend lấy dữ liệu từ backend.

Phần UI implementation có thể sử dụng Vibecoding / AI coding agent.

---

# 5.12 Definition of Done

Frontend hoàn chỉnh khi có flow:

```text
User mở browser
      ↓
Dashboard
      ↓
chọn camera
      ↓
xem video
      ↓
DeepStream detect person
      ↓
DeepStream tạo event
      ↓
FastAPI nhận event
      ↓
Backend lưu DB
      ↓
Backend gửi WebSocket
      ↓
Frontend nhận event
      ↓
Event xuất hiện realtime
      ↓
Snapshot hiển thị
```

UI phải đạt:

- đẹp;
- hiện đại;
- responsive;
- có loading state;
- có empty state;
- có error state;
- có realtime update;
- có animation hợp lý;
- dễ sử dụng.

**Mục tiêu cuối cùng:**

> Người học không trở thành Frontend Developer, nhưng có khả năng xây dựng một AI application có frontend đủ đẹp để demo và đủ tốt để sử dụng thực tế.

---

# Phase 6 — Camera Management + Multi-camera

**Mục tiêu:** biến MVP thành hệ thống có thể sử dụng thực tế.

**Trạng thái (2026-11-02):** 6.1 (trang Cameras: thêm/sửa/xóa/Start/Stop) và 6.2 (`POST /api/cameras/test-connection`, thử với MediaMTX thật có Digest) xong; 6.3 làm một phần (2026-11-03: luồng RTSP công cộng H.264 720p chạy sạch 60 s qua engine thật; chưa có soak dài, H.265, mất mạng thật, camera thật), 6.4/6.5 xong bằng camera RTSP giả 1080p (`engine/tools/benchmark.py`): 1 process × N camera giữ 30 fps tới 20 camera với GPU 75%, 24 sát trần (NVDEC 99%), 28 hỏng; N process × 1 camera nặng hơn nhiều (N=8: RSS 5,1 GiB, CPU 95% so với 1,2 GiB, 34%) nên giữ 1 pipeline = N camera. Latency chưa đo; chưa đo gộp phát hiện + stream. Chi tiết và giới hạn ở CLAUDE.md.

## 6.1 Camera CRUD UI

```text
Add
Edit
Delete
Start
Stop
```

## 6.2 Test connection

```text
POST /api/cameras/test-connection
```

Kiểm tra:

- URL;
- authentication;
- codec;
- resolution;
- timeout.

## 6.3 RTSP thực

Test:

- H.264;
- H.265;
- password có ký tự đặc biệt;
- mất mạng;
- reconnect;
- latency/jitter.

## 6.4 Multi-camera benchmark

Chạy:

```text
1 camera
2 cameras
3 cameras
...
N cameras
```

Đo:

```text
FPS
GPU utilization
VRAM
CPU
latency
stability
```

## 6.5 Quyết định kiến trúc

Mặc định từ đầu (xem mục 4):

```text
1 pipeline = N cameras   (nvstreammux batch)
```

Benchmark so sánh với phương án đối chứng:

```text
N process × 1 camera
```

Chỉ tách thành nhiều process (mỗi process một nhóm camera) nếu benchmark hoặc độ ổn định cho thấy cần: cô lập lỗi, chạm trần NVDEC/NVENC, hoặc `batch-size` lớn làm latency tăng.

### Definition of Done

Biết được:

> RTX 4060 8GB chạy ổn tối đa bao nhiêu camera với cấu hình hiện tại.

---

# Phase 7 — Advanced Features

**Chỉ làm sau khi MVP chạy ổn.**

Không bắt buộc phải làm hết.

## Ưu tiên 1

### 7A — Statistics

```text
FPS
GPU
VRAM
CPU
events/day
camera uptime
```

## Ưu tiên 2

### 7B — People count

```text
people_now
```

sau đó mới cân nhắc line crossing.

## Ưu tiên 3

### 7C — ROI intrusion

```text
nvdsanalytics
```

Event:

```text
ROI_INTRUSION
```

## Ưu tiên 4

### 7D — Smart Record

Event:

```text
person detected
```

→ clip:

```text
pre-roll
+
event
+
post-roll
```

## Ưu tiên 5

### 7E — Overlay on/off

Có hai stream:

```text
camera_raw
camera_ai
```

## Ưu tiên 6

### 7F — Authentication

Chỉ bắt buộc nếu hệ thống ra ngoài LAN.

## Ưu tiên 7

### 7G — PostgreSQL

Đã làm từ Phase 3 (chủ repo chọn PostgreSQL ngay từ đầu, 2026-11-02); mục này không còn việc.

---

# Phase 8 — Production / Docker / Documentation

**Không cần làm sớm.**

**Trạng thái (2026-11-03):** Docker Compose xong và đã chạy thật (`deploy/compose.yml`, 5 container, thử qua nginx + WebRTC trong Chrome + tự hồi phục khi tiến trình chính thoát); Telegram thật đã thử qua backend. README đã đủ các mục Documentation (Architecture, Installation, Run, Configuration, Testing, Benchmark, Troubleshooting, Demo); checklist mục 12 đã duyệt 21/21. Soak: công cụ `deploy/soak.py` xong, **lần chạy 4 giờ / 4 camera bị cắt sau 5 phút vì máy tắt nguồn (2026-11-03 12:57 UTC), chưa có kết quả nào**. Còn: chạy lại soak (máy phải bật suốt), kiểm chứng trên LAN/điện thoại/camera thật. DB backup tự động đã xong (2026-11-03). Chi tiết ở CLAUDE.md.

## Docker Compose

```text
engine        (Dockerfile.engine: base DeepStream 7.1, `make CUDA_VER=12.6`, + Engine Manager Python)
backend
mediamtx
frontend/nginx
```

## Security

- [ ] `.env.example`
- [ ] secret không bake vào image;
- [ ] không log RTSP password;
- [ ] không log Telegram token;
- [ ] `/internal/*` không public;
- [ ] auth trước khi expose Internet.

## Reliability

- [ ] healthcheck;
- [ ] restart policy;
- [ ] engine crash recovery;
- [ ] backend restart recovery;
- [ ] RTSP reconnect;
- [ ] snapshot retention;
- [x] DB backup. — service `backup` (`deploy/backup.sh`, 2026-11-03): `pg_dump -Fc` mỗi 24 giờ vào `data/backups/`, giữ 7 bản; đã thử với stack tạm (chu kỳ 20 s): xoay vòng đúng, dọn file tạm sót, DB tắt thì báo lỗi và giữ nguyên bản cũ, restore vào DB mới và `--clean --if-exists` đè lên dữ liệu đã sửa đều khớp. Chưa tự copy sang máy/ổ khác (bạn tự làm).

## Soak test

Chạy nhiều giờ.

Kiểm tra:

```text
RAM
VRAM
CPU
FPS
process count
disk usage
```

Không được tăng bất thường theo thời gian.

## Documentation

README phải có:

```text
Architecture
Installation
Run
Configuration
Testing
Benchmark
Troubleshooting
Demo
```

---

# 7. Thứ tự học thực tế

Không học toàn bộ DeepStream rồi mới làm project.

Học theo vòng lặp:

```text
Học 1 khái niệm
      ↓
chạy sample
      ↓
sửa code
      ↓
đo kết quả
      ↓
ghi lại
      ↓
sang khái niệm tiếp
```

## Thứ tự DeepStream

```text
C cơ bản (con trỏ, struct, malloc/free, Makefile, pkg-config)
   ↓
GLib (GMainLoop, GAsyncQueue, GThread, g_free)
   ↓
GStreamer
   ↓
GstElement / GstPad / GstBuffer
   ↓
Pipeline
   ↓
nvstreammux (N nguồn → 1 batch)
   ↓
nvinfer
   ↓
TensorRT
   ↓
NvDsBatchMeta
   ↓
NvDsFrameMeta
   ↓
NvDsObjectMeta
   ↓
Pad Probe
   ↓
nvtracker
   ↓
track_id
   ↓
nvdsosd
   ↓
Event
```

---

# 8. Các mốc quan trọng

## Milestone A — AI Core

```text
YOLO
 ↓
TensorRT
 ↓
DeepStream
 ↓
nvinfer
 ↓
nvtracker
 ↓
pad probe
 ↓
track_id
```

Đây là **mốc quan trọng nhất**.

---

## Milestone B — AI Application

```text
track_id
 ↓
EventManager
 ↓
snapshot
 ↓
Publisher (HTTP POST event)
```

---

## Milestone C — Backend

```text
Engine
 ↓
FastAPI
 ↓
PostgreSQL
 ↓
WebSocket + Telegram (Backend gửi)
```

---

## Milestone D — Web

```text
DeepStream
 ↓
MediaMTX
 ↓
WebRTC
 ↓
React
```

---

## Milestone E — Full System

```text
Camera
 ↓
DeepStream
 ↓
Event
 ↓
Backend
 ↓
Database
 ↓
WebSocket
 ↓
Frontend
```

---

# 9. Những thứ cố tình KHÔNG làm sớm

Không làm trước MVP:

```text
Kubernetes
Redis
Kafka
MQTT
PostgreSQL
JWT
Microservices
Face recognition
ReID
Smart Record
ROI
fancy frontend
mobile app
cloud
```

Lý do:

> Chúng không giúp trả lời câu hỏi quan trọng nhất của project: **DeepStream có chạy ổn inference + tracking + event trên GPU của mình hay không?**

> Cập nhật 2026-11-03: danh sách này chỉ nói về **thứ tự** (đừng làm trước khi pipeline DeepStream chạy ổn). Pipeline đã chạy ổn từ Phase 1 nên PostgreSQL (Phase 3) và frontend đẹp (Phase 5, xem 5.1) đã được làm theo đúng thứ tự Phase; không còn mục nào ở đây bị cấm.

---

# 10. Benchmark bắt buộc

Tạo một bảng benchmark duy nhất.

| Test | DeepStream |
|---|---:|
| FPS | đo |
| GPU | đo |
| VRAM | đo |
| CPU | đo |
| Events | đo |
| ID switch | đo |
| Latency | đo |

Sau đó, mỗi mức N so sánh hai kiểu chạy: **1 pipeline batch N nguồn** (đang chọn) và **N process × 1 nguồn** (đối chứng):

```text
1 camera
2 cameras
3 cameras
...
```

Mục tiêu không phải tạo con số đẹp.

Mục tiêu là trả lời:

> RTX 4060 8GB chạy ổn tối đa bao nhiêu camera, và batch N nguồn trong 1 pipeline có đáng hơn N process × 1 nguồn không?

---

# 11. Testing strategy

## Host

```bash
pytest          # Python: EventManager (spec), test vector, backend API, WebSocket, DB, secret masking
make test-core  # C thuần (engine/src/core): EventManager, JSON event, mask URL — dưới ASan/UBSan
```

Test:

- EventManager (Python + C chạy chung vector);
- payload (JSON do C sinh ra phải parse được bằng Pydantic của backend — test hợp đồng);
- secret masking (cả Python lẫn C);
- backend API;
- WebSocket;
- database.

## GPU container

```bash
cd engine && make CUDA_VER=12.6
pytest -m deepstream     # chạy binary ds_engine bằng subprocess, kiểm tra mã thoát + output
```

Test:

- pipeline;
- nvinfer;
- tracker;
- probe;
- detection;
- output.

Kiểm tra rò bộ nhớ: chạy `valgrind`/`compute-sanitizer` trên clip ngắn ở Phase 1–2, và soak test ở Phase 8.

## End-to-end

```text
RTSP giả lập
 ↓
DeepStream
 ↓
Backend
 ↓
PostgreSQL
 ↓
WebSocket
 ↓
Frontend
 ↓
Telegram
```

---

# 12. Definition of Done — MVP

MVP chỉ được coi là xong khi:

**Duyệt 2026-11-03: 21/21 đạt trong phạm vi camera giả + localhost + Docker trên một máy.** Chưa kiểm chứng ngoài thực địa (camera thật, IP LAN, điện thoại, Firefox, chạy nhiều giờ): xem giới hạn ở từng ô và mục "Việc tiếp theo" trong CLAUDE.md. Bằng chứng chạy lại ngày duyệt: `make test-core` 27 test C 0 lỗi; `pytest` 231 test xanh; 16 vector `tests/vectors/event_manager_*.json` dùng chung bản C và Python.

- [x] Engine C build bằng `make` trong DeepStream container. — `make CUDA_VER=12.6` (cả trong `deploy/Dockerfile.engine`, build Docker thành công 2026-11-03).
- [x] DeepStream chạy YOLOv8n. — hàng nghìn frame: 2 video 1080p (1443 frame/nguồn), luồng RTSP công cộng 1609 frame, 24 camera giả 30 fps.
- [x] TensorRT FP16 hoạt động. — `network-mode=2` trong `pgie_yolov8n.txt`; engine `*_gpu0_fp16.engine` (b1…b28) do `nvinfer` build.
- [x] Tracker hoạt động. — NvDCF: 29 track/nguồn trên video mẫu, 6 track trên luồng RTSP công cộng. Chưa đo ID switch (không có ground truth).
- [x] Pad probe đọc `NvDsObjectMeta`. — probe trên src pad của `nvtracker`, `probes.c` (`extract_person_detections`), không giữ con trỏ meta.
- [x] Có `track_id`. — mọi event có `track_id`; khóa là `(source_id, track_id)`.
- [x] Event dedupe đúng (bản C và bản Python cùng pass test vector). — 16 vector, `make test-core` + `pytest` cùng xanh.
- [x] Snapshot được tạo. — 29/29 event có JPEG 1080p tải được qua API (Docker); `--snapshot-dir` không ghi được thì không crash.
- [x] Engine C xử lý ≥ 2 nguồn trong 1 pipeline (batch), event gắn đúng `camera_id`. — chạy tới 28 nguồn một pipeline; 2 camera cho cam 1 = 108, cam 2 = 54 event đúng id.
- [x] Publisher không block pipeline (receiver chậm 3s, FPS không tụt); Telegram (Backend) không làm chậm `POST /internal/events`. — receiver trả chậm 3 s: FPS 711 (ngang 703-715); test: 2 POST < 1 s khi Telegram `sleep 3s`.
- [x] FastAPI nhận event. — `POST /internal/events` (201/200/422), token qua `DS_ENGINE_TOKEN`.
- [x] PostgreSQL lưu event. — 108 event (chạy tay), 157 event còn nguyên sau khi container restart (Docker).
- [x] Event idempotent. — `event_id` là khóa chính, `ON CONFLICT DO NOTHING`; spool phát lại không tạo bản trùng (216 id khác nhau, spool 0 byte).
- [x] WebSocket đẩy event realtime. — test với uvicorn + client `websockets` thật; Chrome thấy toast "Person detected" ngay, qua cả nginx (Docker). Lỗi thiếu `websockets` trong `requirements.txt` đã sửa.
- [x] MediaMTX phát video. — `stream is available and online (H264)`; 12 phiên NVENC đồng thời.
- [x] Browser xem được video. — Chrome WebRTC 1920×1080, bbox + `person N`, cả localhost và qua cổng map của Docker. **Giới hạn:** chưa thử Firefox, điện thoại, `PUBLIC_HOST` là IP LAN, chưa đo glass-to-glass latency.
- [x] Frontend hiển thị event. — Dashboard/Monitoring/Events, drawer ảnh + bbox, Acknowledge; chạy trong Chrome qua nginx (Docker).
- [x] RTSP reconnect hoạt động (từng nguồn, không ảnh hưởng nguồn khác). — camera giả: tắt cam 1, cam 2 giữ 30 fps, cam 1 về 30 fps; `SIGSTOP` phát hiện sau 5 s. **Giới hạn:** chưa thử camera thật, mật khẩu sai 401, mất mạng thật ngoài localhost.
- [x] Không leak credential. — mật khẩu RTSP không vào argv (`--cameras-file` quyền 600, test), log của engine/manager che `user:pass@`, API trả URI đã che, thông báo test-connection không chứa URL; quét file git theo dõi không thấy token/mật khẩu thật, `.env` không được commit. **Giới hạn:** token Telegram/`INTERNAL_TOKEN` truyền qua biến môi trường nên `docker inspect` thấy được (chấp nhận trong LAN); chưa soi toàn bộ log container sau nhiều giờ.
- [x] Có benchmark thật. — `engine/tools/benchmark.py`: 1 process × N camera giữ 30 fps tới 20 camera (GPU 75%), 24 sát trần (NVDEC 99%), 28 hỏng; N process × 1 camera nặng hơn nhiều. **Giới hạn:** camera giả (cùng một video), chưa đo latency, chưa đo gộp phát hiện + stream ở N=12.
- [x] Test cũ vẫn pass. — 231 pytest, 27 test C, 16 vector; không sửa test cũ để cho qua.

---

# 13. Ưu tiên nếu bị thiếu thời gian

Nếu project bị thiếu thời gian, **dừng theo thứ tự từ dưới lên**:

```text
❌ Auth
❌ Smart Record
❌ ROI
❌ Statistics nâng cao
❌ Multi-camera optimization
❌ Camera Management đẹp
❌ Frontend polish (phần trau chuốt thêm ngoài 5.1-5.10; giao diện dùng được và đủ đẹp để demo vẫn là yêu cầu của Phase 5)

        ↑

MUST HAVE

DeepStream (N nguồn, batch)
YOLO/TensorRT
Tracker
Pad Probe
Event
Snapshot
FastAPI
PostgreSQL
WebSocket
Video streaming
React MVP
```

---

# 14. Giá trị chính của project đối với CV

Không mô tả đơn giản là:

> "Built an AI surveillance system."

Nên hướng tới giá trị kỹ thuật thực tế:

```text
Migrated an existing YOLOv8 + ByteTrack surveillance
pipeline to a multi-stream C application on NVIDIA DeepStream with
TensorRT inference, GPU-accelerated tracking, metadata
processing through pad probes, event deduplication, FastAPI backend,
WebSocket realtime events and WebRTC video streaming.
```

Sau khi benchmark có số liệu thật mới thêm:

```text
FPS
latency
VRAM
number of cameras
```

Không đưa số liệu chưa đo.

---

# 15. Thứ tự thực hiện cuối cùng

Đây là thứ tự **khuyến nghị thực tế nhất**:

```text
PHASE 0
Environment + baseline
        ↓
PHASE 1
DeepStream + YOLO + TensorRT + Tracker + Probe
        ↓
PHASE 2
Event + Snapshot + Publisher (HTTP)
        ↓
PHASE 3
FastAPI + PostgreSQL + WebSocket + Telegram
        ↓
PHASE 4
MediaMTX + WebRTC
        ↓
PHASE 5
React frontend
        ↓
PHASE 6
Camera management + multi-camera benchmark
        ↓
PHASE 7
Advanced features
        ↓
PHASE 8
Docker + security + documentation
```

> **Điểm quan trọng nhất:** đừng coi roadmap này là checklist phải hoàn thành 100%. Mỗi phase phải tạo ra một hệ thống chạy được. Nếu một công nghệ mới trở thành blocker, dùng phương án đơn giản hơn để đi tiếp rồi quay lại tối ưu sau.
