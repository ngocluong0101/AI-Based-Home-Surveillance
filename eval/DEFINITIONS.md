# SafeGuard — định nghĩa hành vi và chỉ số (V2-0)

Tài liệu này **chốt trước khi gán nhãn hay đo**. Đổi định nghĩa sau khi đã có số liệu = các số cũ hết giá trị, nên mọi thay đổi phải ghi
vào mục "Lịch sử thay đổi" ở cuối và đo lại. Viết ngày 2026-11-19; **chưa dựa trên JD Asilla thật** (chưa đọc được, xem ROADMAP_V2 mục 1).

## 1. Hành vi

### 1.1 Té ngã (`FALL`)
Một người đang đứng, đi hoặc ngồi trên ghế **mất thăng bằng và chuyển xuống nằm hoặc ngồi bệt trên sàn một cách không chủ ý**, rồi không
tự đứng lên trong ít nhất **2 giây** sau khi chạm sàn.

- `impact_s` = khung hình đầu tiên mà thân người (hông hoặc vai) chạm sàn. **Đây là mốc đo M4.**
- `start_s` = lúc bắt đầu mất thăng bằng (thường sớm hơn `impact_s` 0,3-1,5 s). `end_s` = lúc người bắt đầu đứng lên, hoặc hết clip.
- **Không phải ngã (phải KHÔNG báo):** ngồi xuống ghế/giường/sofa (kể cả ngồi mạnh), nằm xuống giường/sofa/ghế dài có chủ ý, cúi nhặt đồ, quỳ,
  ngồi xổm, làm động tác thể dục/yoga, bò chơi với trẻ, người đứng yên.
- **Ca khó phải có trong tập đánh giá** (vì chúng là nguồn báo giả): ngồi sụp xuống ghế, nằm xuống sofa, cúi nhặt đồ sát sàn, người bị che một phần bởi bàn ghế,
  ngã chỉ thấy một nửa người, ngã xong tự đứng dậy ngay (< 2 s: **không tính là sự kiện ngã**, nhãn `ambiguous`, bỏ khỏi cả recall lẫn báo giả).
- **Vùng được phép nằm** (giường, sofa) là thuộc tính của camera; người nằm trong vùng đó không bao giờ là ngã (xem V2-3).

### 1.2 Xô xát (`FIGHT`)
**Hai người trở lên** có tiếp xúc cơ thể hung hăng (đấm, đá, tát, xô đẩy mạnh, túm giằng) kéo dài ít nhất **2 giây** liên tục.

- `start_s` = lúc có động tác hung hăng đầu tiên; `end_s` = lúc hai bên tách ra hoặc ngừng. Không có `impact_s`.
- **Không phải xô xát:** ôm, bắt tay, vỗ vai, đẩy nhẹ đùa giỡn (một lần, không lặp), chơi thể thao nhẹ, múa/nhảy chung, vẫy tay, đuổi bắt trẻ con.
- Ca khó phải có: ôm chặt, đùa đẩy nhau, vật tay, hai người tranh nhau một đồ vật nhẹ nhàng.
- Đóng kịch bằng người đồng ý, **không tiếp xúc thật** (kỹ thuật "gần chạm"), có đệm, không ai bị đau. Không quay người chưa đồng ý.

### 1.3 Xâm nhập vùng cấm (`RESTRICTED_AREA`)
Một người có **chân/hông nằm trong đa giác vùng cấm** của camera liên tục ≥ **3 giây** (tham số theo camera). Đây là luật hình học, không cần nhãn hành vi riêng:
đúng/sai quyết định bởi đa giác và video; kiểm bằng vài clip tự quay. Không nằm trong bảng M1-M4 (M1-M4 nói về ngã/xô xát).

## 2. Định dạng nhãn

Một file JSON cho mỗi clip, `eval/labels/<tên clip>.json`:

```json
{
  "clip": "hallway_03.mp4", "fps": 30, "width": 1920, "height": 1080,
  "scene": "hallway", "split": "test", "has_incident": true,
  "lie_zones": [[[x,y],[x,y],[x,y],[x,y]]],
  "events": [
    {"type": "fall", "start_s": 12.3, "impact_s": 12.9, "end_s": 15.0, "label": "positive"},
    {"type": "fall", "start_s": 40.0, "impact_s": 40.4, "end_s": 40.9, "label": "ambiguous"}
  ]
}
```
- `has_incident=false` ⇒ cả clip là "video không sự cố" dùng cho M2.
- Thời gian tính bằng **giây trên timeline của clip** (PTS), không phải số khung (để không phụ thuộc fps).
- `split` ∈ `train | val | test`, chia **theo người và theo cảnh** (một người/một phòng không xuất hiện ở hai split). Tập `test` khóa sau khi chốt, không dùng để chỉnh ngưỡng.
- Dữ liệu tracking (V2-1) dùng nhãn MOT chuẩn (`gt.txt`), không dùng định dạng này.

## 3. Khớp event với nhãn

Một **event dự đoán** có loại `T` và thời điểm `t_pred` (giây trên timeline clip; với ngã là thời điểm hành vi theo hệ thống, nếu hệ thống chỉ có thời điểm phát
event thì dùng thời điểm đó và ghi rõ).

- Event `T` **khớp** nhãn `positive` loại `T` nếu `start_s − 1,0 ≤ t_pred ≤ end_s + 10,0`. (Cửa sổ rộng ở phía sau vì báo trễ vẫn là phát hiện được; độ trễ đo riêng ở M4.)
- Mỗi nhãn nhận **tối đa một** TP (event khớp đầu tiên); các event khớp thêm cùng nhãn là **trùng lặp**, đếm riêng, không phải báo giả.
- Event không khớp nhãn `positive` nào và không rơi vào cửa sổ của nhãn `ambiguous` là **báo giả (FP)**.
- Event rơi vào cửa sổ nhãn `ambiguous`: bỏ qua (không TP, không FP).
- Một nhãn không có event nào khớp = **bỏ sót (FN)**.

## 4. Bốn chỉ số

| # | Chỉ số | Công thức | Điều kiện đo bắt buộc ghi kèm |
|---|---|---|---|
| **M1** | Recall té ngã theo sự kiện | `TP_fall / (TP_fall + FN_fall)` trên nhãn `positive` loại `fall` của tập `test` | số cú ngã, số người, số cảnh, khoảng tin cậy Wilson 95% (tập nhỏ nên khoảng sẽ rộng — nêu thẳng) |
| **M2** | Báo giả / camera / giờ | `FP / (số camera × số giờ video)` trên video `has_incident=false`, tính **riêng** ở hai tầng: (a) sau ST-GCN + logic sự kiện, (b) sau VLM | tổng số giờ, số camera, mô tả cảnh; **chỉ video thật, nhiều giờ khác nhau — không tính video lặp vòng**; số FP thô cạnh số tỉ lệ |
| **M3** | FPS mỗi luồng khi có N camera | trung vị và phân vị 5% của FPS từng camera trong cửa sổ 60 s **sau 30 s khởi động**; bảng N = 1, 4, 8, 12 | GPU/driver, độ phân giải/codec/fps nguồn (mặc định 1080p H.264 30 fps), có/không pose, ST-GCN, VLM, stream; luồng "đạt thời gian thực" khi phân vị 5% ≥ 29 fps với nguồn 30 fps |
| **M4** | Độ trễ từ lúc ngã đến cảnh báo | `t_alert_received − t_impact` (cùng đồng hồ máy, clip phát thời gian thực qua RTSP giả, `t_impact` = `impact_s` + thời điểm khung đầu tới) | trung vị, p95, tối đa; **chia chặng** (pose → cửa sổ → debounce → VLM → backend → WebSocket/Telegram); "cảnh báo" = lúc ra ở WebSocket (và riêng lúc Telegram nhận) |

Chỉ số phụ luôn báo cùng M1/M2: precision và F1 theo event, số trùng lặp, ma trận nhầm lẫn theo loại ca khó, recall khi ràng buộc thêm độ trễ ≤ 5 s và ≤ 10 s.
Với xô xát: cùng công thức M1 (recall) và M2, nhưng nhãn ít hơn và thiếu dữ liệu công khai, nên mọi số xô xát phải ghi rõ là trên dữ liệu tự quay quy mô nhỏ.

**Mục tiêu đề xuất (chưa đo gì, sẽ sửa sau baseline V2-4):** M1 ≥ 90%; M2 sau VLM ≤ 0,5 và ≥ 3 lần thấp hơn trước VLM; M3 đạt thời gian thực ở N=8; M4 ≤ 10 s.

## 5. Chỉ số của V2-1 (nền, không phải M1-M4)

- **Detection AP:** COCO AP/AR của lớp `person` (pycocotools, `iouType=bbox`, `catIds=[1]`, AP@[.5:.95] và AP@.5, ảnh `val2017` có người). So PyTorch FP32, ONNX Runtime FP32 và engine TensorRT FP16 trong DeepStream.
  Ngưỡng tin cậy thấp (0,001) khi tính AP; số "ở điểm vận hành" tính ở 0,25 (ngưỡng của engine).
- **Tracking:** MOTA, IDF1, số ID switch (IDSW), số mảnh quỹ đạo (FM), MT/ML, theo giao thức MOT: chỉ lớp người đi bộ, IoU ≥ 0,5,
  bỏ các khung nhãn "distractor" (người trên xe, người tĩnh/phản chiếu) và các kết quả khớp với chúng, bỏ nhãn `visibility` rất thấp theo `conf` của gt.
  Dùng `py-motmetrics` 1.4.0 (đã kiểm với trường hợp có đáp án tay, xem `eval/common/`).
- **Độ đồng thuận giữa hai pipeline** (để giải thích lệch ~10%): ghép từng khung theo IoU ≥ 0,5 (tham lam theo độ tin cậy), báo `đúng chung / chỉ A / chỉ B` và
  phân bố độ tin cậy của phần chỉ có ở một bên.

## 6. Nơi đặt file

```
eval/
  DEFINITIONS.md        (file này)
  README.md             cách chạy từng bộ đánh giá
  common/               mã dùng chung (đọc dump của engine, IoU, ghép khung, motmetrics)
  detection/            V2-1: AP, so sánh pipeline
  tracking/             V2-1: MOTA/IDF1
  labels/               V2-4: nhãn hành vi (JSON mục 2)
  results/              số liệu đã chốt (JSON nhỏ, được commit): <ngày>_<chủ đề>.json
  work/                 file trung gian (khung ảnh, dump, video chuyển đổi) — không commit
datasets/               dữ liệu (không commit; xem datasets/README.md)
```

## Lịch sử thay đổi
- 2026-11-19: bản đầu (V2-0).
