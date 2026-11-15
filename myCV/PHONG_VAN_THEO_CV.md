# Ôn phỏng vấn theo CV — AI-Based-Home-Surveillance

> File này **không lên GitHub** (`myCV/` nằm trong `.gitignore`).
> **Khác với `interview/PHONG_VAN_DEEPSTREAM.md`:** file kia sắp theo *chủ đề kỹ thuật* (119 câu, sâu). File này sắp theo *từng dòng trong CV*: nhà tuyển dụng đọc một dòng rồi hỏi dồn tiếp. Học file này trước, gặp câu nào cần sâu hơn thì mở file kia (có ghi số mục).
> **Cách học:** che phần *Trả lời*, tự nói thành tiếng 1-2 phút, rồi so. Đừng học thuộc từng chữ, hãy thuộc **ý và con số**.
> **Trước khi đi phỏng vấn:** chạy lại `make -C engine test-core`, `pytest`, `npm test` trong `frontend/` để số test nói ra là số thật (số trong file này là số lúc viết, 2026-11).

## Ba quy tắc trả lời

1. **Phân biệt ba loại câu:** "em đã làm", "em đã đo (có số)", "em chưa làm / chưa đo". Loại thứ ba nói thẳng, đừng để bị bắt bẻ.
2. **Mỗi quyết định kết bằng đánh đổi:** "em chọn A vì ..., cái giá là ...".
3. **Chủ động nêu hạn chế trước.** Người phỏng vấn thấy bạn biết rõ chỗ yếu của mình sẽ tin các chỗ còn lại.

## Mục lục

- A. Mở đầu (tiếng Việt)
- B. Dòng CV 1: "C engine trên DeepStream, N camera, một pipeline GPU"
- C. Dòng CV 2: "một pipeline chung so với một process mỗi camera"
- D. Dòng CV 3: "giao sự kiện đáng tin cậy"
- E. Dòng CV 4: "backend, dashboard, Docker"
- F. Câu gài về tính trung thực của CV
- G. Ba câu chuyện thất bại (STAR)
- H. Hai project nhỏ: YOLOv5 xe quân sự/dân sự và RAG chatbot
- I. Kiến thức nền hay bị hỏi chen vào
- J. English Q&A (CV ghi English: basic, nên giữ câu ngắn)
- K. Câu hỏi để hỏi lại nhà tuyển dụng
- L. Checklist cuối

---

## A. Mở đầu

**A1. Em giới thiệu bản thân và dự án chính trong 1 phút.**

*Trả lời:* "Em là sinh viên năm tư Kỹ thuật phần mềm ở Thủy Lợi, quan tâm đến thị giác máy tính và xử lý video thời gian thực. Dự án em tâm đắc nhất là một hệ thống giám sát nhiều camera. Engine viết bằng C trên NVIDIA DeepStream: một pipeline GPU đọc N luồng RTSP hoặc file MP4, phát hiện và tracking người bằng YOLOv8n qua TensorRT, rồi đổi thành *sự kiện* không trùng lặp để gửi cho backend FastAPI lưu vào PostgreSQL và báo qua Telegram. Có dashboard React xem video trực tiếp kèm khung phát hiện, chạy bằng Docker Compose. Phần em thấy khó và học được nhiều nhất là đo hiệu năng và làm cho việc giao sự kiện không bị mất khi mạng hoặc backend chết. Em cũng nói trước: em mới thử với camera giả lập và một luồng RTSP công cộng, chưa thử camera vật lý."

**A2. Vì sao em chọn làm dự án này mà không phải một bài YOLO đơn giản?**

*Trả lời:* "Bài YOLO đơn giản chỉ trả lời 'model nhận ra người không'. Một hệ thống thật còn phải trả lời: chạy được bao nhiêu camera trên một GPU, camera rớt thì sao, mạng đứt thì sự kiện có mất không, một người đứng yên 10 phút có bị báo hàng trăm lần không. Em muốn học phần *kỹ thuật xung quanh* model, vì đó là phần khác biệt giữa demo và sản phẩm."

**A3. Vì sao em ứng tuyển vị trí intern Computer Vision / AI?**

*Trả lời:* "Em muốn làm việc với pipeline video và suy luận trên GPU cùng người đi trước. Dự án của em cho em nền: biết GStreamer, TensorRT, batch inference, tracking và các vấn đề vận hành như reconnect hay backpressure. Em muốn học thêm phần em chưa chạm: chất lượng model (gán nhãn, đánh giá, huấn luyện lại), tối ưu INT8, và triển khai thật trên edge."

---

## B. Dòng CV 1: "C engine trên DeepStream, N camera trong một pipeline GPU, 30 fps"

> Mở thêm: `interview/PHONG_VAN_DEEPSTREAM.md` mục 2, 3, 4, 5.

**B1. Vẽ pipeline cho anh/chị xem, từ nguồn đến sự kiện.**

*Trả lời:*
`nvurisrcbin` (RTSP/file, giải mã bằng NVDEC) → `nvstreammux` (gom N frame thành 1 batch) → `nvinfer` (YOLOv8n TensorRT FP16 + parser bbox tự viết) → `nvtracker` (NvDCF, gán track ID) → **pad probe** trên src pad của tracker → hàng đợi → thread `publisher`.
Probe copy người đã có track ID vào struct `Detection`, đưa cho `EventManager` của từng camera. Nhánh video (nếu bật): `nvstreamdemux` → vẽ bbox (OSD) → NVENC H.264 → RTMP → MediaMTX → WebRTC cho trình duyệt.
Mỗi camera là một **ô (slot)**; `source_id` chính là số ô, và `track_id` chỉ duy nhất trong một source nên khóa là `(source_id, track_id)`.

**B2. Vì sao viết engine bằng C mà không dùng Python (`pyds`)?**

*Trả lời:* "Probe chạy trên đường nóng của từng batch frame, em muốn kiểm soát bộ nhớ và không có thread Python/GIL chen giữa. Python chỉ còn ở backend, engine manager và công cụ test, không nằm giữa đường xử lý frame. Cái giá: viết lâu hơn, dễ lỗi bộ nhớ hơn. Em giảm rủi ro bằng cách tách phần logic thuần C (`EventManager`, spool, JSON) ra `engine/src/core/` để test trên máy thường với ASan/UBSan, không cần GPU."

**B3. `nvstreammux` làm gì? Em chỉnh tham số nào?**

*Trả lời:* "Nó gom frame của N nguồn thành một batch để `nvinfer` chạy một lần cho cả batch (hiệu quả GPU hơn chạy từng ảnh). Tham số em phải chỉnh có lý do đo được: `batched-push-timeout` — với 40 ms thì khi một camera mất, camera còn lại tụt từ 30 xuống 25 fps vì muxer chờ frame của camera kia; giảm còn 20 ms thì hết. `buffer-pool-size` — mặc định 4, mà mỗi `queue` của nhánh stream giữ nguyên một batch buffer nên pool cạn và detection mọi camera dừng khoảng 6 giây; em để mỗi queue giữ 1 và đặt pool = `min(16, 4 + N)`."

**B4. Model của em: từ `yolov8n.pt` đến chạy trong DeepStream thế nào?**

*Trả lời:* "Export sang ONNX với dynamic batch (`engine/tools/export_yolov8_onnx.py`). `nvinfer` tự build engine TensorRT FP16 ở lần chạy đầu và lưu lại. Engine TensorRT gắn với batch size, nên mỗi số camera N cần một engine riêng, mất khoảng 150 giây để build; engine manager tự chọn đúng file theo N. Đầu ra YOLOv8 không có objectness mà có điểm cho từng lớp, nên em viết parser riêng (`engine/parser/`, C++) để lọc lớp `person`, ngưỡng 0.25, và đổi hộp sang định dạng DeepStream. (Trước khi đi phỏng vấn mở file parser xem lại để nói đúng chi tiết đang code.)"

**B5. Vì sao FP16 mà không INT8?**

*Trả lời:* "FP16 gần như không mất độ chính xác và không cần dữ liệu hiệu chỉnh. INT8 nhanh hơn và nhẹ bộ nhớ hơn nhưng cần tập calibration và phải đo lại độ chính xác. Trong các phép đo của em, nút cổ chai là bộ giải mã video chứ không phải suy luận, nên INT8 chưa mang lại lợi ích lớn. **Em chưa làm INT8**, em hiểu khái niệm."

**B6. Vì sao chọn NvDCF làm tracker?**

*Trả lời:* "Là lựa chọn của em sau khi thử cả hai trên một clip 720p: NvDCF ra 21 track (3 track ngắn), ByteTrack ra 36 track (9 ngắn). Ít track ngắn nghĩa là ít bị đứt ID, nên ít sự kiện trùng. **Nhưng em phải nói rõ:** em không có ground truth gán nhãn tay nên không đo được ID switch hay MOTA; đây mới là chỉ báo sơ bộ chứ không phải kết luận. Bước tiếp theo là gán nhãn một đoạn ngắn để đo thật."

**B7. CV ghi "30 fps với ~20 camera". Nói chính xác hơn đi.**

*Trả lời (có số, đừng nói quá):* "Em đo trên RTX 4060 Laptop, camera giả phát lại cùng một video 1080p H.264 30 fps, chỉ phát hiện và tracking (không phát video):

| Số camera | GPU | NVDEC | Ghi chú |
|---|---|---|---|
| 8 | 33% | 31% | 30 fps |
| 16 | 57% | 56% | 30 fps |
| 20 | 75% | 74% | có vài khoảng tụt xuống 24 fps |
| 24 | 93% | 99% | trung vị 30 fps nhưng có lúc xuống 22,5 |
| 28 | — | — | hỏng: ~25,5 fps mỗi camera |

Nên em nói 'ổn định tới khoảng 16-20 camera', còn 24 là sát trần. Hạn chế của phép đo: mọi camera cùng một video, ít người, nên chưa đại diện cho nhà thật."

**B8. Vì sao 30 fps lại là thước đo? Có cần xử lý đủ 30 fps không?**

*Trả lời:* "Không nhất thiết cho việc phát hiện người trong nhà; có thể bỏ frame để tăng số camera. Em dùng 30 fps/camera làm thước đo vì đó là tốc độ nguồn: nếu pipeline theo kịp 30 fps thì nghĩa là không tích tụ độ trễ. Muốn nhiều camera hơn thì giảm fps xử lý (ví dụ chạy detection mỗi 2 frame, tracker nội suy). Em chưa làm phần này."

---

## C. Dòng CV 2: "một pipeline chung so với một process mỗi camera"

> Mở thêm: `interview/PHONG_VAN_DEEPSTREAM.md` mục 13 (Q6 và các câu quanh đó).

**C1. Em đã đo gì và kết quả ra sao?**

*Trả lời:* "Cùng 8 camera 1080p giả lập, cùng máy:

| | Một process, 8 camera | 8 process, mỗi cái 1 camera |
|---|---|---|
| RAM (RSS) | 1,2 GiB | 5,1 GiB (~630 MiB mỗi process) |
| VRAM | 1,6 GiB | 2,8 GiB |
| GPU | 33% | 41% |
| CPU | 34% một nhân | 95% |

Cả hai đều giữ 30 fps, nhưng một process nhẹ hơn rõ rệt. Em giữ kiến trúc **một pipeline cho N camera**."

**C2. Vì sao một process lại nhẹ hơn nhiều vậy?**

*Trả lời:* "Mỗi process phải nạp lại CUDA context, engine TensorRT, thư viện DeepStream và buffer pool riêng; chia sẻ được thì chỉ tốn một lần. Ngoài ra batch lớn dùng GPU hiệu quả hơn nhiều lần chạy batch 1. Em suy ra nguyên nhân từ số đo chứ chưa profile từng phần."

**C3. Vì sao em không đo N=12 hoặc 16 process để đối chứng?**

*Trả lời:* "RAM ước tính khoảng 7,5 GiB trong khi máy 15 GiB chỉ còn khoảng 7 GiB trống, có nguy cơ treo máy. Em chọn không chạy thay vì liều. Với 8 process đã đủ thấy xu hướng. Nói thật như vậy tốt hơn là bịa số."

**C4. Em nói nút cổ chai là bộ giải mã. Căn cứ nào?**

*Trả lời:* "Hai bằng chứng. Một: khi cho nguồn file đọc nhanh nhất có thể, tổng thông lượng chỉ ~850 fps từ N=2 trở đi và NVDEC đạt 100%, trong khi TensorRT chưa kín. Hai: ở N=24, NVDEC 99% còn GPU 93%, và N=28 hỏng khi NVDEC bão hòa. Nghĩa là muốn thêm camera thì phải thêm bộ giải mã (GPU khác), hoặc giảm độ phân giải/fps nguồn, chứ tối ưu model không giúp nhiều."

**C5. Nếu có 50 camera thì sao?**

*Trả lời:* "Một GPU không đủ. Nhiều máy, mỗi máy một engine, backend tập trung. Event đã đi qua HTTP nên tách máy được; snapshot phải lên object storage thay vì thư mục chung. Để giảm tải: dùng luồng phụ độ phân giải thấp cho detection, chỉ phát luồng chính khi có người xem. Em **chưa làm** những việc này, đây là hướng thiết kế."

**C6. Phát video có overlay thì giới hạn là gì?**

*Trả lời:* "Mỗi camera cần một phiên NVENC để mã hóa video có khung vẽ, mà GPU này có tối đa 12 phiên đồng thời (em thử 24 thì chỉ 12 chạy được). Em đo gộp phát hiện + phát video ở 12 camera: tất cả 30 fps (thấp nhất 29,5), GPU 43,6%, NVENC 27%, NVDEC 40%. Vậy trần có overlay là 12 camera trên card này."

---

## D. Dòng CV 3: "giao sự kiện đáng tin cậy, khử trùng lặp, spool ra đĩa, insert idempotent"

> Mở thêm: `interview/PHONG_VAN_DEEPSTREAM.md` mục 6, 7.

**D1. "Sự kiện" khác "detection" thế nào? Vì sao cần khử trùng lặp?**

*Trả lời:* "Detection là kết quả mỗi frame, 30 lần mỗi giây mỗi camera. Nếu mỗi detection là một thông báo thì một người đứng trong phòng sẽ gửi hàng nghìn tin. Sự kiện `PERSON_DETECTED` chỉ phát khi một `track_id` **xuất hiện lần đầu**, hoặc **quay lại sau khi vắng quá `absence_timeout`** (mặc định 10 giây)."

**D2. Mô tả `EventManager`.**

*Trả lời:* "Một state machine thuần túy, không I/O. Nó giữ `last_seen` theo từng `track_id`. Mỗi frame, người gọi truyền vào thời gian `now` và danh sách detection. Track mới hoặc đã vắng quá timeout thì phát event; track cũ không còn xuất hiện quá timeout thì bị dọn. Mỗi camera một instance, nên hai camera không ảnh hưởng nhau."

**D3. Vì sao em port từ Python sang C, và làm sao biết hai bản giống nhau?**

*Trả lời:* "Em viết đặc tả trước bằng Python (dễ đọc, dễ test), sau đó port sang C vì engine C không import được Python. Để hai bản không lệch nhau, em sinh **16 vector test** dạng JSON từ bản Python (mỗi bước ghi `now`, detection, event mong đợi, trạng thái track sau đó) và cả hai bản phải pass đúng bộ vector đó. Bản C chạy dưới ASan/UBSan. Đây là kiểu *conformance test*: một nguồn chân lý, hai bản cài đặt."

**D4. Thời gian logic dùng giờ tường hay PTS? Vì sao?**

*Trả lời:* "Em dùng `stream_start_ns + PTS của frame`, với `stream_start_ns` = giờ tường lúc thấy frame đầu trừ PTS của frame đó. Lý do: nếu dùng giờ tường thì khi chạy file video nhanh hơn thời gian thực, hoặc khi pipeline bị chậm tạm thời, 'vắng 10 giây' sẽ tính sai. PTS đi theo nội dung video nên timeout đúng theo thời gian của cảnh. Khi camera RTSP reconnect, PTS có thể về 0 nên em tính lại `stream_start_ns` (cờ `reanchor`)."

**D5. Probe có được gọi mạng hoặc ghi đĩa không?**

*Trả lời:* "Không. Probe chạy trên thread streaming của GStreamer; nếu nó chặn thì cả pipeline và mọi camera chậm theo. Probe chỉ copy giá trị ra struct riêng rồi đẩy vào `GAsyncQueue` giới hạn 1024 item. Thread `publisher` riêng lấy ra, ghi snapshot, gọi HTTP. Hàng đợi đầy thì bỏ và đếm `events_dropped`, ưu tiên không làm chậm video. Em chia file theo luồng chạy: `probes.c` chỉ *sản xuất* vào hàng đợi, `delivery.c` mới *tiêu thụ*, nên kiểm tra quy tắc này bằng mắt được."

**D6. Backend sập thì sao? Mô tả đường đi của một sự kiện.**

*Trả lời:* "`publisher` POST bằng libcurl. 2xx hoặc 409 = xong. 400/413/422 = backend từ chối hẳn, đếm `rejected`, không thử lại. Mọi thứ khác (lỗi mạng, 5xx, 408, 429, cả 401/404 do cấu hình sai) = thử lại sau. Khi thất bại, backoff 1 giây nhân đôi tới 60 giây, và trong lúc chờ mọi event đi thẳng vào **spool** (file JSON-lines, `fsync` mỗi lần ghi, tối đa 256 MB) để worker không kẹt. Backend sống lại thì spool được đổ ra theo đúng thứ tự. Spool còn sót từ lần chạy trước cũng được giao ở lần chạy sau."

**D7. Giao lại thì có bị trùng không?**

*Trả lời:* "Không, nhờ **idempotency**: mỗi event có `event_id` là UUID do engine tạo, và là khóa chính trong bảng `events`. Backend dùng `INSERT ... ON CONFLICT DO NOTHING`: 201 nếu mới, 200 nếu trùng (không ghi đè). Engine coi cả hai là thành công. Đã thử thật: chạy lại cùng spool, nhận đủ 216 id khác nhau từ hai lần chạy và spool về 0 byte."

**D8. Vì sao backend trả 422 cho event mà camera không tồn tại, chứ không phải 404?**

*Trả lời:* "Vì engine coi 400/413/422 là 'không bao giờ lưu được' nên bỏ, còn 404 thì nó nghĩ là cấu hình sai tạm thời và thử lại mãi. Nếu trả 404 thì event của camera đã xóa sẽ nằm trong spool và bị gửi lại vô hạn, làm spool đầy. Mã trạng thái là một phần của *giao thức* giữa hai bên."

**D9. Vì sao không dùng Kafka hoặc MQTT?**

*Trả lời:* "Chỉ một máy trong LAN, vài event mỗi phút. HTTP + spool file là đủ, ít thành phần phải vận hành, và đã đạt yêu cầu 'không mất event'. Nếu mở rộng nhiều máy và cần tách rời producer/consumer thì mới đáng đưa message bus vào. Cái giá của lựa chọn hiện tại: tự viết spool và retry (có test), nhưng em kiểm soát hoàn toàn hành vi."

**D10. Snapshot thì sao? Vì sao em không dùng `saveImg=TRUE` của NVIDIA?**

*Trả lời:* "`saveImg=TRUE` làm engine segfault khi thư mục không ghi được, vì `jpeg_write_to_file` không kiểm tra `fopen` (em tìm ra bằng gdb: `fwrite` ← `jpeg_write_to_file`). Em chuyển sang `saveImg=FALSE, attachUsrMeta=TRUE`: JPEG nằm trong user meta của frame, probe chỉ `memcpy`, còn thread `publisher` ghi file bằng file tạm + rename. Ghi lỗi chỉ làm event ra với `snapshot:null`, không làm sập engine."

---

## E. Dòng CV 4: "FastAPI + PostgreSQL backend, React dashboard, Docker Compose"

> Mở thêm: `interview/PHONG_VAN_DEEPSTREAM.md` mục 9-12.

**E1. Vì sao FastAPI và PostgreSQL?**

*Trả lời:* "FastAPI: validation bằng Pydantic, tài liệu OpenAPI tự sinh, dễ test với TestClient. PostgreSQL: em chọn vì nhiều ghi đồng thời (engine, UI, notifier), ràng buộc và `JSONB` tốt, và giống môi trường thật. **Thành thật:** SQLite cũng đủ cho một nhà; em chọn PostgreSQL để học và để mô hình gần với sản phẩm. Em dùng SQLAlchemy 2 đồng bộ + psycopg 3, endpoint viết `def` (FastAPI chạy trong threadpool), vì không có việc nào cần async thật sự và code đồng bộ dễ đọc, dễ debug hơn."

**E2. Schema và migration?**

*Trả lời:* "Alembic. Bảng chính: `cameras` (xóa mềm, địa chỉ lưu nguyên nhưng API luôn trả bản che mật khẩu), `events` (`event_id` UUID làm khóa chính, `notify_status`, tên file snapshot), `app_settings` (key/value JSONB). Test migration so khớp model bằng `compare_metadata` và chạy thử downgrade/upgrade."

**E3. Realtime cho trình duyệt hoạt động thế nào?**

*Trả lời:* "Một WebSocket `/api/ws`, một chiều server → client, tin nhắn dạng `{type, data}`: `event.created` (chỉ khi event mới, không gửi cho bản trùng), `event.updated`, `camera.status` (từ heartbeat của engine). Mỗi client có hàng đợi 100 tin; đầy thì cắt client đó (mã 1013) để một trình duyệt kẹt không làm chậm `POST /internal/events`. Server không replay nên client nối lại thì gọi REST lấy trạng thái hiện tại."

**E4. Heartbeat là gì?**

*Trả lời:* "Engine gửi định kỳ (mặc định 5 giây) một JSON liệt kê mọi camera của nó: trạng thái (RUNNING/RECONNECTING/ENDED), fps, tuổi frame cuối, số người đang trong khung. Backend giữ trong RAM (mỗi heartbeat thay cả cái trước), và coi camera là 'không báo' nếu heartbeat cũ hơn 15 giây. Heartbeat không vào spool và không thử lại: dữ liệu cũ vô nghĩa."

**E5. Video đi từ engine đến trình duyệt thế nào? Vì sao WebRTC?**

*Trả lời:* "Engine vẽ khung rồi mã hóa H.264 bằng NVENC, đẩy RTMP lên MediaMTX; MediaMTX phát WebRTC (giao thức WHEP) cho trình duyệt. Em chọn WebRTC vì độ trễ thấp (thường dưới 1 giây) so với HLS (vài giây). **Em chưa đo glass-to-glass latency**, chỉ xác nhận xem được trong Chrome ở 1920×1080, 29,8 fps, 0 frame rơi."

**E6. Nếu server RTMP/MediaMTX chết thì sao?**

*Trả lời:* "Detection, sự kiện và fps không bị ảnh hưởng. Mỗi camera có publisher RTMP riêng (pipeline nhỏ `appsrc → h264parse → flvmux → rtmp2sink`); lỗi thì dỡ và dựng lại sau 5 giây. Em thử tắt MediaMTX 8-9 giây rồi bật lại: fps detection giữ 30, stream tự về. Điểm mấu chốt là nhánh mã hóa trong pipeline chính không bao giờ lỗi nên encoder không phải khởi động lại."

**E7. Frontend: quản lý dữ liệu thế nào?**

*Trả lời:* "React 19 + TypeScript + Vite + TanStack Query. Số camera online và trạng thái gộp từ hai nguồn: REST `/status` poll 5 giây và `camera.status` đẩy qua WebSocket. Mỗi `event.created` chỉ gọi toast **đúng một lần cho mỗi `event_id`** (khử trùng ở client) và làm mới các query liên quan. Mất WebSocket thì hiện banner và quay lại dùng REST; nối lại thì refetch."

**E8. Bản demo trên GitHub Pages hoạt động thế nào khi không có backend?**

*Trả lời:* "Có một backend giả chạy ngay trong trình duyệt (`src/demo/server.ts`) cùng route và kiểu dữ liệu với API thật, với 5 camera và khoảng 100 sự kiện sinh bằng PRNG có hạt giống cố định. Cảnh quay là canvas vẽ người đi qua phòng, là hàm của (camera, thời gian) nên số người, hình trực tiếp và snapshot khớp nhau. Chỉ bật khi build `--mode demo`; bản thường cắt nhánh demo đi. Dải báo ở đầu trang nói rõ đây là camera giả. Mục đích: người xem bấm link là thấy app thật mà không cần GPU."

**E9. Docker Compose gồm những gì? Bảo mật thế nào?**

*Trả lời:* "Các service: `db` (postgres), `mediamtx`, `backend` (chạy `alembic upgrade head` khi khởi động), `engine` (DeepStream, cấp GPU qua `deploy.resources.reservations.devices`), `frontend` (nginx), thêm `backup` (`pg_dump` định kỳ, giữ 7 bản). Chỉ publish cổng web, WebRTC; backend, manager, RTMP, PostgreSQL không mở ra host. `/internal` trả 404 qua nginx. Mật khẩu DB và `INTERNAL_TOKEN` là bắt buộc (`${VAR:?}`) và không nằm trong image. Hệ thống **không có đăng nhập** vì chỉ chạy trong LAN (quyết định có chủ đích); nếu mở ra internet thì phải thêm."

**E10. Mật khẩu RTSP được xử lý ra sao?**

*Trả lời:* "Không để trong tham số dòng lệnh (lộ qua `ps`). Engine manager ghi vào file `cameras.ini` quyền 600 và engine từ chối file có quyền rộng hơn. API không bao giờ trả mật khẩu (che thành `rtsp://***@host/...`). Log của engine đi qua bộ lọc thay `user:pass@` bằng `***@`. Khi sửa camera, bỏ trống ô địa chỉ nghĩa là giữ địa chỉ cũ."

**E11. Em test những gì và test thế nào?**

*Trả lời:* "Bốn lớp: (1) C thuần: 29 test, chạy dưới ASan/UBSan, gồm vector chung với Python; (2) pytest ~290 test cho backend, manager, WebSocket, notifier... backend test tự khởi tạo cluster PostgreSQL tạm bằng `initdb`, không đụng DB thật; (3) vitest ~110 test cho logic frontend; (4) Playwright + axe cho end-to-end và trợ năng WCAG 2.1 A/AA, với backend thật + manager thật + engine giả. Ngoài ra có test engine thật trên GPU (reload cấu hình, thêm/bớt camera, phát video), mặc định bị skip và chỉ chạy khi đặt `RUN_GPU_TESTS=1`. Em còn kiểm test bằng *đột biến*: cố ý làm hỏng code để chắc test đỏ."

---

## F. Câu gài về tính trung thực của CV

> Mở thêm: `interview/PHONG_VAN_DEEPSTREAM.md` mục 15. Mục này quan trọng nhất; học kỹ.

**F1. "~20 stream" là camera thật à?**

*Trả lời:* "Không ạ, là **camera giả lập**: một server RTSP em tự viết phát lại cùng một file 1080p H.264 30 fps, mỗi camera là một phiên RTSP/giải mã riêng. CV ghi chữ 'simulated' và ghi chưa thử camera vật lý. Con số này cho thấy giới hạn của GPU với tải giải mã tương đương, nhưng camera thật có thể khác về codec, bitrate, độ phân giải và jitter."

**F2. Em đã thử với camera thật chưa?**

*Trả lời:* "Chưa. Em đã thử: camera RTSP giả, file video, và một luồng RTSP công cộng (H.264 1280×720) trong 60 giây: 1609 frame, 29-30 fps, 6 track, không reconnect. Chưa thử: Digest thật, `rtsps://`, H.265, mất mạng thật, jitter. Kế hoạch: dùng điện thoại làm camera IP để thử trước."

**F3. Độ chính xác của hệ thống là bao nhiêu?**

*Trả lời:* "Em chưa có con số mAP hay recall cho tập dữ liệu của mình, vì chưa gán nhãn ground truth. Em có một so sánh thô: ở ngưỡng 0.25, số người đếm được chênh khoảng 10% so với Ultralytics trên cùng video, chủ yếu ở các hộp sát ngưỡng; em chưa giải thích hết chênh lệch. Em không muốn công bố một con số 'độ chính xác' mà mình không đo."

**F4. Chạy liên tục bao lâu rồi? Có rò rỉ bộ nhớ không?**

*Trả lời:* "Soak 4 giờ với 4 camera: 0 restart, camera chậm nhất 29,6 fps, VRAM không tăng (còn giảm 6%), không có lỗi trong log. Nhưng **RAM của engine tăng từ 930 lên 1081 MiB (+16%, khoảng 55 MiB mỗi giờ)**, tốc độ có chậm lại ở cuối nhưng chưa phẳng. Em chưa kết luận được là rò rỉ hay cache chưa bão hòa. Em chưa chạy nhiều ngày. Hướng điều tra: `MALLOC_ARENA_MAX`/jemalloc, hoặc chạy lâu hơn xem có phẳng không."

**F5. Độ trễ từ camera đến trình duyệt là bao nhiêu?**

*Trả lời:* "Em chưa đo. Để đo glass-to-glass cần một đồng hồ hiển thị trong cảnh rồi so với thời gian trên màn hình. Em chỉ biết WebRTC thường dưới 1 giây; không có số của riêng em nên em không nói số."

**F6. Dự án này có dùng AI để viết code không?**

*Trả lời (sửa cho đúng thực tế của bạn trước khi nói):* "Có. Em dùng Claude Code như một người cùng lập trình cho nhiều phần. Phần em làm chủ: chọn kiến trúc và đánh đổi (một pipeline N camera, spool + idempotency), đọc và hiểu từng phần, chạy và đo trên GPU thật, quyết định giữ hay bỏ. Em có thể giải thích và chỉ ra code của từng phần; nếu anh/chị muốn, em mở code ra giải thích ngay." **Đừng nhận mọi thứ là tự viết.** Nếu có dòng nào trong CV mà bạn không giải thích được, hãy ôn hoặc bỏ dòng đó.

**F7. Hệ thống có an toàn không? Có đăng nhập không?**

*Trả lời:* "Chưa có đăng nhập, vì chỉ chạy trong LAN và em chọn không làm. Các lớp có sẵn: `/internal/*` có token, chỉ publish các cổng cần thiết, mật khẩu camera không lộ ra API hay log. Hạn chế đã biết: engine manager không xác thực (an toàn nhờ không publish cổng), endpoint test-connection của backend kết nối tới bất kỳ host nào được đưa vào (rủi ro SSRF) nên chỉ chấp nhận được trong LAN. Nếu mở ra internet, việc đầu tiên là đăng nhập + HTTPS."

**F8. Điểm yếu lớn nhất của dự án?**

*Trả lời:* "Ba điều: (1) chưa thử camera thật; (2) chưa đo độ chính xác của tracking, chỉ có so sánh thô; (3) lỗi của một nguồn **không phải RTSP** (file hỏng) vẫn làm cả pipeline thoát với mã 1. Em có thử giữ các nguồn khác chạy nhưng camera còn lại bị đói frame và engine treo khi tắt nên em bỏ hướng đó và chấp nhận."

---

## G. Ba câu chuyện thất bại (dùng cho câu "kể một lỗi khó")

> Khung STAR: **S**ituation → **T**ask → **A**ction → **R**esult. Thay bằng cách kể của riêng bạn nếu khác với thực tế. Mở thêm: `interview/PHONG_VAN_DEEPSTREAM.md` mục 17 (10 chuyện).

**G1. Segfault khi thư mục snapshot không ghi được.**

- **S:** Snapshot dùng hàm lưu JPEG có sẵn của NVIDIA (`saveImg=TRUE`). Khi em thử thư mục chỉ-đọc, cả engine sập.
- **T:** Tìm nguyên nhân, đảm bảo lỗi đĩa không giết hệ thống giám sát.
- **A:** Chạy lại dưới gdb: `fwrite` ← `jpeg_write_to_file` trong `libnvds_batch_jpegenc.so`, hàm này không kiểm tra `fopen`. Em chuyển sang để JPEG trong user meta, probe chỉ `memcpy`, và thread riêng ghi file bằng file tạm + rename. Thêm test thư mục chỉ-đọc.
- **R:** Thư mục chỉ-đọc không còn làm sập engine; event ra với `snapshot:null`.

**G2. Deadlock của `rtspclientsink`.**

- **S:** Em đẩy video có overlay lên MediaMTX bằng `rtspclientsink`. Sau lỗi kết nối, engine treo khi tắt.
- **T:** Làm cho việc phát video không bao giờ làm treo hay ảnh hưởng detection.
- **A:** gdb: thread chính chờ một mutex của `rtspclientsink` khi chuyển state NULL (GStreamer 1.20.3). Em thử vài cách (khởi động lại bin nhánh tại chỗ, xin lại pad của `nvstreamdemux`) đều thất bại: mất sticky event, pad trả NULL. Cuối cùng em đổi sang RTMP, mỗi camera một publisher riêng bằng pipeline nhỏ, dựng lại sau lỗi.
- **R:** MediaMTX tắt 8-9 giây rồi bật lại, fps detection giữ 30, stream tự về, engine thoát mã 0.

**G3. Cạn `buffer-pool-size` làm detection mọi camera dừng.**

- **S:** Khi một nhánh phát video bị kẹt, detection của **tất cả** camera dừng khoảng 6 giây.
- **T:** Hiểu vì sao một nhánh kẹt lại kéo cả pipeline.
- **A:** Nhận ra `nvstreammux` có pool buffer mặc định 4, và mỗi `queue` của nhánh giữ một batch buffer; hai queue × 4 buffer là cạn pool nên muxer không còn buffer để nạp. Em cho mỗi queue giữ 1 và đặt pool = `min(16, 4 + N)`; với nguồn RTSP (live) dùng `leaky=downstream` để nhánh chậm thì bỏ frame chứ không chặn.
- **R:** Detection các camera giữ 30 fps dù nhánh phát video gặp sự cố.

---

## H. Hai project nhỏ trong CV

> **Lưu ý:** mình không có dữ liệu về hai project này (CV còn chỗ trống `[N images / N classes]`, `[mAP@0.5]`). Phần dưới là câu hỏi hay gặp và *khung* trả lời; chỗ có `[điền]` bạn phải thay bằng sự thật của mình. Nếu không có số thì nói "em không nhớ chính xác / em chưa đo", đừng bịa.

### H1. Military and Civilian Vehicle Detection (YOLOv5)

**H1.1. Em làm gì trong project này?**
*Trả lời:* "Em huấn luyện YOLOv5 để phát hiện và phân loại xe quân sự và xe dân sự trong ảnh và video. Em chuẩn bị dữ liệu (`[điền: nguồn, số ảnh, số lớp]`), chia train/val, huấn luyện và đánh giá. Kết quả: `[điền: mAP@0.5 nếu có]`."

**H1.2. mAP@0.5 là gì?**
*Trả lời:* "Với mỗi lớp, vẽ đường precision–recall bằng cách quét ngưỡng điểm tin cậy; diện tích dưới đường đó là AP; mAP là trung bình AP các lớp. '@0.5' nghĩa là một dự đoán chỉ tính đúng khi IoU với hộp thật ≥ 0.5. mAP@0.5:0.95 chặt hơn: lấy trung bình ở nhiều ngưỡng IoU."

**H1.3. IoU là gì? NMS làm gì?**
*Trả lời:* "IoU = diện tích giao / diện tích hợp của hai hộp. NMS (non-maximum suppression): giữ hộp có điểm cao nhất, bỏ các hộp cùng lớp chồng lên nó quá ngưỡng IoU, lặp lại. Mục đích: một vật chỉ ra một hộp."

**H1.4. Dữ liệu mất cân bằng (ít xe quân sự hơn xe dân sự) thì sao?**
*Trả lời:* "Model có xu hướng thiên về lớp nhiều ví dụ hơn. Cách xử lý: thêm dữ liệu cho lớp ít, augmentation, trọng số lớp, và nhìn precision/recall *từng lớp* thay vì chỉ mAP tổng. `[điền: em đã làm gì thật]`."

**H1.5. Làm sao biết model bị overfit?**
*Trả lời:* "Loss train tiếp tục giảm mà mAP/loss trên tập validation ngừng cải thiện hoặc xấu đi. Cách giảm: thêm dữ liệu, augmentation, dừng sớm, model nhỏ hơn. Quan trọng: tập validation phải tách khỏi train *theo nguồn* (không để ảnh gần giống nhau ở cả hai bên) nếu không số đo bị lạc quan."

**H1.6. Vì sao YOLOv5 ở project này mà YOLOv8 ở project kia?**
*Trả lời:* "Project xe được làm trước và YOLOv5 có tài liệu và tài nguyên huấn luyện sẵn. Ở project giám sát em dùng YOLOv8n vì nhỏ, nhanh, và export ONNX dễ. Em hiểu khác biệt chính: YOLOv8 là anchor-free và không có đầu ra objectness riêng." (Nếu bạn có lý do khác thì thay.)

### H2. RAG Chatbot (LangChain + Hugging Face + FAISS + Ollama)

**H2.1. Mô tả luồng hoạt động.**
*Trả lời:* "Nạp PDF → tách thành đoạn (chunk) → tính embedding bằng model Hugging Face `[điền tên model]` → lưu vào FAISS. Khi có câu hỏi: embed câu hỏi → tìm top-k đoạn gần nhất → đưa các đoạn đó và câu hỏi vào prompt → LLM chạy local qua Ollama (`[điền tên model]`) sinh câu trả lời dựa trên ngữ cảnh."

**H2.2. Chunking: em chọn kích thước thế nào?**
*Trả lời:* "Chunk quá nhỏ thì mất ngữ cảnh, quá lớn thì loãng và tốn token. Thường chọn vài trăm token có chồng lấn (overlap) để câu không bị cắt đôi. `[điền: kích thước/overlap em dùng và vì sao]`. Cách chọn đúng là thử vài giá trị và so chất lượng truy xuất trên một bộ câu hỏi mẫu."

**H2.3. Làm sao giảm 'bịa' (hallucination)?**
*Trả lời:* "Prompt yêu cầu chỉ trả lời từ ngữ cảnh được cung cấp và nói 'không biết' nếu không có; trích nguồn đoạn văn; kiểm tra điểm tương đồng của đoạn truy xuất (nếu quá thấp thì từ chối); và dùng model đủ mạnh. Local LLM nhỏ vẫn có thể bịa nên không thể loại bỏ hoàn toàn."

**H2.4. Làm sao đánh giá chatbot tốt hay không?**
*Trả lời:* "Tách hai phần: *truy xuất* (đoạn đúng có nằm trong top-k không — recall@k) và *sinh câu trả lời* (đúng, bám ngữ cảnh). Cần một bộ câu hỏi + đáp án mẫu. `[điền: em đã đánh giá thế nào; nếu chưa thì nói 'em mới kiểm tra bằng tay một số câu']`."

**H2.5. FAISS là gì? Vì sao không dùng cơ sở dữ liệu vector khác?**
*Trả lời:* "Thư viện tìm kiếm láng giềng gần nhất trên vector, chạy trong bộ nhớ, rất nhanh với dữ liệu nhỏ đến vừa. Em chọn vì chạy local không cần dịch vụ riêng. Khi dữ liệu lớn, cần cập nhật liên tục hoặc nhiều người dùng thì cần vector DB có lưu trữ và lọc metadata (pgvector, Qdrant...)."

**H2.6. Vì sao dùng Ollama (LLM local)?**
*Trả lời:* "Dữ liệu không rời máy, không tốn phí API, chạy offline. Đánh đổi: chất lượng và tốc độ phụ thuộc phần cứng, model nhỏ thường kém model lớn."

---

## I. Kiến thức nền hay bị hỏi chen vào

> Mở thêm: `interview/PHONG_VAN_DEEPSTREAM.md` mục 16.

**I1. Batch inference tốt hơn chạy từng ảnh ở chỗ nào?**
*Trả lời:* "GPU rất song song nhưng mỗi lần phóng kernel và nạp trọng số có chi phí cố định; gộp nhiều ảnh thì chia chi phí đó và dùng GPU kín hơn, tăng thông lượng. Cái giá: tăng độ trễ vì phải chờ đủ batch."

**I2. FP32, FP16, INT8 khác gì?**
*Trả lời:* "Càng ít bit thì bộ nhớ và băng thông càng nhỏ, tensor core chạy nhanh hơn, nhưng sai số lượng tử hóa lớn hơn. FP16 thường gần như không mất độ chính xác; INT8 cần hiệu chỉnh (calibration) hoặc huấn luyện có nhận biết lượng tử hóa để giữ độ chính xác."

**I3. ONNX là gì? TensorRT làm gì?**
*Trả lời:* "ONNX là định dạng trao đổi model giữa các framework. TensorRT đọc ONNX rồi tối ưu cho đúng GPU đó (gộp layer, chọn kernel, chọn độ chính xác) và sinh file *engine* chạy nhanh. Engine gắn với GPU, phiên bản TensorRT và batch size nên không mang sang máy khác được."

**I4. RTSP và RTP khác gì?**
*Trả lời:* "RTSP là giao thức *điều khiển* (DESCRIBE, SETUP, PLAY) thương lượng phiên; dữ liệu video đi qua RTP (qua UDP hoặc xen kẽ trong TCP). SDP trả về mô tả codec và tham số, ví dụ SPS/PPS của H.264. Em tự viết kiểm tra kết nối RTSP: TCP connect → DESCRIBE → xử lý 401 bằng Digest hoặc Basic → đọc SDP để lấy codec và độ phân giải."

**I5. I-frame, P-frame, GOP là gì? Vì sao quan trọng khi đẩy luồng?**
*Trả lời:* "I-frame (key frame) giải mã độc lập; P/B-frame phụ thuộc frame khác. GOP là khoảng giữa hai key frame. Bắt đầu một luồng mới (hoặc nối lại) phải bắt đầu ở key frame, nếu không người xem thấy hình vỡ. Ở publisher RTMP em chờ key frame kế tiếp rồi mới bắt đầu gửi."

**I6. Tracking đa đối tượng hoạt động thế nào? ID switch là gì?**
*Trả lời:* "Mỗi frame: dự đoán vị trí track hiện có (Kalman filter hoặc tương quan), ghép với detection mới (Hungarian, theo IoU hoặc đặc trưng hình dạng), tạo track mới cho detection chưa ghép, xóa track mất lâu. ID switch là khi một người thật bị đổi ID (do che khuất, hai người cắt nhau). Với hệ thống của em, ID switch → sự kiện trùng, nên em có `absence_timeout` làm vùng đệm."

**I7. Backpressure là gì? Hệ thống em xử lý ra sao?**
*Trả lời:* "Khi bên tiêu thụ chậm hơn bên sản xuất thì phải chọn: chặn bên sản xuất, hoặc bỏ dữ liệu, hoặc đệm có giới hạn. Với video thời gian thực, em ưu tiên *không chặn video*: hàng đợi event có giới hạn rồi bỏ + đếm, queue của nhánh stream dùng `leaky=downstream` với nguồn live, WebSocket cắt client chậm."

**I8. Idempotency là gì?**
*Trả lời:* "Một thao tác thực hiện nhiều lần cho cùng kết quả như một lần. Giao hàng 'ít nhất một lần' (có thử lại) cộng với thao tác idempotent (khóa `event_id`) cho hiệu ứng 'đúng một lần'."

**I9. Thread-safety trong probe: em đảm bảo bằng cách nào?**
*Trả lời:* "Trạng thái cấu hình theo camera (`conf_threshold`, `absence_timeout`, `ai_enabled`) là `_Atomic`, main thread ghi và probe đọc. Mỗi `EventManager` chỉ một thread đụng vào. Khi thêm/gỡ camera lúc chạy, probe giữ `slot_lock` khi xử lý một batch và main thread giữ lock đó khi điền hoặc dọn ô, nên một camera không bị giải phóng giữa lúc probe đang dùng."

**I10. Thêm hoặc bớt camera khi engine đang chạy: em làm sao?**
*Trả lời:* "Engine có `--max-cameras` ô dự phòng; batch của muxer và `nvinfer` bằng số ô. Gửi `SIGHUP` thì engine đọc lại file camera và thêm camera vào ô trống hoặc gỡ camera đã rời file mà không restart, kèm chế độ 'tất cả hoặc không gì': file hỏng thì không đổi gì. Vì sao phải có ô sẵn: `nvstreamdemux` chỉ cho xin pad `src_N` lúc dựng pipeline (xin lúc đang chạy trả NULL). Vượt số ô thì manager restart với sức chứa lớn hơn."

---

## J. English Q&A

> Giữ câu ngắn, từ đơn giản. Nếu không hiểu câu hỏi, nói: "Sorry, could you repeat that more slowly?" hoặc "Do you mean ...?" — hoàn toàn bình thường.

**J1. Please introduce yourself and your main project.**
*Answer:* "I'm a fourth-year software engineering student at Thuy Loi University. I'm interested in computer vision and real-time video. My main project is a multi-camera person detection system. The engine is written in C on NVIDIA DeepStream. One GPU pipeline reads many RTSP or MP4 sources, runs YOLOv8 with TensorRT, and tracks people. Then it turns detections into events and sends them to a FastAPI backend with PostgreSQL. There is also a React dashboard with live video."

**J2. What was the hardest part?**
*Answer:* "Making event delivery reliable. A person stays in the frame for many seconds, so I must send one event, not thousands. And if the network or the backend is down, I must not lose events. I solved it with a de-duplication state machine, a retry queue with a disk spool, and idempotent inserts using a unique event ID."

**J3. Why one pipeline for many cameras instead of one process per camera?**
*Answer:* "I measured both with 8 cameras. One process used 1.2 GiB of RAM and 34 percent of one CPU core. Eight processes used 5.1 GiB and 95 percent. They both kept 30 fps, so I chose one shared pipeline."

**J4. What is the bottleneck of your system?**
*Answer:* "The hardware video decoder, not the neural network. At 24 cameras the decoder is at 99 percent while the GPU compute is lower. To add more cameras I would need another GPU or lower resolution or frame rate."

**J5. Have you tested with real cameras?**
*Answer:* "Not yet. I tested with simulated RTSP cameras, video files, and one short public RTSP stream. My next step is to use a phone as an IP camera and measure latency."

**J6. How do you know your tracking is good?**
*Answer:* "Honestly, I don't have a number yet. I compared two trackers on one clip and counted short tracks, but I have no ground truth to measure ID switches. I would label a short video by hand and measure it."

**J7. Tell me about a bug you fixed.**
*Answer:* "The engine crashed when the snapshot folder was read-only. I used gdb and found that an NVIDIA function did not check the result of `fopen`. I changed the design: the probe only copies the JPEG into memory, and a separate thread writes the file and handles errors. Now a bad folder only means an event without a snapshot."

**J8. What would you do next?**
*Answer:* "First, test with a real camera and measure end-to-end latency. Second, label some video and measure tracking accuracy. Third, find why the engine memory grows about 55 MiB per hour in my 4-hour test. After that, add login, because now the system is for a local network only."

**J9. Did you use AI tools to write this project?**
*Answer:* "Yes, I used Claude Code as a pair programmer. I made the design decisions, I read and understand the code, and I ran and measured everything on a real GPU. I can explain any part of it." (Chỉnh cho đúng với thực tế của bạn.)

---

## K. Câu hỏi để hỏi lại nhà tuyển dụng

1. Nhóm đang làm gì với video/camera: edge (Jetson) hay server GPU? Số camera và độ phân giải điển hình?
2. Stack suy luận hiện tại là gì (TensorRT, Triton, DeepStream, ONNX Runtime)? Có tối ưu INT8 không?
3. Thực tập sinh thường bắt đầu với loại việc nào trong 1-2 tháng đầu? Có người hướng dẫn trực tiếp không?
4. Nhóm đo chất lượng model thế nào (bộ dữ liệu đánh giá, chỉ số nào quan trọng nhất)?
5. Em nên chuẩn bị hoặc học thêm gì trước khi bắt đầu?

---

## L. Checklist cuối

- [ ] Nói được 1 phút giới thiệu (A1) và 5 con số: **30 fps; 16-20 camera ổn định, 24 sát trần; 1,2 so với 5,1 GiB RAM (8 camera); trần NVENC 12; soak 4 giờ RAM +16%**.
- [ ] Vẽ được pipeline (B1) lên giấy không nhìn.
- [ ] Giải thích được chuỗi: probe không chặn → hàng đợi → spool → `event_id` idempotent (D5-D7).
- [ ] Thuộc ba câu thất bại (G) và biết mở đúng file code khi bị hỏi (`engine/src/probes.c`, `delivery.c`, `core/event_manager.c`, `core/event_spool.c`).
- [ ] Trả lời thẳng được F1, F2, F3, F6 (CV có thật không, camera thật, độ chính xác, dùng AI).
- [ ] **Điền các `[điền]` ở mục H** bằng sự thật hoặc xóa dòng đó trong CV.
- [ ] Chạy lại `make -C engine test-core`, `pytest`, `npm test` để nói số test đúng.
- [ ] Mở demo `https://ngocluong0101.github.io/AI-Based-Home-Surveillance/` để chắc nó còn chạy trước khi gửi link.
