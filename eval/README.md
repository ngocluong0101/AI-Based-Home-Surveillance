# eval/ — đo SafeGuard (ROADMAP_V2)

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

## Kiểm tra công cụ

`eval/.venv/bin/python -m pytest tests/test_eval_tools.py` (10 test: IoU, ghép khung, letterbox, decode, và MOTA/IDF1 trên ca có đáp án tay).
