# Ôn phỏng vấn — AI-Based-Home-Surveillance (bản DeepStream / C)

> File này **không lên GitHub** (`interview/` đã nằm trong `.gitignore`).
> `README.md` cùng thư mục nói về **bản Python cũ** (YOLO + ByteTrack + Tkinter) đã bị xóa khỏi repo. Chỉ dùng lại phần YOLO/tracking cơ bản và live coding (IoU, NMS) của nó. **Khi nói về dự án hiện tại, trả lời theo file này.**
> Cách học: nhìn câu hỏi, tự nói thành tiếng 1-2 phút, rồi mới đọc phần *Gợi ý*. Đường dẫn trong ngoặc là chỗ để mở code khi bị hỏi "chỉ cho anh/chị đoạn code đó".

## Ba nguyên tắc trả lời (quan trọng hơn thuộc lòng)

1. **Tách ba loại câu:** "em đã làm", "em đã đo (có số)", "em chưa làm / chưa đo". Nói trung thực loại thứ ba, không bị bắt bẻ.
2. **Mỗi quyết định kết bằng đánh đổi:** "em chọn A vì ..., cái giá phải trả là ...".
3. **Kể chuyện thất bại:** phần "đã thử và không dùng được" (mục 17) là thứ nhà tuyển dụng nhớ lâu nhất.

## Mục lục

1. Mở đầu: giới thiệu 30 giây và 2 phút
2. Tổng quan & kiến trúc
3. DeepStream & GStreamer
4. Model: YOLOv8, ONNX, TensorRT
5. Tracking
6. Sự kiện & thời gian
7. Giao sự kiện đáng tin cậy
8. RTSP, reconnect, thêm/bớt camera
9. Phát video lên web
10. Backend, database, realtime, Telegram
11. Frontend & bản demo
12. Docker, bảo mật, đăng nhập
13. Hiệu năng, benchmark, soak test
14. Testing
15. Điểm yếu & câu hỏi gài
16. Kiến thức nền Video / Edge AI
17. Đã thử và không dùng được
18. Hành vi (STAR)
19. Live coding
20. Checklist

---

## 1. Mở đầu

**Q1. Giới thiệu dự án trong 30 giây.**
*Gợi ý:* "Em làm một hệ thống giám sát nhiều camera chạy trên NVIDIA DeepStream. Engine viết bằng C đọc N luồng RTSP hoặc file MP4 trong **một** pipeline batch trên GPU, phát hiện và tracking người bằng YOLOv8n, đổi thành sự kiện rồi gửi cho backend FastAPI lưu vào PostgreSQL và báo qua Telegram. Có web React xem video trực tiếp kèm khung phát hiện. Em đo được khoảng 20 camera 1080p giả lập 30 fps trên một RTX 4060 Laptop."

**Q2. Giới thiệu trong 2 phút (kể theo trình tự này).**
*Gợi ý:*
1. **Bài toán:** nhà có vài camera, muốn biết khi nào có người, không muốn mở từng camera xem.
2. **Cách làm:** một pipeline GPU cho tất cả camera, vì chạy mỗi camera một process tốn RAM và CPU gấp nhiều lần (em đo: 8 camera, 1.2 GiB so với 5.1 GiB RAM).
3. **Chỗ khó nhất:** biến detection từng frame thành *sự kiện* không trùng, không mất, kể cả khi mạng hoặc backend chết: ID tracking, timeout vắng mặt, spool ra đĩa, `event_id` idempotent.
4. **Phần còn lại:** backend, web realtime, Docker Compose, test tự động.
5. **Hạn chế thật:** chưa thử camera thật, chưa đo độ chính xác tracking, soak 4 giờ còn một cảnh báo RAM. (Nói trước, đừng để bị hỏi.)

---

## 2. Tổng quan & kiến trúc

**Q3. Luồng dữ liệu từ frame đến thông báo Telegram?**
*Gợi ý:* nguồn (`nvurisrcbin`, giải mã bằng NVDEC) → `nvstreammux` gom batch → `nvinfer` (YOLOv8n TensorRT FP16 + parser tự viết) → `nvtracker` (NvDCF) → **probe** trên src pad của tracker copy người có track ID ra struct `Detection` → `EventManager` của từng camera quyết định có phải sự kiện mới → dựng `EventRecord` (JSON, UUID) → hàng đợi → thread `publisher` POST HTTP (libcurl) → backend FastAPI `INSERT ... ON CONFLICT DO NOTHING` vào PostgreSQL → hai nhánh: WebSocket `event.created` cho web, và thread `Notifier` gửi Telegram. Song song có nhánh video: `nvstreamdemux` → vẽ OSD → NVENC H.264 → RTMP → MediaMTX → WebRTC cho trình duyệt.

**Q4. Vì sao chia thành engine C, backend Python và Engine Manager?**
*Gợi ý:* Mỗi phần có yêu cầu khác nhau. Engine chạy trên đường nóng của frame nên cần C và kiểm soát bộ nhớ. Backend là CRUD, WebSocket, tích hợp Telegram nên FastAPI viết nhanh và dễ test. Manager chỉ giám sát tiến trình (khởi động, restart, ghi file cấu hình camera), không chạm DeepStream. Python không bao giờ nằm giữa đường xử lý frame. Đánh đổi: nhiều thành phần, phải thống nhất giao thức giữa chúng (HTTP + JSON, heartbeat).

**Q5. Vì sao viết engine bằng C mà không dùng Python (`pyds`)?**
*Gợi ý:* Probe chạy cho mọi batch (đường nóng), C truy cập thẳng `NvDs*Meta`, không có GIL, kiểm soát vòng đời bộ nhớ, test được bằng ASan/UBSan. Cái giá: viết dài hơn, dễ lỗi con trỏ (em từng bị segfault, xem mục 17). **Trung thực:** em chưa đo so với `pyds` nên không nói "nhanh hơn X lần". Lý do chính là kiểm soát và học sâu, không phải một con số.

**Q6. Vì sao một process cho N camera, không phải một process mỗi camera?**
*Gợi ý:* Em đo cả hai với 8 camera 30 fps:
- 1 process: GPU 33%, VRAM 1.6 GiB, RAM 1.2 GiB, CPU 34% một nhân.
- 8 process: GPU 41%, VRAM 2.8 GiB, RAM 5.1 GiB, CPU 95%.
Mỗi process phải nạp riêng CUDA context, TensorRT engine, thư viện (~630 MiB mỗi process). Batch cũng làm GPU hiệu quả hơn. Đánh đổi: một lỗi nặng làm sập cả pipeline (nên có Manager restart), và đổi danh sách camera phải đụng engine chung (em giải bằng hot reload, Q49-Q52).

**Q7. Chọn PostgreSQL thay vì SQLite?**
*Gợi ý:* Nói thật: với một nhà riêng SQLite đủ dùng. Em chọn PostgreSQL vì nhiều thread ghi đồng thời (nhận event, notifier, janitor), có `ON CONFLICT`, JSONB cho settings, và nó giống môi trường thật hơn khi chạy Docker Compose. Đánh đổi: thêm một service phải vận hành, phải backup (em có service `backup` dùng `pg_dump`).

**Q8. Hệ thống mở rộng ra nhiều máy được không?**
*Gợi ý:* Chưa hoàn toàn. Snapshot hiện ghi vào thư mục dùng chung giữa engine và backend trên **cùng một máy**, JSON chỉ chứa tên file. Muốn tách máy thì phải gửi ảnh qua HTTP hoặc dùng object storage (MinIO/S3). Phần còn lại (event qua HTTP, heartbeat) đã tách máy được. Đây là hạn chế em biết và nói được cách sửa.

---

## 3. DeepStream & GStreamer

**Q9. `nvstreammux` làm gì?**
*Gợi ý:* Gom frame của N nguồn thành một batch để `nvinfer` xử lý cùng lúc. Tham số quan trọng: `batch-size` (= số camera hoặc số "ô"), `batched-push-timeout` (chờ tối đa bao lâu trước khi đẩy batch chưa đầy), `live-source`, `buffer-pool-size`. Em từng thấy: timeout 40 ms làm khi một camera mất thì camera còn lại tụt 30 → 25 fps; giảm xuống 20 ms thì hết.

**Q10. Pad probe là gì và em gắn ở đâu?**
*Gợi ý:* Hàm callback gắn lên một pad, chạy mỗi khi buffer đi qua. Em gắn ở **src pad của `nvtracker`**: lúc này batch còn nguyên (chưa tách theo camera), object đã có track ID, và `frame_meta->source_id` cho biết camera nào. Gắn sau tiler/demux thì mất cấu trúc batch.

**Q11. Vì sao probe không được block (gọi mạng, ghi đĩa)?**
*Gợi ý:* Probe chạy trên streaming thread của GStreamer. Chặn nó thì buffer không đi tiếp, pipeline đứng. Em từng gặp hậu quả dây chuyền: nhánh phát video kẹt làm cạn `buffer-pool-size` của `nvstreammux`, mọi camera ngừng detection ~6 giây. Quy tắc: probe chỉ `copy` giá trị từ meta ra struct riêng rồi đẩy vào hàng đợi; thread khác lo mạng và đĩa.

**Q12. Cấu trúc metadata của DeepStream?**
*Gợi ý:* `NvDsBatchMeta` → `frame_meta_list` (mỗi frame một `NvDsFrameMeta`, có `source_id`, `buf_pts`) → `obj_meta_list` (mỗi object có `rect_params`, `confidence`, `object_id` = track ID, `class_id`). Ngoài ra có `user_meta` để gắn dữ liệu thêm (em dùng cho JPEG snapshot).

**Q13. Em cấu hình `nvinfer` thế nào? (mở `engine/configs/pgie_yolov8n.txt`)**
*Gợi ý:* Đầu vào RGB, `net-scale-factor` = 1/255, letterbox 640×640 giống Ultralytics (`maintain-aspect-ratio=1`, `symmetric-padding=1`); `network-mode=2` (FP16); `num-detected-classes=1` (chỉ person); `cluster-mode=2` (NMS) vì YOLOv8 không có NMS trong model; `pre-cluster-threshold=0.25`, `nms-iou-threshold=0.45`, `topk=300`; parser tự viết `NvDsInferParseYoloV8` trong `libnvds_yolov8_parser.so`. `batch-size` bị engine ghi đè bằng số ô.

**Q14. Vì sao phải tự viết parser cho YOLOv8?**
*Gợi ý:* `nvinfer` không biết định dạng output của YOLOv8. Output là tensor dạng `[1, 84, 8400]`: 4 giá trị box + 80 điểm class cho 8400 điểm trên lưới, và **không có objectness** (anchor-free). Parser (C++, `engine/parser/`) duyệt 8400 điểm, lấy điểm lớp person, lọc theo ngưỡng, đổi tâm/rộng/cao sang góc rồi trả danh sách box cho `nvinfer` chạy NMS. (Kích thước 84×8400 là của model COCO 80 lớp ở 640×640; mở `engine/parser/` kiểm lại trước khi nói con số cụ thể.)

**Q15. `nvurisrcbin` khác `uridecodebin` ở đâu và vì sao dùng?**
*Gợi ý:* `nvurisrcbin` là bin của DeepStream: giải mã bằng NVDEC, xuất thẳng bộ nhớ NVMM (trên GPU) và có sẵn reconnect cho RTSP (`rtsp-reconnect-interval`, `rtsp-reconnect-attempts=-1`). Nhờ đó em không phải tự viết logic nối lại, chỉ cần ghi nhận trạng thái.

**Q16. NVMM là gì?**
*Gợi ý:* Bộ nhớ buffer NVIDIA dùng chung giữa decoder, mux, inference, encoder, giữ frame trên GPU để khỏi copy về CPU. Probe chỉ đọc metadata, không đụng pixel. Với snapshot thì dùng `nvds_obj_enc` để mã hóa JPEG ngay trên GPU.

**Q17. Pipeline có `--output` khác pipeline bình thường thế nào?**
*Gợi ý:* Bình thường kết thúc ở `fakesink` (chỉ cần metadata). Với `--output FILE` thì thêm tiler → OSD → H.264 → MP4 để debug bằng mắt (khung + `person N`). Đây là công cụ kiểm tra chứ không phải đường chạy thật.

**Q18. Em debug pipeline GStreamer thế nào?**
*Gợi ý:* `GST_DEBUG=<element>:5` để xem log từng element, `gst-launch-1.0` dựng pipeline nhỏ thử riêng, `gst-inspect-1.0` xem property/pad, `nvidia-smi` xem NVDEC/NVENC, `gdb` khi treo hoặc segfault (em đã dùng để tìm deadlock trong `rtspclientsink` và lỗi `fwrite` trong `jpeg_write_to_file`), `compute-sanitizer` cho lỗi CUDA.

---

## 4. Model: YOLOv8, ONNX, TensorRT

**Q19. Quy trình từ `yolov8n.pt` đến engine chạy được?**
*Gợi ý:* `engine/tools/export_yolov8_onnx.py` xuất ONNX (batch động) → `nvinfer` lần đầu tự build engine TensorRT FP16 và lưu `engine/models/yolov8n.onnx_b<N>_gpu0_fp16.engine` → các lần sau nạp engine trong ~1 giây. Build mất khoảng 150 giây.

**Q20. Vì sao engine phải build riêng cho từng số camera N?**
*Gợi ý:* Em cố định `batch-size` bằng số ô nên TensorRT tối ưu cho đúng batch đó, và tên engine có `_b<N>`. Engine Manager sinh config theo N để chọn đúng engine. Cái giá: lần đầu với một N mới tốn ~150 giây không có hình (manager cảnh báo trong log). Cũng cần nhớ: `nvinfer` **không kiểm engine cũ so với ONNX**, nên xuất lại ONNX phải xóa engine cũ.

**Q21. Vì sao FP16 mà không INT8?**
*Gợi ý:* FP16 gần như không mất độ chính xác, không cần dữ liệu hiệu chuẩn, giảm nửa bộ nhớ và nhanh hơn rõ trên Tensor Core. INT8 nhanh hơn nữa nhưng cần calibration dataset và phải đo lại mAP. Em chưa làm INT8 vì nút cổ chai thật là **NVDEC (bộ giải mã)** chứ không phải inference (Q82); tối ưu inference thêm không đổi trần camera.

**Q22. Engine TensorRT có chuyển sang máy khác được không?**
*Gợi ý:* Không an toàn. Engine gắn với kiến trúc GPU, phiên bản TensorRT và CUDA, nên mỗi máy (kể cả Jetson) phải build lại từ ONNX. ONNX mới là thứ mang đi.

**Q23. Độ chính xác so với Ultralytics thế nào?**
*Gợi ý:* Em so ở ngưỡng 0.25: số người DeepStream chênh khoảng 10% so với Ultralytics, chủ yếu ở các box sát ngưỡng. Em **chưa có ground truth** nên chưa nói được cái nào đúng hơn. Nếu được hỏi cách làm tiếp: gán nhãn một tập video, đo precision/recall/mAP và chọn ngưỡng bằng đường PR thay vì cảm tính.

**Q24. Ngưỡng confidence ảnh hưởng gì?**
*Gợi ý:* Cao → ít box rác nhưng dễ bỏ sót (recall giảm); thấp → nhiều box rác kéo theo track rác và event giả. Mặc định 0.25 (`pre-cluster-threshold`). Em còn làm ngưỡng **theo từng camera** bằng probe đặt trước tracker: xóa object có `confidence` thấp hơn ngưỡng riêng của camera đó, nên tracker, sự kiện, số người và khung vẽ đều cùng thấy tập đã lọc.

**Q25. NMS là gì và ai chạy nó ở đây?**
*Gợi ý:* Giữ box điểm cao nhất trong nhóm box chồng nhau (IoU lớn hơn ngưỡng), bỏ phần còn lại. Ở đây `nvinfer` chạy (`cluster-mode=2`, IoU 0.45) vì model YOLOv8 xuất ra box thô chưa qua NMS.

---

## 5. Tracking

**Q26. Vì sao cần tracking?**
*Gợi ý:* Detection chỉ trả box từng frame, không biết "người này có phải người ở frame trước". Tracking gán ID ổn định theo thời gian. Có ID mới biết "người mới xuất hiện" để phát một sự kiện, thay vì phát mỗi frame.

**Q27. Vì sao chọn NvDCF?**
*Gợi ý:* Chủ repo chọn NvDCF vì có tracker thị giác (correlation filter) giữ ID tốt hơn tracker chỉ dùng IoU khi detector chớp tắt. Trên video mẫu `sample_720p.mp4`: NvDCF ra 21 track (3 ngắn), ByteTrack 36 track (9 ngắn), tức ít track rác hơn. Nhưng **chưa có ground truth nên chưa đo được ID switch**. Đánh đổi: NvDCF nặng hơn tracker IoU thuần.

**Q28. Các tham số tracker em đã chỉnh? (`tracker_nvdcf.yml`)**
*Gợi ý:* Chủ yếu: `probationAge: 3` (target phải sống 3 frame mới được báo ID, lọc nhiễu), `maxShadowTrackingAge: 30` (giữ target 1 giây ở 30 fps khi mất detection), `minIouDiff4NewTarget: 0.5`, `useUniqueID: 0` (ID đếm riêng từng stream). Em nói rõ đó là **giá trị khởi đầu cho người đi trong phòng, chưa tune** trên cảnh thật (file config cũng ghi vậy).

**Q29. Vì sao khóa là `(source_id, track_id)`?**
*Gợi ý:* `track_id` chỉ duy nhất trong từng nguồn (`useUniqueID: 0`), hai camera có thể cùng có ID 5. Nên trạng thái (EventManager, FPS, state) nằm trong mảng theo `source_id`, `camera_id` chỉ tra ra khi dựng event.

**Q30. ID switch ảnh hưởng gì và đo thế nào?**
*Gợi ý:* Một người bị gán ID mới → bị coi là người mới → **sự kiện trùng**. Giảm bằng `probationAge`, giữ target lâu hơn, hoặc re-ID bằng embedding. Đo bằng MOTA/IDF1/HOTA, cần video có ground truth ID. Em chưa làm bước này và biết đó là việc nên làm tiếp.

---

## 6. Sự kiện & thời gian

**Q31. Logic chống trùng sự kiện hoạt động thế nào?**
*Gợi ý:* `EventManager` mỗi camera lưu `last_seen` theo `track_id`. Phát `PERSON_DETECTED` khi track xuất hiện lần đầu, hoặc đã vắng **lâu hơn** `absence_timeout` (mặc định 10 giây) rồi quay lại. Mỗi lần gọi dọn track vắng quá timeout để bộ nhớ không phình. Người đứng yên 1 giờ vẫn chỉ một sự kiện.

**Q32. Vì sao port từ Python sang C, và đảm bảo hai bản giống nhau thế nào?**
*Gợi ý:* Engine C không import được Python, nên em giữ bản Python (`src/events/person_detected.py`) làm **đặc tả chuẩn** và sinh 16 test vector JSON bằng chính nó (mỗi bước: thời điểm, detections, event mong đợi, trạng thái sau). Test pytest và `make test-core` (C, chạy dưới ASan/UBSan) chạy **cùng** các vector. Hai bản cùng đúng với một bộ dữ liệu thì hành vi khớp nhau.

**Q33. Vì sao thời gian tính bằng `stream_start + PTS` mà không dùng giờ tường?**
*Gợi ý:* Với file video, xử lý nhanh hơn thời gian thật (em đạt ~700 fps tổng), nên giờ tường làm `absence_timeout` sai. PTS là thời gian của video. `stream_start_ns` = giờ tường lúc thấy frame đầu trừ PTS của nó. Với RTSP, khi reconnect PTS có thể về 0 nên có cờ `reanchor` tính lại.

**Q34. Vì sao `now` là tham số của `process()`?**
*Gợi ý:* Để test không cần `sleep` và để đổi nguồn thời gian (PTS) mà không đổi logic. Đó chính là thứ cho phép dùng cùng vector test cho Python và C.

**Q35. "Số người hiện tại" (`people`) được định nghĩa thế nào? Có bẫy gì?**
*Gợi ý:* Là số người **đã có track ID** trong frame gần nhất (cùng định nghĩa với sinh event; người chưa qua `probationAge` thì chưa đếm). Chỉ báo khi frame cuối cách chưa quá 2 giây, còn lại là "không rõ" (`null`), **không phải 0**, vì 0 sẽ khẳng định sai là phòng trống khi mất camera. Là số tức thời, chưa làm mượt nên có thể nhảy ±1.

**Q36. Nếu pipeline restart thì EventManager thế nào?**
*Gợi ý:* Tạo lại, mất trạng thái `last_seen`, nên người đang đứng đó sẽ bị báo như mới. Có `pipeline_run_id` (UUID mỗi lần chạy) để phân biệt; dùng nó để xử lý trường hợp này là việc dọn nhỏ còn lại.

---

## 7. Giao sự kiện đáng tin cậy

**Q37. Từ probe đến backend, sự kiện đi qua những bước nào?**
*Gợi ý:* Probe dựng `EventRecord` (UUID `event_id`) và đẩy vào `GAsyncQueue` giới hạn 1024 → thread `publisher` lấy ra, POST bằng libcurl → thành công thì xong, thất bại thì ghi vào spool. Probe không bao giờ chờ mạng.

**Q38. Hàng đợi đầy thì sao?**
*Gợi ý:* Bỏ và đếm `events_dropped` (giới hạn 1024 item và 64 MB JPEG). Với snapshot, bỏ **cả nhóm** (các event cùng frame dùng chung một JPEG) để không có event nào trỏ tới file chưa được xếp hàng. Đây là chính sách có chủ đích: thà bỏ sự kiện mới còn hơn làm chậm pipeline hoặc tăng RAM vô hạn.

**Q39. Em phân loại mã HTTP trả về thế nào?**
*Gợi ý:*
- **2xx và 409** = đã giao xong (409 là bản trùng).
- **400/413/422** = backend từ chối hẳn, đếm `rejected`, không thử lại.
- **Mọi thứ khác** (lỗi mạng, 5xx, 408/429, cả 401/403/404 do cấu hình sai) = thử lại sau.
Lý do thử lại cả 401/404: cấu hình sai thường sửa được, và mất sự kiện thì không lấy lại được. Nên backend trả 422 cho event không bao giờ lưu được (camera không tồn tại), để engine không thử lại vô hạn.

**Q40. Idempotency được đảm bảo thế nào?**
*Gợi ý:* `event_id` là UUID do engine sinh, là khóa chính. Backend dùng `INSERT ... ON CONFLICT DO NOTHING`: 201 nếu mới, 200 nếu trùng. Nhờ vậy engine gửi lại thoải mái (sau crash, sau lỗi mạng khi không biết backend đã nhận chưa) mà không tạo bản trùng. Đây là mẫu "at-least-once delivery + idempotent receiver" cho ra hiệu quả giống exactly-once.

**Q41. Spool hoạt động thế nào?**
*Gợi ý:* File JSON-lines, `fsync` mỗi lần ghi, vị trí đọc giữ trong RAM, cắt file về 0 khi giao hết, tối đa 256 MB, tự sửa dòng cuối bị cụt sau crash. Khi backend chết, mọi event đi thẳng vào spool nên worker không kẹt và hàng đợi không đầy; có spool thì event mới nối đuôi để giữ thứ tự. Spool còn lại từ lần chạy trước được giao ở lần sau.

**Q42. Backoff thế nào và vì sao?**
*Gợi ý:* 1 giây, nhân đôi tới 60 giây. Tránh dồn dập khi backend đang chậm hoặc đang khởi động lại. Trong lúc chờ, event vào spool chứ không chặn.

**Q43. Heartbeat khác event ở điểm nào?**
*Gợi ý:* Heartbeat (mặc định mỗi 5 giây) báo trạng thái từng camera: `RUNNING/RECONNECTING/ENDED`, fps, `people`. Nó **không vào spool, không thử lại, không đụng backoff của event** vì nó chỉ có giá trị khi còn mới, và backend có thể nhận heartbeat được trong khi DB từ chối event. Nếu cái trước chưa gửi xong thì bỏ tick sau (không dồn đống).

**Q44. Vì sao snapshot lưu bằng `attachUsrMeta` mà không `saveImg=TRUE`?**
*Gợi ý:* `saveImg=TRUE` gọi `jpeg_write_to_file` trong thư viện của NVIDIA, hàm đó **không kiểm `fopen`**, nên thư mục không ghi được làm cả engine segfault (em tìm ra bằng gdb: `fwrite` ← `jpeg_write_to_file`). Em dùng `attachUsrMeta=TRUE`: JPEG nằm trong `frame_user_meta_list`, probe chỉ `memcpy` sang hàng đợi, thread `publisher` ghi file bằng `g_file_set_contents` (file tạm + rename). Ghi lỗi thì log và event ra với `snapshot:null`.

**Q45. Tắt chương trình thế nào để không mất dữ liệu?**
*Gợi ý:* SIGINT/SIGTERM thoát vòng lặp gọn, in thống kê, `publisher` đổ spool, tối đa thêm 10 giây ân hạn; phần chưa giao nằm lại spool cho lần sau. Có một bug đã gặp: `g_async_queue_push` từ chối `NULL`, nên dùng marker riêng để báo dừng; trước đó `g_thread_join` treo mãi.

---

## 8. RTSP, reconnect, thêm/bớt camera

**Q46. Mất kết nối RTSP thì sao?**
*Gợi ý:* `nvurisrcbin` tự nối lại (chu kỳ cố định `--rtsp-timeout`, mặc định 10 giây, vô hạn lần). Engine chỉ ghi nhận: lỗi bus của nguồn RTSP không dừng pipeline; timer `watchdog_tick` mỗi giây đánh dấu `RECONNECTING` khi quá timeout không có frame, `RUNNING` lại khi có frame. Camera còn lại giữ 30 fps (em thử tắt server của camera 1).

**Q47. Em phát hiện camera "treo" (không đóng kết nối) thế nào?**
*Gợi ý:* Không dựa vào lỗi kết nối mà dựa vào **không có frame mới** quá timeout. Em thử `SIGSTOP` vào server RTSP giả: phát hiện sau ~5 giây, và về lại sau `SIGCONT`.

**Q48. Trạng thái `ENDED` để làm gì?**
*Gợi ý:* Nguồn **file** hết (EOS) trước đây vẫn báo `RUNNING` với `fps=0`, gây hiểu nhầm. Thêm `ENDED` bằng pad probe bắt sự kiện EOS trên sink pad của `nvstreammux` (chỉ cho nguồn không phải RTSP vì `nvurisrcbin` nuốt EOS của RTSP để reconnect).

**Q49. Đổi cài đặt camera (ngưỡng, timeout, bật/tắt AI) mà không restart: làm thế nào?**
*Gợi ý:* Manager ghi lại `cameras.ini` rồi gửi **SIGHUP** cho engine. Handler đọc lại file, kiểm hợp lệ rồi áp dụng; file hỏng thì không đổi gì. Các giá trị của `SourceCtx` là `_Atomic` (probe đọc, main thread ghi); `EventManager` có hàm đổi timeout, chỉ thread probe đụng vào nó nên không có race. Em kiểm bằng engine thật: sau SIGHUP, camera 1 (`absence_timeout=0.2`) tăng số event (70 → 132), camera 2 (`conf_threshold=0.9`) gần như hết event (3), camera 3 (`ai_enabled=false`) bằng 0.

**Q50. Thêm/bớt camera khi engine đang chạy: làm thế nào?**
*Gợi ý:* Engine có `--max-cameras N` = số **ô** (slot) cố định. Batch và engine TensorRT tính theo số ô. Camera mới vào ô trống bằng cách thêm `nvurisrcbin` + pad `nvstreammux` vào pipeline đang chạy; camera bị gỡ thì `set_state(NULL)`, `flush_stop`, release pad. Thêm vượt số ô thì Manager restart với sức chứa lớn hơn. Cái giá của ô trống (đo): batch 12 so với batch 4 khi chạy 4 camera: GPU 18% → 22-25%, VRAM +260 MiB.

**Q51. Vì sao thao tác "tất cả hoặc không gì" khi reload?**
*Gợi ý:* Nếu áp dụng một nửa (ví dụ thêm 2 camera nhưng chỉ đủ chỗ cho 1) thì trạng thái thực tế khác danh sách được yêu cầu và không ai biết. Engine từ chối cả lô và in `reload: nothing applied`; Manager đọc dòng đó rồi restart với danh sách đúng. Nguyên tắc: cái đang chạy không bao giờ được khác cái được yêu cầu.

**Q52. Điều gì khiến việc thêm camera động khó?**
*Gợi ý:* `nvstreamdemux` chỉ cho xin pad `src_N` **lúc dựng pipeline**; xin lúc đang chạy trả NULL. Nên mọi ô xin sẵn pad lúc dựng và nối nhánh mã hóa khi có camera; nhánh của ô ở lại pipeline khi camera đi để camera kế tiếp dùng lại (cũng giữ một phiên NVENC). Probe giữ `slot_lock` khi xử lý batch nên camera không bị giải phóng giữa chừng.

---

## 9. Phát video lên web

**Q53. Video lên trình duyệt đi đường nào?**
*Gợi ý:* `tracker → nvstreamdemux → mỗi camera: queue → nvvideoconvert → nvdsosd (vẽ khung + ID) → nvvideoconvert → I420 → nvv4l2h264enc (NVENC) → h264parse → appsink`. Frame đã mã hóa chuyển sang một pipeline nhỏ riêng của mỗi camera (`appsrc ! h264parse ! flvmux ! rtmp2sink`) đẩy tới **MediaMTX**, trình duyệt xem bằng **WebRTC (WHEP)**. Chrome xem được ở 1920×1080, ~30 fps.

**Q54. Vì sao có pipeline publish riêng cho từng camera?**
*Gợi ý:* Để lỗi của server phát (MediaMTX chết) **không ảnh hưởng detection**. Pipeline chính không bao giờ lỗi ở nhánh encode; chỉ pipeline publish nhỏ bị dỡ và dựng lại mỗi 5 giây. Em thử: MediaMTX chết 8-9 giây, fps detection vẫn 30, stream tự về.

**Q55. Vì sao RTMP mà không RTSP để đẩy lên MediaMTX?**
*Gợi ý:* Em thử `rtspclientsink` trước và gặp **deadlock** khi chuyển NULL sau lỗi kết nối (GStreamer 1.20.3; gdb: main thread chờ mutex của rtspclientsink), làm engine treo khi tắt. Đổi sang RTMP (`rtmp2sink`) ổn định. Lưu ý `rtmp2sink` đòi URL có phần application (`rtmp://host:1935/live`), nên path là `live/cam<ID>`.

**Q56. Giới hạn số camera khi phát video?**
*Gợi ý:* Mỗi camera có overlay cần một phiên NVENC. Em đo trên máy này: **12 phiên đồng thời chạy được, thử 24 thì chỉ 12 khởi động**. Đo gộp phát hiện + phát ở 12 camera: mọi camera 30 fps (thấp nhất 29.5), GPU 43.6%, NVENC 27%, NVDEC 40%.

**Q57. Độ trễ (latency) của video?**
*Gợi ý:* **Chưa đo.** Cách đo đúng là glass-to-glass: quay một đồng hồ hiển thị mili giây trong cảnh, so với đồng hồ trên màn hình web. Thành phần gây trễ: buffer RTSP, batch timeout, encode, MediaMTX, WebRTC jitter buffer. Nói thế còn hơn bịa một con số.

---

## 10. Backend, database, realtime, Telegram

**Q58. Backend có những API chính nào?**
*Gợi ý:* `/api/cameras` (CRUD, start/stop, status, stream, test-connection), `/api/events` (danh sách, lọc, PATCH status), `/api/settings`, `/api/engine`, `/api/ws` (WebSocket), và nhóm `/internal/*` (nhận event, heartbeat) chỉ dành cho engine.

**Q59. Vì sao dùng SQLAlchemy sync thay vì async?**
*Gợi ý:* Endpoint viết `def` thì FastAPI chạy trong threadpool, code đơn giản, ít bẫy; tải của hệ thống nhà riêng không cần async thuần. Đánh đổi: số kết nối đồng thời bị giới hạn bởi threadpool. Phần cần realtime em kiểm soát bằng `Hub` riêng (Q61).

**Q60. Camera bị xóa thì event cũ thế nào?**
*Gợi ý:* Xóa mềm (soft delete): camera đánh dấu đã xóa nhưng event và snapshot cũ còn. Event của camera đã xóa vẫn được lưu nếu engine còn gửi tới.

**Q61. WebSocket được thiết kế thế nào?**
*Gợi ý:* Một chiều server → client, tin nhắn `{type, data}` (`event.created`, `event.updated`, `camera.status`). `Hub.publish` gọi được từ mọi thread (endpoint `def` chạy trong threadpool) và chuyển sang event loop bằng `call_soon_threadsafe`. Mỗi client có `asyncio.Queue` 100 tin; đầy thì bỏ client và đóng mã 1013, để một trình duyệt kẹt không làm chậm `POST /internal/events`. Không replay: client đọc trạng thái hiện tại bằng REST khi nối lại.

**Q62. Có lỗi concurrency nào em đã gặp ở WebSocket?**
*Gợi ý:* Bản đầu dùng `asyncio.ensure_future` trần và bị `CancelledError` ngẫu nhiên (~1/4 lần chạy test) khi TestClient hủy scope ngay sau khi đóng. Đổi sang **task group của anyio** thì hết; em chạy 25 lần liên tiếp để chắc.

**Q63. Telegram được gửi thế nào và vì sao không gửi trong request nhận event?**
*Gợi ý:* `POST /internal/events` chỉ lưu rồi `notifier.trigger()`; một thread nền (`Notifier`) lấy các event `pending` theo thời gian, gửi ảnh (hoặc text nếu file ảnh đã mất), rồi ghi `notify_status`. Lỗi Telegram → event giữ `pending`, thử lại với backoff 1→60 giây. Lý do tách: Telegram chậm (em test `sleep 3s` giả: 2 POST vẫn < 1 giây). Event **không bao giờ mất hay đổi** vì Telegram.

**Q64. Các giá trị `notify_status` nghĩa là gì?**
*Gợi ý:* `pending` đang chờ; `sent` đã gửi; `disabled` Telegram chưa cấu hình/chưa bật (ví dụ `ENABLE_TELEGRAM=false` trong `.env`); `skipped` bị bỏ theo luật (tắt Alerts hoặc nằm trong giờ yên lặng); `failed` nằm `pending` quá 600 giây (báo trễ vậy là nhiễu).

**Q65. Giờ yên lặng (quiet hours) tính thế nào?**
*Gợi ý:* Theo giờ **local của máy chủ**; giờ bắt đầu tính, giờ kết thúc không tính, qua nửa đêm được. Bằng nhau khi đang bật → 422. Notifier đọc luật **một lần mỗi vòng**; sự kiện trong giờ yên lặng vẫn lưu, vẫn hiện, chỉ không gửi Telegram.

**Q66. Làm sao backend điều khiển engine?**
*Gợi ý:* Một `reconciler` (thread nền) so danh sách camera mong muốn (chưa xóa + `enabled` + `desired_state=running`) với danh sách Manager đang áp dụng (so băm `revision`); khác thì `PUT /cameras`. Quét mỗi 5 giây nên Manager chết hoặc restart cũng tự được đẩy lại. Đây là mẫu *reconciliation loop* giống Kubernetes: khai báo trạng thái mong muốn, vòng lặp kéo thực tế về đúng.

**Q67. Mật khẩu RTSP được bảo vệ thế nào?**
*Gợi ý:* Không nằm trong argv (lộ qua `ps`): đi qua `--cameras-file` quyền 600 (engine từ chối file có quyền group/others). API luôn trả URL đã che (`rtsp://***@host/path?***`). Log của Manager thay `scheme://user:pass@` bằng `***@`. Thông báo lỗi không kèm URL.

**Q68. Chức năng Test connection hoạt động thế nào? Có rủi ro gì?**
*Gợi ý:* RTSP thuần Python (không cần ffprobe): TCP connect → `DESCRIBE` (xử lý Basic/Digest), đọc SDP lấy codec, với H.264 giải mã SPS để ra độ phân giải. Rủi ro: backend kết nối tới bất kỳ host nào được đưa (**SSRF**). Em chấp nhận vì hệ thống chỉ chạy trong LAN; nếu mở ra ngoài thì phải chặn dải IP nội bộ hoặc yêu cầu đăng nhập.

---

## 11. Frontend & bản demo

**Q69. Frontend dùng công nghệ gì và vì sao?**
*Gợi ý:* React 19 + TypeScript + Vite, TanStack Query (cache, refetch), `motion` (animation, tôn trọng `prefers-reduced-motion`), không router (tab và camera nằm trong URL hash). Không thêm thư viện chart: biểu đồ cột bằng CSS để bundle nhỏ (~147 kB gzip).

**Q70. Dữ liệu realtime và dữ liệu REST kết hợp thế nào?**
*Gợi ý:* `useFleet` gộp `/status` (poll 5 giây) với `camera.status` đẩy qua WS thành một nguồn duy nhất. `event.created` làm vô hiệu cache `events` và gọi callback **đúng một lần mỗi `event_id`** (bản trùng bị bỏ). Nối lại WS thì refetch vì server không replay; mất kết nối thì xóa dữ liệu `live` để quay lại `/status`.

**Q71. Trang Wall giới hạn 4 ô phát trực tiếp, vì sao?**
*Gợi ý:* Mỗi ô là một luồng WebRTC đầy đủ (1080p) trong trình duyệt, 9 luồng cùng lúc sẽ nặng. Ô còn lại hiện snapshot + nút "Go live". Muốn tường nhiều ô cần luồng phụ độ phân giải thấp từ engine (chưa có).

**Q72. Em kiểm tra accessibility thế nào?**
*Gợi ý:* Playwright + axe (WCAG 2.1 A/AA) trên mọi trang và các hộp thoại; test bàn phím (focus trap, Esc, trả tiêu điểm về nút mở). Lần quét đầu tìm ra lỗi tương phản màu và thiếu focus trap, đã sửa. Em **chưa** thử với trình đọc màn hình thật.

**Q73. Bản demo trên GitHub Pages hoạt động thế nào? Có "lừa" người xem không?**
*Gợi ý:* Là bản build với `VITE_DEMO=true`, có một backend giả chạy **trong trình duyệt** (`src/demo/`): camera, sự kiện, cảnh người đi trong phòng (SVG và canvas) đều là dữ liệu giả, cố định hạt giống. Có dải thông báo rõ "Live demo with made-up cameras". Em nói thẳng đó là bản mô phỏng để xem giao diện; bản thật chạy bằng Docker trên GPU. Nhánh demo bị cắt khỏi bản build thường.

---

## 12. Docker, bảo mật, đăng nhập

**Q74. Triển khai gồm những container nào?**
*Gợi ý:* `db` (PostgreSQL 16), `mediamtx`, `backend` (FastAPI, tự `alembic upgrade head`), `engine` (DeepStream + Manager, có GPU), `frontend` (nginx phục vụ web và chuyển `/api`, gồm WebSocket), và `backup` (`pg_dump` định kỳ). Chỉ publish cổng web (8080), 8889 (WebRTC) và 8189/udp; backend, Manager, RTMP, PostgreSQL không mở ra host.

**Q75. Quản lý bí mật thế nào?**
*Gợi ý:* `POSTGRES_PASSWORD` và `INTERNAL_TOKEN` bắt buộc (`${VAR:?}`), nằm trong `.env` (gitignore), không nằm trong image. Engine nhận token qua biến môi trường `DS_ENGINE_TOKEN`, không qua argv. Em quét log soak: không có mật khẩu DB hay token trong log nào.

**Q76. Hệ thống có đăng nhập không? Vì sao?**
*Gợi ý:* **Giao diện web chưa có**, đó là quyết định có chủ đích vì hệ thống chạy trong LAN nhà riêng. Phần máy-với-máy có `INTERNAL_TOKEN` và nginx trả 404 cho `/internal`; Manager không xác thực nhưng không publish cổng. Khi nào phải thêm: mở ra ngoài LAN, nhiều người dùng với quyền khác nhau, hoặc cần biết ai làm gì. Thứ tự em sẽ làm: tài khoản riêng → hai vai trò (xem / quản trị) → nhật ký truy cập → tách camera ra VLAN. Nếu cần truy cập từ xa trước khi có đăng nhập, dùng VPN (WireGuard/Tailscale) thay vì port-forward.

**Q77. Image engine 31.7 GB, có vấn đề gì?**
*Gợi ý:* Do image nền DeepStream triton (rất lớn), không phải code của em. Cách giảm: dùng bản `base` thay vì `triton` nếu không cần Triton, build nhiều giai đoạn. Em chưa tối ưu.

**Q78. Sao lưu dữ liệu thế nào?**
*Gợi ý:* Service `backup` chạy `pg_dump -Fc` mỗi 24 giờ, kiểm bản dump bằng `pg_restore -l`, ghi file tạm rồi rename, giữ 7 bản mới nhất ở `data/backups/` (nằm ngoài volume nên sống sót qua `down -v`). Em đã thử khôi phục: `pg_restore` khớp 500/500 dòng. Chưa làm: tự copy sang máy khác, backup snapshot.

---

## 13. Hiệu năng, benchmark, soak test

**Q79. Em đo hiệu năng bằng gì?**
*Gợi ý:* `engine/tools/benchmark.py`: dùng camera RTSP giả (`rtsp_test_server`, mỗi camera một phiên NVDEC), mỗi ca đo 25 giây, ghi fps từng camera, `nvidia-smi` (GPU, NVENC, NVDEC, VRAM), CPU và RSS của engine; tự build engine TensorRT cho từng N.

**Q80. Kết quả chính?**
*Gợi ý:* Chỉ phát hiện + tracking, 1080p H.264 30 fps: N=8: GPU 33%; N=16: 57%; N=20: 75% (có lúc tụt 24 fps); **N=24: GPU 93%, NVDEC 99%, sát mép (có lúc 22.5 fps); N=28: hỏng** (25.5 fps/camera, tổng bão hòa ~730 fps). Kết luận: ổn định tới ~20 camera trên RTX 4060 Laptop 8 GB.

**Q81. Giới hạn của phép đo?**
*Gợi ý:* Mọi camera giả phát **cùng một video** (cùng cảnh, ít người); camera thật khác độ phân giải/fps/codec thì trần khác; chưa đo latency; mỗi N cần engine TensorRT riêng; camera thật có jitter mà bản giả không có.

**Q82. Nút cổ chai thật là gì?**
*Gợi ý:* **Bộ giải mã NVDEC**, không phải TensorRT. Với nguồn file đọc nhanh nhất có thể, tổng ~850 fps từ N=2 trở lên với NVDEC 100%. Vì vậy tối ưu inference (INT8, model nhỏ hơn) không nâng được trần số camera; muốn tăng phải giảm tải giải mã (độ phân giải thấp hơn, thêm GPU).

**Q83. Soak test 4 giờ cho kết quả gì?**
*Gợi ý:* 4 camera giả, 4 giờ: camera chậm nhất 29.6 fps, 0 restart container, 0 restart `ds_engine`, 0 container unhealthy, 0 dòng ERROR, không lộ secret, VRAM không tăng (-6.2%). **Cảnh báo (WARN):** RAM engine 930 → 1081 MiB (+16.2%, ~55 MiB/giờ), tốc độ tăng chậm lại ở ~50 phút cuối nhưng chưa phẳng. Em chưa kết luận được là leak hay cache chưa bão hòa. Nếu tuyến tính thì ~1.3-1.5 GiB/ngày, nên chạy 24/7 phải theo dõi.

**Q84. Nếu phải tìm nguyên nhân RAM tăng, em làm gì?**
*Gợi ý:* (1) Chạy dài hơn xem có phẳng không. (2) So RSS với kích thước các hàng đợi/bộ đệm (publisher, snapshot) để loại trừ. (3) Đặt `MALLOC_ARENA_MAX` hoặc thử jemalloc: phân mảnh của glibc malloc thường trông như leak. (4) `valgrind --tool=massif` hoặc `heaptrack` trên một lần chạy ngắn với video lặp. (5) Chia nhỏ: tắt snapshot, tắt phát video, tắt publisher, xem tắt cái nào thì hết tăng. Ứng viên em nghi: hàng đợi snapshot, allocator, libcurl, metadata DeepStream.

---

## 14. Testing

**Q85. Chiến lược test tổng thể?**
*Gợi ý:* Nhiều tầng: **test C** (`make test-core`, 29 test, chạy trên host dưới ASan/UBSan, không cần GPU), **pytest** (293 test, trong đó backend dùng cụm PostgreSQL tạm tự khởi tạo nên không đụng DB của chủ máy), **vitest** (113), **Playwright** (28, kèm axe), và **test engine thật** (`RUN_GPU_TESTS=1`, mặc định bị skip, cần GPU rảnh). (Số test thay đổi theo thời gian; chạy lại trước khi nói con số.)

**Q86. Làm sao đảm bảo test không gửi Telegram thật?**
*Gợi ý:* `make_client` mặc định dùng `NoTelegram` (ném lỗi nếu bị gọi) và tắt hai thread nền. Test nào tự gọi `create_app` phải làm tương tự. Đây là lớp bảo vệ có chủ đích vì token là của thật.

**Q87. Em có kiểm tra bản thân test không?**
*Gợi ý:* Có, bằng **đột biến (mutation)** thủ công: cố ý làm hỏng code (reload quên `ai_enabled`; tắt trả tiêu điểm trong hộp thoại) rồi xem test có đỏ không. Test không đỏ nghĩa là test vô dụng.

**Q88. Có test nào em thấy hay nhất?**
*Gợi ý:* Test vector chung Python ↔ C (cùng 16 vector chạy ở cả hai ngôn ngữ), và test migration so khớp model bằng `compare_metadata` rồi chạy downgrade/upgrade. Cũng có test WebSocket chạy uvicorn thật với client `websockets` thật qua TCP.

---

## 15. Điểm yếu & câu hỏi gài

> Học kỹ mục này. Tự nêu được hạn chế và hướng sửa là điểm cộng lớn.

**Q89. Dự án này có dùng AI để viết code không?**
*Gợi ý:* Hãy trả lời **trung thực**. Em dùng Claude Code như một người cùng lập trình (pair programmer) cho nhiều phần. Phần em làm chủ là: chọn kiến trúc, quyết định đánh đổi (một pipeline N camera, spool + idempotency), đọc và hiểu từng phần, chạy và kiểm trên GPU thật, và quyết định cái gì giữ, cái gì bỏ. Em có thể giải thích và chỉ code của từng phần (vì thế em ôn kỹ file này). **Đừng nói dối hay nhận mọi thứ là tự viết:** nếu không giải thích được code đang trong CV thì còn tệ hơn.

**Q90. Em đã thử với camera thật chưa?**
*Gợi ý:* Chưa. Em đã thử với camera RTSP giả (phát lại file theo thời gian thực), một luồng RTSP công cộng trong thời gian ngắn (60 giây: 1609 frame, 29-30 fps, 6 track), và file video. Chưa thử: camera thật có Digest/`rtsps://`, mất mạng thật, H.265, jitter. Kế hoạch: dùng điện thoại làm camera IP để thử trước.

**Q91. Con số nào em chưa đo?**
*Gợi ý:* ID switch (chưa ground truth), latency glass-to-glass, độ lệch ~10% với Ultralytics chưa giải thích hết, RAM tăng trong soak chưa chẩn đoán, chưa chạy nhiều ngày, chưa thử Firefox/Safari/điện thoại thật.

**Q92. Lỗi khó nhất em gặp?**
*Gợi ý:* Chọn một trong mục 17 và kể theo STAR. Ví dụ nên chọn: segfault do `saveImg=TRUE` (tìm bằng gdb) hoặc deadlock của `rtspclientsink`.

**Q93. Hạn chế đã biết của engine?**
*Gợi ý:* Lỗi của nguồn **không phải RTSP** (file hỏng) vẫn dừng cả pipeline với exit 1 (em thử giữ các nguồn khác chạy nhưng camera còn lại bị đói frame và engine treo khi tắt nên bỏ). `ai_enabled=false` **không tiết kiệm GPU** (nvinfer vẫn chạy, chỉ xóa kết quả). Một process: một GPU. Engine Manager không xác thực.

**Q94. Vì sao ví dụ JSON trong ROADMAP khác tin nhắn thật?**
*Gợi ý:* Frontend bám backend thật chứ không bám tài liệu: `{"type":"event.created","data":{...}}` và `camera_id` là số nguyên. Tài liệu kế hoạch viết trước khi code và không được cập nhật hết. (Nói được điều này cho thấy em kiểm chứng với hệ thống thật.)

**Q95. Nếu phải bỏ bớt một phần vì gấp, em bỏ gì?**
*Gợi ý:* Giữ phần ảnh hưởng độ tin cậy (phát hiện, spool + idempotency, không mất sự kiện), bỏ phần làm đẹp (animation, trang Wall, bản demo). Lý do: mất một cảnh báo an ninh tệ hơn việc giao diện xấu.

**Q96. Nếu có 50 camera thì sao?**
*Gợi ý:* Một GPU không đủ (trần ~20 camera 1080p), nên nhiều máy/GPU, mỗi máy một engine, backend tập trung. Snapshot lên object storage thay vì thư mục chung. Event qua HTTP đã tách máy được; thêm message bus (MQTT/Kafka) nếu cần tách rời. Giảm tải: dùng luồng phụ độ phân giải thấp cho detection, phát luồng chính chỉ khi có người xem.

---

## 16. Kiến thức nền Video / Edge AI

> Phần này theo bảng chủ đề bạn lưu trong `myCV/image.png`. Với mỗi mục, tách rõ **dự án đã làm gì** và **em biết thêm gì**. Đừng nhận mình làm thứ chưa làm.

**Q97. H.264 và H.265 khác gì? Khi nào chọn cái nào?**
*Gợi ý:* H.265 nén tốt hơn (cùng chất lượng thì băng thông thấp hơn, thường tiết kiệm khoảng một phần ba đến một nửa) nhưng tốn tính toán hơn để mã hóa/giải mã và tương thích trình duyệt kém hơn. H.264 tương thích rộng nhất (WebRTC trên trình duyệt hỗ trợ H.264). Dự án dùng H.264 cho cả nguồn đo và nhánh phát. **Chưa thử H.265 thật.**

**Q98. GOP, I-frame, P-frame, B-frame là gì? Vì sao publisher "bắt đầu ở key frame kế tiếp"?**
*Gợi ý:* I-frame (key frame) tự giải mã được; P/B frame phụ thuộc frame khác. GOP là nhóm frame giữa hai key frame. Người xem hoặc publisher mới vào giữa GOP không có tham chiếu nên không giải mã được, phải đợi key frame. Đó là lý do publisher dựng lại cũng đợi key frame và đưa PTS về 0.

**Q99. RTSP, RTP, RTMP, WebRTC khác nhau thế nào?**
*Gợi ý:* RTSP là giao thức **điều khiển** (DESCRIBE, SETUP, PLAY); dữ liệu media đi bằng RTP (UDP hoặc TCP xen kẽ). RTMP (đóng gói FLV) hay dùng để **đẩy** luồng lên server. WebRTC dành cho trình duyệt, độ trễ thấp (WHEP là chuẩn xem một luồng). Dự án: camera vào bằng RTSP, đẩy lên MediaMTX bằng RTMP, xem bằng WebRTC.

**Q100. Nguồn độ trễ trong chuỗi video là gì?**
*Gợi ý:* Buffer của RTSP/jitter, thời gian chờ batch của mux, inference, encode (GOP, B-frame), server trung gian, jitter buffer của trình duyệt. Giảm: bỏ B-frame, GOP ngắn, giảm timeout batch, `sync=false` cho nguồn live (em đã dùng `leaky=downstream` + `sync=false`).

**Q101. TensorRT làm gì để tăng tốc?**
*Gợi ý:* Gộp layer (fusion), chọn kernel tốt nhất cho GPU cụ thể (auto-tune), giảm độ chính xác (FP16/INT8), tối ưu bộ nhớ. Kết quả là engine gắn với GPU + phiên bản. Dự án: ONNX → FP16 engine theo từng batch.

**Q102. `trtexec` và `nsys` dùng để làm gì?**
*Gợi ý:* `trtexec` build và đo engine độc lập từ ONNX (latency, throughput, thử FP16/INT8). `nsys` (Nsight Systems) vẽ timeline CPU/GPU để thấy chờ ở đâu. Dự án: em đo bằng `nvidia-smi` và `benchmark.py`; **chưa dùng `nsys`**, đó là bước hợp lý tiếp theo để chẩn đoán nút cổ chai.

**Q103. Triton Inference Server khác `nvinfer` ra sao?**
*Gợi ý:* `nvinfer` chạy TensorRT **trong tiến trình** của pipeline: đơn giản, độ trễ thấp, hợp một model trên một GPU. `nvinferserver` nối với Triton: có model repository, nhiều framework (ONNX, PyTorch, TensorRT), dynamic batching, gRPC/HTTP, hợp khi có nhiều model hoặc suy luận tách máy. Em chọn `nvinfer` vì một model và muốn ít thành phần. **Em chưa dùng Triton**, nên nói "em hiểu khác biệt" chứ không nói "em đã làm".

**Q104. Dynamic batching là gì?**
*Gợi ý:* Server gom nhiều request nhỏ thành một batch trong thời gian chờ ngắn để GPU hiệu quả hơn, đánh đổi bằng độ trễ. Ở dự án, `nvstreammux` làm việc tương tự cho frame của nhiều camera (batch theo `batched-push-timeout`).

**Q105. Chạy trên edge (Jetson, ARM64, GPU nhỏ) khác gì?**
*Gợi ý:* Công suất và bộ nhớ hạn chế (Jetson dùng bộ nhớ chung CPU-GPU), cần build lại cho ARM64 và build lại engine TensorRT trên thiết bị, thường dùng FP16/INT8 và model nhỏ, theo dõi nhiệt/điện. Code DeepStream về nguyên tắc chạy trên Jetson, nhưng **em chưa chạy thử**. Nói thẳng điều đó và nói cách em sẽ làm: build thử trong container ARM64, đo fps/nhiệt.

**Q106. Docker giới hạn tài nguyên và GPU thế nào?**
*Gợi ý:* `deploy.resources.reservations.devices` (NVIDIA Container Toolkit) cấp GPU cho container; có thể giới hạn CPU/RAM (`cpus`, `mem_limit`). Dự án cấp GPU cho container engine, `init: true` để dọn tiến trình con, `stop_grace_period: 45s` cho engine tắt gọn. Em chưa đặt giới hạn tài nguyên chặt.

**Q107. OpenVINO là gì và có liên quan không?**
*Gợi ý:* Bộ công cụ tối ưu suy luận cho CPU/GPU Intel. Dự án chạy trên GPU NVIDIA nên không dùng. Khi không có GPU NVIDIA thì ONNX → OpenVINO là hướng hợp lý. Em chưa làm.

**Q108. `pybind11` và Python/C++ interop?**
*Gợi ý:* Dùng để gọi code C++ từ Python (hoặc ngược lại). Dự án đi hướng khác: engine hoàn toàn C/C++ (parser bằng C++), Python chỉ ở backend và công cụ, không có pybind11. Em biết khái niệm nhưng chưa dùng.

**Q109. Kernel CUDA, stream, memory: em hiểu đến đâu?**
*Gợi ý:* Dự án **không** viết kernel CUDA riêng (DeepStream đã lo). Em hiểu: kernel chạy song song trên nhiều thread theo block/grid, stream cho phép chồng việc, bộ nhớ có host/device/unified và copy qua PCIe tốn kém (vì vậy giữ frame trong NVMM). Em dùng `compute-sanitizer` để kiểm lỗi CUDA (0 lỗi). Nếu vị trí cần CUDA sâu, nói rõ đây là phần em đang học thêm.

**Q110. IoU, NMS, mAP là gì? (câu nền hay bị hỏi)**
*Gợi ý:* IoU = diện tích giao / diện tích hợp của hai box. NMS: giữ box điểm cao nhất, bỏ box chồng lên nó quá ngưỡng IoU. mAP: trung bình độ chính xác trung bình (AP) qua các lớp, AP là diện tích dưới đường precision-recall; mAP@0.5 dùng ngưỡng IoU 0.5, mAP@0.5:0.95 lấy trung bình nhiều ngưỡng. Xem thêm live coding ở `README.md` cùng thư mục (Q81, Q82).

---

## 17. Đã thử và không dùng được

> Đây là chất liệu kể chuyện tốt nhất. Mỗi mục: **thử gì → thấy gì → vì sao → làm gì thay thế**.

1. **`rtspclientsink` để đẩy lên MediaMTX:** deadlock khi chuyển NULL sau lỗi kết nối (GStreamer 1.20.3; gdb thấy main thread chờ mutex) → engine treo khi tắt → **đổi sang RTMP**.
2. **`saveImg=TRUE` để lưu JPEG:** segfault khi thư mục không ghi được (`jpeg_write_to_file` không kiểm `fopen`) → **dùng `attachUsrMeta` + tự ghi file ở thread riêng**.
3. **Tự `set_state(NULL)` rồi bật lại `nvurisrcbin` để reconnect:** báo `Padname vsrc_0 is not unique`, camera không bao giờ về → **để bin tự reconnect, engine chỉ ghi nhận**.
4. **Khởi động lại nhánh mã hóa tại chỗ, hoặc xin lại pad `nvstreamdemux`:** mất sticky event, encoder không ra frame, hoặc pad trả NULL → **xin sẵn pad lúc dựng pipeline, nhánh ở lại pipeline và được dùng lại**.
5. **Chặn EOS của RTSP bằng pad probe:** thừa, bin đã tự xử lý EOS.
6. **`g_async_queue_push(NULL)` làm dấu dừng:** bị từ chối nên `g_thread_join` treo → **dùng marker riêng**.
7. **Giữ các nguồn khác chạy khi một nguồn file hỏng:** camera còn lại đói frame và engine treo khi tắt → **bỏ, chấp nhận exit 1**.
8. **Cạn `buffer-pool-size` của `nvstreammux`:** hai `queue` × 4 buffer làm cạn pool, mọi camera dừng ~6 giây → **mỗi queue giữ 1, pool = min(16, 4+N)**.
9. **Camera giả phát vòng lặp bằng seek trực tiếp trong `gst-rtsp-server`:** engine không nhận frame → **nối nhiều bản video bằng `concat` + `mp4mux`, không mã hóa lại**.
10. **`frontend/.env.demo` cho bản demo:** bị `.gitignore` gốc chặn nên CI không thấy → **dùng `define` trong `vite.config.ts`**.

---

## 18. Hành vi (STAR)

Chuẩn bị 2-3 câu chuyện thật. Khung: **S**ituation (bối cảnh) → **T**ask (nhiệm vụ) → **A**ction (em làm gì, cụ thể) → **R**esult (kết quả, có số).

**Q111. Kể về một lỗi khó em đã gặp.**
*Gợi ý (khung mẫu, thay bằng trải nghiệm thật của em):* **S:** engine chạy ổn nhưng khi thư mục snapshot không ghi được thì cả engine sập. **T:** tìm nguyên nhân, không để một lỗi đĩa giết giám sát. **A:** chạy lại dưới gdb, thấy `fwrite` ← `jpeg_write_to_file` trong thư viện NVIDIA, đổi sang `attachUsrMeta` và ghi file ở thread riêng, thêm test thư mục chỉ-đọc. **R:** thư mục chỉ-đọc không còn làm engine sập, event ra với `snapshot:null`.

**Q112. Kể một lần em thử cách A thất bại rồi đổi sang cách B.**
*Gợi ý:* Chọn chuyện `rtspclientsink` → RTMP hoặc reconnect `nvurisrcbin` (mục 17). Điểm cần nhấn: em **đo/debug trước khi bỏ** (gdb, log), và ghi lại lý do để không thử lại.

**Q113. Một quyết định kỹ thuật em phải cân nhắc.**
*Gợi ý:* Một pipeline N camera so với N process (có số đo: Q6). Nhấn: đã đo cả hai, không chọn theo cảm tính.

**Q114. Em học thế nào khi gặp công nghệ mới (DeepStream)?**
*Gợi ý:* Đọc sample C của NVIDIA (`deepstream-test1/3`) để học cấu trúc rồi tự viết, không chép; dựng pipeline nhỏ bằng `gst-launch` thử từng element; viết test và tài liệu học riêng (`HOC_DU_AN.md`) để chắc mình hiểu; ghi lại điều đã thử không được. Trả lời theo đúng cách học của em.

**Q115. Em muốn làm gì tiếp theo trong dự án?**
*Gợi ý:* Theo thứ tự đáng làm: (1) thử camera thật (điện thoại làm camera IP) và đo latency, (2) gán nhãn tập video đo ID switch/độ chính xác, (3) chẩn đoán RAM tăng bằng massif/jemalloc, (4) thêm đăng nhập đơn giản, (5) đường cắt ngang (line crossing) và vùng cấm (ROI) bằng `nvdsanalytics`.

---

## 19. Live coding

**Q116. Viết bộ lọc chống trùng sự kiện (như `EventManager`).**
```python
class Dedup:
    def __init__(self, timeout):
        self.timeout = timeout
        self.last = {}                      # track_id -> thời điểm thấy gần nhất

    def process(self, now, track_ids):
        new = []
        for i in track_ids:
            t = self.last.get(i)
            if t is None or now - t > self.timeout:   # '>' : vắng đúng bằng timeout thì chưa tính
                new.append(i)
            self.last[i] = now
        # dọn track đã vắng quá timeout để bộ nhớ không phình
        self.last = {k: v for k, v in self.last.items() if now - v <= self.timeout}
        return new
```
*Lưu ý khi nói:* `now` là tham số (test không cần `sleep`); nói về ranh giới `>` và về việc dọn bộ nhớ.

**Q117. Viết backoff nhân đôi 1 → 60 giây.**
```c
#include <stdint.h>
int64_t backoff_next_ms(int64_t cur) {
    if (cur <= 0) return 1000;             // lần đầu
    return cur >= 30000 ? 60000 : cur * 2; // nhân đôi, trần 60 s
}
// reset về 0 khi gửi thành công
```
*Lưu ý:* nói vì sao cần trần, và vì sao nên thêm jitter ngẫu nhiên khi nhiều client cùng thử lại.

**Q118. Viết hàng đợi giới hạn: đầy thì bỏ item mới và đếm.**
```python
import queue

class BoundedSink:
    def __init__(self, maxsize):
        self.q = queue.Queue(maxsize=maxsize)
        self.dropped = 0

    def offer(self, item):
        try:
            self.q.put_nowait(item)      # không bao giờ chặn nơi gọi (như probe)
            return True
        except queue.Full:
            self.dropped += 1
            return False
```
*Lưu ý:* giải thích vì sao không dùng `put()` chặn trong probe (Q11).

**Q119. Viết IoU và NMS đơn giản.** Xem `README.md` cùng thư mục (Q81, Q82); nói thêm độ phức tạp O(n²) và cách vector hóa bằng numpy.

---

## 20. Checklist

- [ ] Nói được Q1 (30 giây) và Q2 (2 phút) trôi chảy, không nhìn ghi chú.
- [ ] Vẽ được sơ đồ luồng (Q3) lên giấy: nguồn → mux → nvinfer → tracker → probe → event → publisher/spool → backend → web/Telegram.
- [ ] Giải thích được 5 quyết định: 1 pipeline N camera (có số), C thay vì Python, spool + `event_id` idempotent, thời gian theo PTS, hot reload bằng SIGHUP.
- [ ] Nhớ các con số: ~20 camera 1080p@30 ổn định (GPU 75%), N=24 sát mép (NVDEC 99%), 8 camera 1 process 1.2 GiB so với 8 process 5.1 GiB RAM, soak 4 giờ ≥29.6 fps, RAM +16%.
- [ ] Có sẵn 3 câu chuyện thất bại (mục 17) và biết kể theo STAR.
- [ ] Nói trước 5 hạn chế: chưa camera thật, chưa đo ID switch, chưa đo latency, RAM tăng trong soak, chưa đăng nhập (và lý do).
- [ ] Trả lời được thẳng câu "có dùng AI viết code không" (Q89).
- [ ] Mở sẵn: `engine/src/ds_engine.c`, `engine/configs/pgie_yolov8n.txt`, `engine/src/core/event_manager.c`, `backend/routers/internal.py`.
- [ ] Chạy thử demo trước buổi phỏng vấn: bản GitHub Pages, và (nếu có GPU) `docker compose --env-file .env -f deploy/compose.yml up -d --build`.
- [ ] Kiểm tra `.env` (`ENABLE_TELEGRAM=true`) nếu định demo gửi Telegram thật, và dùng **ít camera** để không làm đầy chat.
- [ ] Chuẩn bị 2-3 câu hỏi ngược cho người phỏng vấn (bài toán thật của họ, stack, cách họ đo chất lượng).
