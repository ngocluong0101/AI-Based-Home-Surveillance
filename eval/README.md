# eval/ — đo hệ thống phát hiện ngã và xô xát (ROADMAP_V2)

> **Lưu ý 2026-11-20 (nguồn 4:3):** engine từng kéo giãn mọi nguồn không phải 16:9 cho vừa khung 1920x1080 (UR Fall 640x480 bị rộng ra 1,33 lần, mọi góc thân bị lệch). Nay mặc định **giữ tỉ lệ, đệm đen hai bên** (`enable-padding`); cờ `--stretch-sources` trả về cách cũ. Mọi số trên UR Fall của 2026-11-19 (`eval/results/2026-11-19_*`) đo khi còn giãn; đã đo lại bản giữ tỉ lệ ở `eval/results/2026-11-20_v2-8_*` và `…_m4_pad_*`. Muốn tái hiện số cũ: thêm `--stretch-sources` vào lệnh engine (dump cũ còn ở `eval/work/urfall_stretch/`). Nguồn 16:9 (MOT17, camera 1080p) không đổi.


Mọi thứ ở đây là công cụ **offline** (Python + container DeepStream); không nằm trong đường xử lý frame của engine.
Định nghĩa hành vi và bốn chỉ số M1-M4: [DEFINITIONS.md](DEFINITIONS.md). Dữ liệu và license: [../datasets/README.md](../datasets/README.md).
Số liệu đã chốt: [results/](results/) (JSON nhỏ, được commit). File trung gian: `work/` (không commit).

## Chuẩn bị (một lần)

```bash
python3 -m venv --system-site-packages eval/.venv
eval/.venv/bin/pip install motmetrics pycocotools scipy pandas     # motmetrics 1.4.0 cần thêm một dòng vá cho NumPy 2 (đã có trong mot_eval.py)
eval/.venv/bin/python eval/prepare_data.py                           # COCO val2017 + MOT17 train vào datasets/
# engine: build trong container DeepStream (host không có DeepStream)
eval/tools/in_container.sh /bin/bash -c 'cd engine && make CUDA_VER=12.6 && make -C parser CUDA_VER=12.6'
```
Chạy mọi thứ bằng `eval/.venv/bin/python` (venv có sẵn torch/ultralytics/onnxruntime của môi trường chính qua `--system-site-packages`).
**Máy là laptop:** các script đọc video theo luồng (không giữ cả video trong RAM), ONNX Runtime chạy `--threads 4`, nên chạy kèm `nice -n 19`.
Một lần build TensorRT mới mất ~3 phút và dùng GPU hết sức.

## V2-1 — detection

```bash
# 1. 1000 ảnh COCO -> video 1080p H.264 (cả engine lẫn bản tham chiếu Python đọc cùng file này)
eval/.venv/bin/python eval/detection/make_coco_video.py --n 1000
eval/tools/in_container.sh eval/tools/encode_h264.sh png 'eval/work/coco/frames/%06d.png' eval/work/coco/coco1000.mp4
# 2. engine với ngưỡng 0.001 để lấy đường cong AP, ghi dump
eval/.venv/bin/python eval/tools/make_pgie_config.py --batch 1 --threshold 0.001 --out eval/work/pgie_b1_t0001.txt
eval/tools/in_container.sh engine/build/ds_engine --config /work/eval/work/pgie_b1_t0001.txt \
  --tracker-config /work/engine/configs/tracker_nvdcf.yml --camera 1=file:///work/eval/work/coco/coco1000.mp4 \
  --dump-detections /work/eval/work/coco/ds_dump.csv --fps-interval 0
# 3. AP của 6 pipeline (PyTorch / ONNX Runtime / TensorRT) -> results/..._coco_ap.json
nice -n 19 eval/.venv/bin/python eval/detection/coco_ap.py
```

## V2-1 — vì sao engine ra ít người hơn Ultralytics, và tracking

```bash
# video từ MOT17 (camera cố định 1080p: 02, 04, 09)
for n in 02 04 09; do eval/tools/in_container.sh eval/tools/encode_h264.sh jpg \
  "datasets/mot17/MOT17/train/MOT17-$n-FRCNN/img1/%06d.jpg" eval/work/mot17/MOT17-$n.mp4 30 40000000 1; done
# engine cấu hình thật (input 640x640) và cấu hình input chữ nhật 640x384 (xem lưu ý tên engine ở dưới)
eval/.venv/bin/python eval/tools/make_pgie_config.py --batch 1 --out eval/work/pgie_b1.txt
for n in 02 04 09; do eval/tools/in_container.sh engine/build/ds_engine --config /work/eval/work/pgie_b1.txt \
  --tracker-config /work/engine/configs/tracker_nvdcf.yml --camera 1=file:///work/eval/work/mot17/MOT17-$n.mp4 \
  --dump-detections /work/eval/work/mot17/MOT17-$n.nvdcf.csv --fps-interval 0; done
nice -n 19 eval/.venv/bin/python eval/detection/compare_pipelines.py          # --only <tên,tên> để chỉ tính thêm vài pipeline
eval/.venv/bin/python eval/tracking/mot_eval.py --seqs 02 04 09 --tag nvdcf    # MOTA / IDF1 / IDSW
eval/.venv/bin/python eval/tracking/sweep_nvdcf.py maxShadowTrackingAge=60     # đổi từng tham số NvDCF
```
**Bẫy về tên engine TensorRT:** `nvinfer` luôn ghi engine mới build vào `<onnx>_b<N>_gpu0_fp16.engine`, bỏ qua `model-engine-file` bạn đặt.
Muốn có hai kích thước input cho cùng N, phải có hai file ONNX tên khác nhau (`engine/models/yolov8n_h384.onnx` là bản sao của `yolov8n.onnx`),
nếu không lần build sau **đè** engine kia.

## V2-2 — pose

```bash
eval/.venv/bin/python engine/tools/export_yolov8_pose_onnx.py          # yolov8n-pose.pt (tự tải) -> engine/models/yolov8n-pose.onnx, input 640x384
eval/tools/in_container.sh /bin/bash -c 'cd engine && make CUDA_VER=12.6 && make -C parser CUDA_VER=12.6'
make -C engine/parser test                                                 # test parser pose bằng g++ (host, không cần DeepStream)
# độ chính xác keypoint (COCO, OKS): engine TensorRT so với PyTorch/ONNX Runtime, cùng điểm ảnh
eval/.venv/bin/python eval/tools/make_pgie_config.py --base pgie_yolov8n_pose.txt --batch 1 --threshold 0.001 --out eval/work/pgie_pose_b1_t0001.txt
eval/tools/in_container.sh engine/build/ds_engine --config /work/eval/work/pgie_pose_b1_t0001.txt --tracker-config /work/engine/configs/tracker_nvdcf.yml \
  --camera 1=file:///work/eval/work/coco/coco1000.mp4 --dump-detections /work/eval/work/coco/pose_dump.csv --fps-interval 0
nice -n 19 eval/.venv/bin/python eval/pose/coco_kpt_ap.py                  # --only-engine: chỉ tính lại phần engine
# pose làm detector duy nhất so với detection model: chạy engine pose trên MOT17 (tag "pose") rồi
eval/.venv/bin/python eval/detection/compare_pipelines.py --only engine_pose
# FPS / GPU với N camera (trong container; cần `make rtsp-test-server`)
docker run --rm --gpus all -u 1000:1000 -v $PWD:/repo -e HOME=/tmp --entrypoint /usr/bin/python3 nvcr.io/nvidia/deepstream:7.1-triton-multiarch \
  /repo/engine/tools/benchmark.py --video /repo/eval/work/mot17/MOT17-04.mp4 --source rtsp --cameras 1 4 8 --duration 20 --pgie pgie_yolov8n_pose.txt
```
Xem video có khung xương: thêm `--output /work/eval/work/mot17/pose_09.mp4` khi chạy engine (một nguồn).

## V2-4 — đánh giá đầu-cuối (M1, M2)

Quay dữ liệu: [RECORDING.md](RECORDING.md). Nhãn: `eval/labels/*.json` (định dạng ở DEFINITIONS mục 2; `new_label.py` tạo khung, `check_labels.py` kiểm).
```bash
eval/.venv/bin/python eval/e2e/check_labels.py                                   # nhãn hợp lệ? tập test còn thiếu gì?
eval/.venv/bin/python eval/e2e/make_dumps.py --splits train,val                  # engine + pose, một lần mỗi clip -> eval/work/dumps/
eval/.venv/bin/python eval/e2e/run_eval.py --splits val --tag baseline           # luật Python chạy lại trên dump, in M1/M2, ghi eval/results/
eval/.venv/bin/python eval/e2e/run_eval.py --splits val --set fall_still_s=1.5   # thử ngưỡng khác (vài giây, không động tới GPU)
eval/.venv/bin/python eval/e2e/lock_test.py && eval/.venv/bin/python eval/e2e/run_eval.py --splits test --final   # đo đúng một lần
eval/.venv/bin/python eval/e2e/run_eval.py --splits dev --set fall_still_s=1.0   # 8 clip UR Fall: thử máy, không ghi kết quả
```

M4 trên đường thật (cần Docker + GPU + `docker pull postgres:16-alpine`; dựng backend và PostgreSQL tạm riêng, cổng 18090/55433/8556, Telegram tắt):
```bash
eval/tools/in_container.sh /bin/bash -c 'cd engine && make rtsp-test-server'      # camera RTSP giả (có in dấu thời gian gói)
nice -n 10 eval/.venv/bin/python eval/e2e/latency.py --clips fall-01,fall-02,fall-03,fall-04,fall-05 --repeats 3 --set fall_still_s=1.0
```
Ngoài ra kiểm tra `docker ps -a | grep m4-` sau khi chạy (container `m4-latency-pg`, `m4-rtsp-*`, `m4-engine-*` phải tự mất; nếu một lần chạy bị ngắt giữa chừng thì `docker rm -f` chúng).

## V2-5 — ST-GCN: dữ liệu → train → ONNX → TensorRT C++

```bash
# dữ liệu: UR Fall đầy đủ (30 chuỗi ngã + 40 ADL, cam0 RGB). Tải về datasets/urfall/_zips/ (xem datasets/README.md), rồi:
eval/.venv/bin/python eval/train/prepare_urfall.py          # giải nén -> mp4 (NVENC) -> engine pose thật -> eval/work/urfall/<chuỗi>.csv
# thử nghiệm: thang mô hình (MLP -> TCN -> ST-GCN), độ dài cửa sổ, bỏ vận tốc, 3 seed, cross-validation 5 fold theo CHUỖI
nice -n 10 eval/train/run_experiments2.sh                    # ~30 phút trên laptop
eval/.venv/bin/python eval/train/compare.py eval/work/train/cv_p_stgcn_T48.json --thr 0.8,0.9,0.95    # luật vs mô hình, mức sự kiện
# mô hình cuối: huấn luyện trên mọi chuỗi, xuất ONNX (kèm kiểm PyTorch = ONNX Runtime), kiểm TensorRT FP16 + đo thời gian
eval/.venv/bin/python eval/train/train.py --model stgcn --T 48 --final --epochs 30 --out eval/work/train/stgcn_T48.pt
eval/.venv/bin/python eval/train/export_onnx.py eval/work/train/stgcn_T48.pt --out engine/models/stgcn_fall.onnx
eval/tools/in_container.sh /bin/bash -c 'cd engine && make stgcn-check CUDA_VER=12.6'
eval/tools/in_container.sh engine/build/stgcn_check /work/engine/models/stgcn_fall.onnx 48 /work/eval/work/train/ref_windows.f32 /work/eval/work/train/ref_probs.f32 16
# trong engine: --fall-model engine/models/stgcn_fall.onnx và fall_net=2 trong [camera.N]; M3 với mô hình: engine/tools/benchmark.py --fall-model ...
RUN_GPU_TESTS=1 pytest tests/engine_real/test_fall_model.py -v
```

## V2-6 — VLM xác minh

```bash
eval/.venv/bin/pip install "transformers>=4.57" accelerate huggingface_hub        # một lần; mô hình Qwen/Qwen3-VL-2B-Instruct (~4 GB) tự tải lần đầu
# 212 cửa sổ trên UR Fall (cửa sổ chú thích + cảnh báo thật của luật và mạng); kết quả được cache trong eval/work/vlm/, chạy lại gần như miễn phí
nice -n 10 eval/.venv/bin/python eval/vlm/evaluate.py --frames 6 --style options --no-reason --after 2.0 --tag _after2
eval/.venv/bin/python eval/vlm/analyse.py eval/work/vlm/results_options_f6_after2.jsonl      # loại bao nhiêu báo giả, mất bao nhiêu cú ngã thật, theo ngưỡng
# một lần chạy thật cả chuỗi: engine (vòng đệm + snapshot) -> backend -> PostgreSQL tạm (v2-test-pg) -> dịch vụ VLM thật
eval/.venv/bin/python eval/vlm/chain.py fall-09 adl-10 adl-34
# M3 với vòng đệm + mạng ngã (và tải VLM chạy song song: xem ROADMAP_V2_NHAT_KY.md 10.3): engine/tools/benchmark.py --fall-model ... --frame-ring-dir ...
```

## Kiểm tra công cụ

`eval/.venv/bin/python -m pytest tests/test_eval_tools.py` (13 test: IoU, ghép khung, letterbox, decode detection và pose, đọc cột keypoint của dump, và MOTA/IDF1 trên ca có đáp án tay).
