# Ôn phỏng vấn AI Engineer — AI-Based-Home-Surveillance

> File này **không đưa lên GitHub** (thư mục `interview/` và `myCV/` đã nằm trong `.gitignore`).
> Mỗi câu có phần **Gợi ý trả lời** — hãy tự trả lời thành tiếng trước rồi mới đọc gợi ý.
> Đường dẫn file trong ngoặc giúp bạn mở đúng code khi được hỏi "em hãy chỉ cho anh/chị đoạn code đó".

## Mục lục

0. [Cách học](#0-cách-học)
1. [Tổng quan & kiến trúc](#1-tổng-quan--kiến-trúc)
2. [Object detection với YOLO](#2-object-detection-với-yolo)
3. [Multi-object tracking (ByteTrack)](#3-multi-object-tracking-bytetrack)
4. [Logic sự kiện PERSON_DETECTED](#4-logic-sự-kiện-person_detected)
5. [Xử lý video realtime, RTSP, threading](#5-xử-lý-video-realtime-rtsp-threading)
6. [Desktop GUI (Tkinter)](#6-desktop-gui-tkinter)
7. [Snapshot, Telegram, lưu trữ](#7-snapshot-telegram-lưu-trữ)
8. [Config, logging, triển khai](#8-config-logging-triển-khai)
9. [Testing](#9-testing)
10. [Điểm yếu tự nhận & cách cải tiến](#10-điểm-yếu-tự-nhận--cách-cải-tiến)
11. [Câu hỏi mở rộng cho AI Engineer](#11-câu-hỏi-mở-rộng-cho-ai-engineer)
12. [Live coding](#12-live-coding)
13. [Hành vi & kinh nghiệm cá nhân](#13-hành-vi--kinh-nghiệm-cá-nhân)
14. [Checklist trước buổi phỏng vấn](#14-checklist-trước-buổi-phỏng-vấn)

---

## 0. Cách học

- Đọc `CLAUDE.md` và chạy thử: `python main.py --source data/videos/test.mp4 --track`.
- Với mỗi mục, tự nói to câu trả lời trong 1–2 phút. Phỏng vấn kiểu "giải thích" cần nói trôi chứ không cần thuộc từng chữ.
- Phần 10 (điểm yếu) quan trọng nhất: nhà tuyển dụng rất thích ứng viên **tự nhận ra hạn chế của code mình** và nêu cách sửa.

---

## 1. Tổng quan & kiến trúc

**Q1. Giới thiệu ngắn gọn về project này.**
*Gợi ý:* Đây là pipeline giám sát nhà thông minh chạy trên video (file MP4, webcam, RTSP). Dùng YOLOv8 để phát hiện người, ByteTrack để gán ID cho từng người, và khi có người mới xuất hiện thì phát sự kiện `PERSON_DETECTED`. Sự kiện này lưu snapshot và/hoặc gửi cảnh báo qua Telegram. Có thêm GUI Tkinter để chọn nguồn, bật tracking, chỉnh confidence và xem danh sách sự kiện realtime.

**Q2. Luồng dữ liệu từ frame đầu vào đến cảnh báo gồm những bước nào?**
*Gợi ý:*
`cv2.VideoCapture.read()` → `PersonTracker.annotate_frame` → `model.track()` (YOLOv8 + ByteTrack) → lọc box có `id` → `PersonDetectedEventManager.process()` (lọc trùng) → nếu có event mới: ghi snapshot → gọi `event_callback` (GUI) → gửi Telegram → trả frame đã vẽ box về `play_video_source` để hiển thị.

**Q3. Vì sao tách `PersonDetector`, `PersonTracker`, `PersonDetectedEventManager` thành các lớp riêng?**
*Gợi ý:* Single responsibility và dễ test. `PersonDetectedEventManager` là logic thuần (không I/O, không OpenCV) nên test được ngay bằng `datetime` giả. `PersonTracker` chỉ ghép ba thành phần. Trong `test_gui_worker.py` và `test_person_tracker_events.py`, detector được thay bằng bản giả nên không cần tải YOLO.

**Q4. Dependency injection được dùng ở đâu? Vì sao?**
*Gợi ý:* `PipelineWorker` nhận `processor_factory` qua constructor (`src/gui/worker.py`). Mặc định là `default_processor_factory` nạp YOLO thật, còn test truyền factory trả về object giả. Tương tự `play_video_source` nhận `frame_processor`, `stop_event`, `on_frame` là tham số. Lợi ích: test không cần GPU/model/camera.

**Q5. Vì sao config tập trung ở `src/config/__init__.py` mà không đọc `os.environ` trực tiếp?**
*Gợi ý:* Single source of truth: kiểu dữ liệu được ép một lần (`_parse_float`, `_parse_bool`), có giá trị mặc định, dễ tìm khi debug và dễ thay đổi. Tránh việc cùng một biến được đọc ở 3 chỗ với 3 giá trị mặc định khác nhau.

**Q6. Vì sao có cả CLI và GUI, và GUI import lười (lazy import) để làm gì?**
*Gợi ý:* CLI phù hợp server/NAS chạy headless qua systemd/Docker. GUI cần tkinter và màn hình. `main.py` chỉ import `src.gui.app` khi có `--gui`, nên máy không có tkinter vẫn chạy CLI bình thường.

---

## 2. Object detection với YOLO

**Q7. YOLOv8 hoạt động thế nào ở mức khái niệm?**
*Gợi ý:* Là one-stage detector: một mạng CNN (backbone → neck kiểu FPN/PAN → head) dự đoán trực tiếp tọa độ box, objectness/class score trên nhiều scale trong một lần forward. Ultralytics YOLOv8 là anchor-free, dùng DFL để hồi quy box và task-aligned assigner khi huấn luyện. Khác với two-stage như Faster R-CNN (có bước đề xuất vùng trước).

**Q8. Vì sao chọn `yolov8n.pt`? Có đánh đổi gì?**
*Gợi ý:* Nano là model nhỏ nhất (vài triệu tham số), nhanh, chạy được trên CPU/máy yếu, phù hợp realtime. Đánh đổi: độ chính xác (mAP) thấp hơn `s/m/l/x`, dễ bỏ sót người nhỏ hoặc bị che khuất. Nếu cần chính xác hơn thì đổi `model_name` sang `yolov8s.pt` và đo lại FPS.

**Q9. Vì sao truyền `classes=[0]`?**
*Gợi ý:* COCO có 80 lớp, lớp 0 là `person`. Chỉ giữ lớp person để: (1) output nhẹ hơn, (2) không cảnh báo nhầm xe/chó/… (`src/detection/person_detector.py`).

**Q10. `confidence_threshold` ảnh hưởng thế nào?**
*Gợi ý:* Threshold cao → ít false positive nhưng dễ miss người (recall giảm). Threshold thấp → nhiều box rác, dễ gây event giả vì tracker có thêm track rác. Mặc định 0.5, chỉnh qua `--conf-threshold` hoặc thanh trên GUI. Muốn chọn đúng cần vẽ đường Precision-Recall trên tập có nhãn (xem Q57).

**Q11. NMS là gì và YOLOv8 có cần NMS không?**
*Gợi ý:* Non-Maximum Suppression: giữ box có score cao nhất trong nhóm box chồng nhau (IoU > ngưỡng), bỏ phần còn lại. Ultralytics thực hiện NMS bên trong `predict/track`. Lưu ý: đây là NMS truyền thống, không phải end-to-end như YOLOv10 (NMS-free).

**Q12. Ý nghĩa của `device = cuda:0 hoặc cpu` và cách kiểm tra?**
*Gợi ý:* `_resolve_device()` kiểm tra `torch.cuda.is_available()`. Nếu không có GPU hoặc chưa cài torch thì fallback về CPU. Kiểm tra nhanh: `python -c "import torch; print(torch.cuda.is_available())"`. Lưu ý: với CPU, FPS là điểm nghẽn chính, nên cần giảm độ phân giải đầu vào, đổi model nhỏ hơn, hoặc export ONNX/OpenVINO.

**Q13. Mô tả cách vẽ bounding box và label trong `_annotate_results`.**
*Gợi ý:* Lấy `box.xyxy` (tọa độ góc trên-trái và dưới-phải), `box.conf`, `box.id` nếu có. Vẽ bằng `cv2.rectangle` và `cv2.putText`. Code làm việc trên `frame.copy()` nên frame gốc không bị sửa, điều này quan trọng vì snapshot lấy frame gốc.

---

## 3. Multi-object tracking (ByteTrack)

**Q14. Tracking khác detection ở chỗ nào? Tại sao cần?**
*Gợi ý:* Detection trả box độc lập từng frame, không biết đây có phải cùng một người với frame trước không. Tracking gán ID ổn định qua thời gian. Nhờ có ID, hệ thống mới biết "người này mới xuất hiện" hay "người này đã có từ trước" để tránh cảnh báo lặp.

**Q15. ByteTrack hoạt động thế nào?**
*Gợi ý:*
- Dùng Kalman filter để dự đoán vị trí track ở frame kế tiếp.
- Ghép cặp theo IoU bằng thuật toán gán tối ưu (linear assignment), làm 2 vòng: vòng đầu ghép detection có score cao, vòng hai ghép phần detection score thấp còn lại với track chưa khớp.
- Ý tưởng chính: detection score thấp (thường là người bị che khuất) vẫn được dùng để giữ track, thay vì bỏ đi như SORT.
- Track không khớp được giữ trong một khoảng `track_buffer` frame (xem `bytetrack.yaml`) trước khi bị xoá.

**Q16. Vì sao gọi `model.track(..., persist=True)`?**
*Gợi ý:* `persist=True` giữ state của tracker giữa các lần gọi, tức là trạng thái ByteTrack được giữ qua các frame. Nếu không có nó, mỗi frame tracker bắt đầu lại từ đầu và ID sẽ không ổn định.

**Q17. ID switch là gì? Hệ thống này bị ảnh hưởng thế nào?**
*Gợi ý:* Một người vẫn ở đó nhưng được gán ID mới, thường do bị che khuất lâu hoặc ra khỏi khung hình rồi quay lại. Với project này, ID switch sẽ tạo **event trùng**: người cũ được coi là người mới. Đây là giới hạn đã biết (xem Q53).

**Q18. Làm sao đo chất lượng tracking?**
*Gợi ý:* Metric chuẩn MOT: MOTA (tổng hợp FP/FN/ID switch), IDF1 (độ đúng của danh tính), HOTA (cân bằng detection và association). Cần tập video có ground-truth ID. Project hiện **chưa có** bước đo này, đây là điều nên nói thẳng khi được hỏi.

---

## 4. Logic sự kiện PERSON_DETECTED

**Q19. Logic chống trùng lặp sự kiện hoạt động thế nào?**
*Gợi ý:* `PersonDetectedEventManager` lưu `last_seen` theo `track_id`. Một event được phát khi:
- `track_id` xuất hiện lần đầu, hoặc
- `track_id` đã vắng mặt lâu hơn `absence_timeout` (mặc định 10 giây).
Sau mỗi lần gọi, các track không còn xuất hiện và vắng quá lâu sẽ bị xoá để bộ nhớ không tăng mãi (`src/events/person_detected.py`).

**Q20. Vì sao dùng `dataclass(frozen=True)` cho `PersonDetectedEvent`?**
*Gợi ý:* Event là bản ghi bất biến: sau khi phát ra không ai được sửa, an toàn khi truyền qua queue giữa các thread trong GUI. Đây là thói quen tốt khi thiết kế value object.

**Q21. Vì sao `process()` nhận tham số `now` tuỳ chọn?**
*Gợi ý:* Để test được logic thời gian mà không phải `sleep`. Test truyền các mốc thời gian cố định. Đây cũng là chỗ có thể dẫn đến một lỗi thiết kế, xem Q52.

**Q22. Nếu một người đứng yên trước camera 1 giờ, có bao nhiêu event?**
*Gợi ý:* Một event khi xuất hiện, vì ID không đổi và `last_seen` được cập nhật mỗi frame. Nếu ByteTrack giữ ID ổn định thì không có event lặp.

**Q23. Nếu người đó đi ra rồi quay lại sau 30 giây thì sao?**
*Gợi ý:* Vắng mặt > `absence_timeout` (10 giây) nên phát event mới. Nếu quay lại sau 3 giây, không có event mới vì coi là cùng một lần xuất hiện. Đây là đánh đổi giữa "không spam" và "không bỏ sót".

---

## 5. Xử lý video realtime, RTSP, threading

**Q24. `play_video_source` xử lý kết nối lại RTSP như thế nào?**
*Gợi ý:* Vòng ngoài thử `cv2.VideoCapture` tới khi mở được; nếu không mở được thì đợi `RECONNECT_DELAY` (mặc định 5 giây) rồi thử lại. Vòng trong đọc frame; sau 5 lần đọc thất bại liên tiếp thì coi là mất kết nối, release capture và quay lại vòng ngoài. Lần đọc thành công thì reset bộ đếm.

**Q25. Vì sao phải có `stop_event` và `wait()` thay vì `time.sleep`?**
*Gợi ý:* `time.sleep(5)` sẽ khiến nút Stop của GUI phải chờ 5 giây. `stop_event.wait(seconds)` trả về ngay khi event được set, nên dừng nhanh.

**Q26. Xử lý SIGTERM như thế nào và vì sao?**
*Gợi ý:* `main.py` đăng ký handler biến SIGTERM thành `KeyboardInterrupt`. Nhờ đó `systemctl stop` hoặc `docker stop` sẽ đi qua khối `except KeyboardInterrupt` và `finally` để release camera và đóng cửa sổ, thay vì bị kill giữa frame. Chỉ `kill -9` mới không xử lý được.

**Q27. Vì sao `cv2.VideoCapture` phải release và vì sao cần `finally`?**
*Gợi ý:* Camera và file handle là tài nguyên hệ điều hành. Nếu không release, lần mở sau có thể thất bại vì device còn bị giữ. `finally` đảm bảo release dù có exception hay Ctrl+C.

**Q28. Điểm nghẽn của pipeline này là gì?**
*Gợi ý:* Thứ tự: (1) inference YOLO, thường nặng nhất, đặc biệt trên CPU; (2) decode video; (3) vẽ và encode. Và quan trọng: gửi Telegram đồng bộ trong vòng lặp video (xem Q50). Cách cải thiện: chạy inference mỗi N frame và nội suy giữa các frame, dùng batch, export sang ONNX/TensorRT, hoặc tách capture ra thread riêng.

**Q29. Với video file, xử lý nhanh hơn realtime thì có vấn đề gì không?**
*Gợi ý:* Có, và đây là lỗi thiết kế thật (xem Q52). Event manager dùng `datetime.now()` (wall clock). Nếu video 10 phút xử lý trong 1 phút thì `absence_timeout` tính theo thời gian thực, dẫn đến kết quả khác với video thật. Cách sửa: dùng timestamp của video (`frame_index / fps`).

**Q30. Vì sao GUI chạy pipeline ở thread nền, còn Tk ở main thread?**
*Gợi ý:* Tkinter không thread-safe: mọi thao tác widget phải chạy trên main thread. Nếu inference chạy trên main thread, UI sẽ đơ. Thread nền đẩy dữ liệu qua queue, còn main thread poll bằng `root.after(30, ...)`.

---

## 6. Desktop GUI (Tkinter)

**Q31. Vì sao `frames` là `Queue(maxsize=1)` còn `events` là queue không giới hạn?**
*Gợi ý:* Frame chỉ cần frame mới nhất. Nếu UI chậm mà queue đầy thì bỏ frame cũ (`_publish` lấy ra rồi đẩy vào), tránh backlog và tăng RAM. Sự kiện thì không được mất, nên dùng queue không giới hạn. Đây là ví dụ rõ về việc chọn chính sách queue theo ngữ nghĩa dữ liệu.

**Q32. Thread-safety của `set_confidence` có ổn không?**
*Gợi ý:* Hiện tại GUI ghi `self._confidence` còn worker đọc nó mỗi frame. Với Python và kiểu gán một float, GIL làm việc này không gây hỏng dữ liệu, nhưng về lý thuyết nên dùng `threading.Lock` hoặc `Event`/queue để rõ ràng. Đây là điểm nên nói khi được hỏi về concurrency.

**Q33. Khi đóng cửa sổ, làm sao đảm bảo thread nền dừng?**
*Gợi ý:* Đóng cửa sổ gọi `worker.stop(timeout=3)`: set `stop_event` và join tối đa 3 giây. Thread là daemon nên nếu không kịp thì tiến trình vẫn thoát được.

**Q34. `helpers.py` tách ra để làm gì?**
*Gợi ý:* Các hàm thuần như `classify_source`, `validate_source`, `format_event_row`, `frame_to_ppm` không phụ thuộc màn hình nên test được bằng pytest. `frame_to_ppm` encode ảnh sang PPM để `tk.PhotoImage` đọc trực tiếp, không cần Pillow.

---

## 7. Snapshot, Telegram, lưu trữ

**Q35. Snapshot được lưu từ frame nào: gốc hay đã vẽ box? Vì sao?**
*Gợi ý:* Frame gốc (trước khi vẽ), vì `annotate_frame` lưu snapshot trước khi gọi `_annotate_results`, và `_annotate_results` làm việc trên bản copy. Ảnh cảnh báo nên sạch để dùng làm bằng chứng.

**Q36. Tên file snapshot được đặt thế nào và có thể trùng không?**
*Gợi ý:* `PERSON_DETECTED_id{track_id}_{YYYYmmdd_HHMMSS}.jpg`. Trùng chỉ khi cùng track ID trong cùng một giây, mà ByteTrack không gán lại ID đó nên thực tế rất hiếm. Điểm yếu: nếu muốn chắc chắn thì thêm microseconds hoặc UUID.

**Q37. Dọn snapshot cũ hoạt động thế nào?**
*Gợi ý:* `SnapshotCleaner` glob `PERSON_DETECTED_*.jpg`, xoá file có `mtime` cũ hơn `SNAPSHOT_RETENTION_DAYS` (mặc định 7). Gọi mỗi `SNAPSHOT_CLEANUP_INTERVAL` (mặc định 100) lần lưu, không gọi mỗi frame để tránh quét đĩa liên tục. Lỗi xoá được log, không dừng pipeline.

**Q38. Vì sao Telegram notifier không raise exception?**
*Gợi ý:* Thông báo là phụ, không được làm gián đoạn giám sát. Mạng chập chờn không được làm dừng camera. Nên `send_*` bắt `RequestException`/`OSError`, log và trả `False`. Tương tự, `PersonTracker` bọc callback và Telegram trong try/except.

**Q39. Telegram notifier được cấu hình an toàn như thế nào?**
*Gợi ý:* Nếu thiếu token hoặc chat id hoặc `ENABLE_TELEGRAM=false` thì `self.enabled = False` và mọi hàm gửi là no-op. Token không hard-code mà nằm trong `.env` (đã gitignore).

---

## 8. Config, logging, triển khai

**Q40. Cách quản lý secrets trong project?**
*Gợi ý:* Token và chat id nằm trong `.env`, được `python-dotenv` nạp. `.env` nằm trong `.gitignore`, chỉ commit `.env.example` không có giá trị thật. Trong production nên dùng secret manager hoặc biến môi trường của container, không để trong image.

**Q41. Logging được cấu hình thế nào?**
*Gợi ý:* `setup_logging()` chỉ gọi một lần ở `main.py`, gắn `StreamHandler` (terminal) và `RotatingFileHandler` (`logs/app.log`, xoay theo `LOG_MAX_BYTES`, giữ `LOG_BACKUP_COUNT` file). Nếu không tạo được file log thì fallback về terminal, không crash. Các module khác chỉ dùng `logging.getLogger(__name__)`.

**Q42. Vì sao dùng `logging` thay vì `print`?**
*Gợi ý:* Có mức độ (INFO/WARNING/ERROR), có timestamp, ghi được ra file xoay vòng, và tắt/bật theo cấu hình mà không sửa code. Event `PERSON_DETECTED` cũng được log theo định dạng cố định để dễ grep.

**Q43. Triển khai project này lên server/NAS như thế nào?**
*Gợi ý:* Chạy `--headless` (không mở cửa sổ OpenCV) dưới systemd hoặc Docker, vì server không có màn hình. Có thể đăng ký `systemd` service với `Restart=on-failure`. Vì có handler SIGTERM nên `stop` tắt sạch. Lưu ý: image Docker nên dùng bản OpenCV headless (`opencv-python-headless`) và cần GPU passthrough nếu dùng CUDA.

**Q44. Vì sao `--headless` không cho thoát bằng phím `q`?**
*Gợi ý:* Phím `q` đến từ `cv2.waitKey` trên cửa sổ OpenCV, mà headless thì không có cửa sổ. Với file thì thoát khi hết video, với webcam/RTSP thì dùng Ctrl+C hoặc SIGTERM.

**Q45. Có nên đưa model YOLO vào image Docker không?**
*Gợi ý:* Model `.pt` được ultralytics tự tải nếu thiếu, nhưng tải lúc chạy làm container khởi động chậm và phụ thuộc mạng. Nên tải trước khi build image hoặc mount volume. `*.pt` đang nằm trong `.gitignore` nên không đẩy file nặng lên git.

---

## 9. Testing

**Q46. Chiến lược test của project?**
*Gợi ý:* Pytest, chạy bằng video tổng hợp do fixture `sample_video` tạo ra, không cần camera/YOLO/mạng/màn hình. Các test tập trung vào logic: event manager, `play_video_source` với `stop_event`/`on_frame`, hàm thuần của GUI, `PipelineWorker` và `PersonTracker` (thay detector bằng bản giả). Lớp Tk chưa có unit test, kiểm tra bằng cách chạy thật.

**Q47. Vì sao không test YOLO thật trong unit test?**
*Gợi ý:* Chậm, cần tải weights, cần GPU để nhanh, và kết quả phụ thuộc model nên test không ổn định. Unit test nên kiểm tra logic của *code mình viết*. Việc đánh giá model là bài toán khác, dùng tập dữ liệu có nhãn và metric riêng.

**Q48. Nếu phải viết test cho `PersonDetectedEventManager` thì những case nào?**
*Gợi ý:* (1) Track mới → có event. (2) Track đang active → không event. (3) Vắng đúng bằng timeout → không event (kiểm tra ranh giới `>`). (4) Vắng quá timeout → có event mới. (5) Track cũ bị dọn khi vắng lâu. (6) Nhiều track cùng lúc.

**Q49. Mock và fake khác nhau thế nào? Bạn dùng loại nào?**
*Gợi ý:* Fake là implementation đơn giản chạy được (ví dụ detector trả box cố định). Mock kiểm tra số lần gọi/tham số. Project dùng fake vì kiểm tra được output, dễ đọc hơn.

---

## 10. Điểm yếu tự nhận & cách cải tiến

> Đây là phần nên học kỹ nhất. Khi được hỏi "Có gì bạn muốn làm tốt hơn?", hãy chọn 2–3 mục dưới đây.

**Q50. Gửi Telegram đồng bộ trong vòng lặp video có vấn đề gì?**
*Gợi ý:* `sendPhoto` có timeout 10 giây và chạy trên thread xử lý video. Nếu mạng chậm, cả pipeline đứng lại, không xử lý frame và có thể bỏ lỡ người khác. Cách sửa: đẩy việc gửi vào queue và worker thread riêng (producer-consumer), có giới hạn kích thước queue và retry với backoff.

**Q51. `TelegramNotifier()` được tạo mỗi lần có event thì sao?**
*Gợi ý:* Trong `annotate_frame`, notifier được khởi tạo mỗi khi có batch event mới. Không phải lỗi nghiêm trọng nhưng lãng phí và lặp logic. Nên tạo một lần trong `__init__` của `PersonTracker`.

**Q52. Dùng `datetime.now()` cho absence timeout có vấn đề gì với video file?**
*Gợi ý:* Như Q29: thời gian xử lý không bằng thời gian video. Nên truyền timestamp theo video (`frame_idx / fps`) hoặc `time.monotonic()` cho nguồn realtime. Đây là điều tôi nên sửa trước.

**Q53. ID switch của ByteTrack gây ra gì và sửa thế nào?**
*Gợi ý:* Gây event trùng. Có thể giảm bằng: (1) tăng `track_buffer`, (2) yêu cầu track tồn tại tối thiểu N frame mới phát event (tránh nhiễu), (3) kết hợp re-ID bằng embedding (như OSNet/DeepSORT). Đây là hướng nâng cấp rõ ràng cho AI Engineer.

**Q54. Hàm `annotate_frame` trong `PersonTracker` đang làm quá nhiều việc. Bạn sẽ tách thế nào?**
*Gợi ý:* Hiện một hàm làm: inference, lọc track, cập nhật event, lưu snapshot, gọi callback, gửi Telegram, vẽ. Nên tách thành pipeline các bước hoặc dùng observer/handler: `EventDispatcher` nhận event rồi gọi các handler (`SnapshotHandler`, `TelegramHandler`, `GuiHandler`). Dễ test và dễ thêm kênh mới (email, MQTT).

**Q55. Trong `play_video_source`, nhánh file và nhánh RTSP lặp lại nhiều code. Có bug nào không?**
*Gợi ý:* Có. Overlay FPS chỉ được vẽ khi có `frame_processor` (nằm trong `if frame_processor is not None:` ở nhánh file), nên khi không có processor thì không có FPS. Hai nhánh cũng lặp code vẽ FPS, `on_frame`, `imshow`. Nên trích thành hàm `_emit_frame` dùng chung và bỏ điều kiện thừa.

**Q56. `PersonDetector` có thể tăng tốc inference trên GPU thế nào?**
*Gợi ý:* Thử `half=True` (FP16) khi chạy trên CUDA để giảm VRAM và tăng tốc, rồi đo lại mAP vì FP16 có thể lệch nhẹ. Ngoài ra có thể export TensorRT engine cho GPU NVIDIA. Luôn đo trước và sau để có số liệu.

**Q57. Chưa có đánh giá định lượng nào. Bạn sẽ đánh giá thế nào?**
*Gợi ý:* Gán nhãn một tập video (CVAT, Label Studio, Roboflow), chạy model, đo: precision, recall, mAP@0.5 và mAP@0.5:0.95 cho detection; MOTA/IDF1 cho tracking; số event đúng/sai/trùng cho toàn hệ thống. Vẽ đường PR để chọn confidence threshold, không chọn theo cảm tính.

**Q58. Nếu `.env` có giá trị sai kiểu (ví dụ `CONFIDENCE_THRESHOLD=abc`) thì sao?**
*Gợi ý:* Parser ép kiểu với giá trị mặc định nên sai kiểu dữ liệu sẽ âm thầm dùng mặc định, khó debug. Nên validate và log cảnh báo khi giá trị không parse được. (Đây là đánh đổi của `_parse_*` hiện tại.)

---

## 11. Câu hỏi mở rộng cho AI Engineer

### 11.1 Model & inference

**Q59. Làm sao tăng FPS khi chạy trên CPU hoặc thiết bị edge (Raspberry Pi, Jetson)?**
*Gợi ý:* Theo thứ tự hiệu quả thường thấy: giảm input size (`imgsz=320`), dùng model nhỏ hơn, export sang ONNX + ONNX Runtime/OpenVINO, quantize INT8, dùng TensorRT trên Jetson, chạy inference mỗi N frame, và giảm FPS capture.

**Q60. Quantization là gì? Khác gì giữa post-training và quantization-aware training?**
*Gợi ý:* Quantization giảm độ chính xác số (FP32 → FP16 → INT8) để giảm bộ nhớ và tăng tốc. PTQ quantize sau khi train, nhanh nhưng mất chính xác hơn. QAT mô phỏng lượng tử hoá khi train, chính xác hơn nhưng tốn công.

**Q61. ONNX là gì và vì sao cần?**
*Gợi ý:* Định dạng trung gian để chạy model trên nhiều runtime khác framework (ONNX Runtime, TensorRT, OpenVINO). Ultralytics có `model.export(format="onnx")`.

**Q62. Batch inference có giúp không khi xử lý nhiều camera?**
*Gợi ý:* Có, GPU hiệu quả hơn khi xử lý batch frame từ nhiều camera cùng lúc. Đánh đổi là độ trễ tăng và phải đồng bộ frame giữa các luồng.

**Q63. Làm sao biết model đã đủ nhanh cho realtime?**
*Gợi ý:* Đo latency p50/p95/p99 của cả pipeline (không chỉ inference), đo FPS sau khi đã vẽ và encode, đo CPU/GPU/RAM dài hạn để xem rò rỉ bộ nhớ. Ngưỡng tuỳ bài toán: camera 15 FPS thì xử lý 15 FPS là đủ.

### 11.2 Dữ liệu & huấn luyện

**Q64. Nếu camera nhà bạn có góc nhìn khác với dữ liệu COCO, bạn làm gì?**
*Gợi ý:* Thu thập frame thật từ camera, gán nhãn, fine-tune YOLO từ checkpoint COCO (transfer learning). Dùng augmentation (đổi sáng, lật, mosaic). Giữ tập validation tách biệt theo thời gian/camera để tránh data leakage.

**Q65. Data leakage là gì? Ví dụ trong video?**
*Gợi ý:* Frame liền kề gần như giống hệt nhau. Nếu chia train/val ngẫu nhiên theo frame, val sẽ giống train và điểm bị đẹp giả. Nên chia theo video hoặc theo khoảng thời gian.

**Q66. Class imbalance trong bài toán phát hiện người ở nhà thì thế nào?**
*Gợi ý:* Phần lớn frame không có người nên nhiều negative. Với object detection, focal loss và hard negative mining xử lý phần này. Khi đánh giá dùng precision/recall thay vì accuracy.

**Q67. Làm sao gán nhãn hiệu quả?**
*Gợi ý:* Dùng model hiện tại để gợi ý nhãn (pre-label), người chỉ sửa. Active learning: ưu tiên gán nhãn các frame model không chắc chắn. Kiểm tra chéo giữa người gán nhãn.

### 11.3 GenAI / LLM / VLM

**Q68. Nếu thêm mô tả ngữ cảnh bằng VLM (ví dụ "người đang mang bao lớn"), bạn sẽ thiết kế thế nào?**
*Gợi ý:* Không gọi VLM trên mọi frame vì tốn chi phí và chậm. Chỉ gọi khi có event `PERSON_DETECTED`, gửi snapshot (không phải video) kèm prompt yêu cầu mô tả ngắn và định dạng JSON. Cache kết quả theo event id. Có timeout và fallback về cảnh báo không mô tả nếu VLM lỗi. Vì đây là bước không đồng bộ nên gửi vào queue như Q50.

**Q69. Vì sao không nên để VLM quyết định có cảnh báo hay không?**
*Gợi ý:* LLM/VLM có thể hallucinate và không ổn định. Quyết định an ninh nên dựa trên detector có metric đo được, còn VLM chỉ bổ sung mô tả cho người xem. Đây là nguyên tắc "deterministic gate, generative enrich".

**Q70. Làm sao hỏi đáp trên lịch sử sự kiện bằng ngôn ngữ tự nhiên ("hôm qua có ai vào sau 22h không")?**
*Gợi ý:* Lưu event vào DB (có timestamp, track id, snapshot path, mô tả). Dùng text-to-SQL cho câu hỏi có cấu trúc (thời gian, số lượng). Với mô tả tự do, dùng RAG: embed mô tả, lưu vector DB, truy xuất rồi nhờ LLM tổng hợp. Luôn hiển thị nguồn (event id, snapshot) để kiểm chứng.

**Q71. Prompt injection có liên quan đến project này không?**
*Gợi ý:* Có nếu thêm VLM/LLM: mô tả trong snapshot hoặc văn bản từ camera có thể chứa chỉ dẫn độc hại. Không để LLM có quyền hành động (mở khoá, tắt báo động) và sanitize output trước khi hiển thị.

**Q72. Bạn sẽ đo chất lượng một hệ thống có LLM như thế nào?**
*Gợi ý:* Tập eval cố định có đáp án mong đợi, LLM-as-judge kèm kiểm tra con người, đo tỉ lệ đúng định dạng, độ trễ và chi phí mỗi request. Chạy eval mỗi khi đổi prompt hoặc model (regression test).

### 11.4 MLOps & vận hành

**Q73. Làm sao phát hiện model drift khi triển khai thật?**
*Gợi ý:* Theo dõi phân phối đầu vào (độ sáng, góc camera, số lượng detection/giờ), phân phối confidence, và tỉ lệ event được người dùng đánh dấu là sai. Thay đổi lớn là dấu hiệu cần đánh giá lại hoặc fine-tune.

**Q74. Quản lý phiên bản model và cấu hình thế nào?**
*Gợi ý:* Lưu weights với version trong registry (MLflow, DVC, hoặc đơn giản là thư mục có tên version + hash), ghi `model_name`, threshold và commit hash vào log mỗi lần chạy để tái lập được kết quả.

**Q75. Làm sao giám sát hệ thống chạy 24/7?**
*Gợi ý:* Metric: FPS, latency, số lần RTSP reconnect, số lỗi Telegram, dung lượng đĩa snapshot, nhiệt độ/GPU. Dùng health check (heartbeat file hoặc endpoint), alert khi pipeline không có frame trong N giây. Đây là chỗ hiện project còn thiếu.

**Q76. Nếu có 50 camera thì kiến trúc thay đổi thế nào?**
*Gợi ý:* Tách mỗi camera thành một worker/process (tránh GIL), hoặc một inference server (Triton, TorchServe) xử lý batch từ nhiều camera. Message bus (MQTT, Kafka, Redis) để gửi event. Lưu event vào DB tập trung, snapshot lên object storage (S3/MinIO). Dashboard đọc từ DB.

**Q77. Bảo mật và quyền riêng tư của dữ liệu camera?**
*Gợi ý:* Mã hoá RTSP (rtsps), không để mật khẩu camera trong code, giới hạn quyền truy cập snapshot, đặt thời gian lưu (đã có `SNAPSHOT_RETENTION_DAYS`), tuân thủ luật địa phương về quay phim nơi công cộng và gia đình, thông báo cho người được quay nếu cần.

### 11.5 System design

**Q78. Thiết kế hệ thống cảnh báo đáng tin cậy (không mất cảnh báo khi mạng rớt).**
*Gợi ý:* Outbox pattern: ghi event vào local DB/file trước, rồi một sender đọc và gửi, đánh dấu đã gửi khi thành công. Retry với exponential backoff, idempotency key là event id để tránh gửi trùng. Heartbeat để biết kênh đang sống.

**Q79. Làm sao giảm chi phí lưu trữ video?**
*Gợi ý:* Không lưu toàn bộ video, chỉ lưu clip quanh event (pre/post buffer vài giây). Dùng codec H.264/H.265 nếu lưu video. Snapshot nén JPEG chất lượng vừa phải. Đặt lifecycle policy trên object storage.

**Q80. Nếu phải đảm bảo độ trễ dưới 1 giây từ lúc người bước vào đến lúc có thông báo?**
*Gợi ý:* Phân tích từng bước: buffer RTSP (giảm buffer size của capture), thời gian inference, gửi ảnh qua mạng. Chạy inference trên edge để tránh upload video. Gửi text trước, ảnh sau (như `send_person_detected` rồi `sendPhoto`).

---

## 12. Live coding

**Q81. Viết hàm tính IoU giữa hai box `(x1, y1, x2, y2)`.**
```python
def iou(a, b):
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0
```
*Lưu ý:* Kiểm tra trường hợp không giao nhau (`max(0, ...)`) và chia cho 0.

**Q82. Viết NMS đơn giản.**
```python
def nms(boxes, scores, thr=0.5):
    order = sorted(range(len(boxes)), key=lambda i: scores[i], reverse=True)
    keep = []
    while order:
        i = order.pop(0)
        keep.append(i)
        order = [j for j in order if iou(boxes[i], boxes[j]) <= thr]
    return keep
```
*Lưu ý:* Độ phức tạp O(n²), nói được điều này và đề xuất vectorize bằng numpy hoặc dùng `torchvision.ops.nms`.

**Q83. Viết lại `PersonDetectedEventManager.process` để dùng timestamp của video thay vì wall clock.**
*Gợi ý:* Thay `now` bằng `frame_idx / fps` ở nơi gọi, giữ nguyên logic so sánh `(current - last_seen).total_seconds() > timeout`. Điểm cần nói: logic không đổi, chỉ đổi nguồn thời gian; đây là lý do tách `now` thành tham số.

**Q84. Viết producer-consumer dùng queue để gửi Telegram ở thread riêng.**
```python
import queue, threading

def start_sender(notifier, maxsize=100):
    q = queue.Queue(maxsize=maxsize)

    def worker():
        while True:
            item = q.get()
            if item is None:
                break
            event, photo = item
            if photo:
                notifier.send_person_detected_photo(event, str(photo))
            else:
                notifier.send_person_detected(event)
            q.task_done()

    threading.Thread(target=worker, daemon=True).start()
    return q
```
*Lưu ý:* Nói về chính sách khi queue đầy (`put_nowait` và bỏ, hoặc chặn), vì đây là lựa chọn có chủ đích.

**Q85. Viết decorator đo thời gian chạy hàm.**
```python
import functools, time, logging

def timed(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            logging.getLogger(__name__).debug("%s took %.1f ms", fn.__name__, (time.perf_counter() - start) * 1000)
    return wrapper
```

**Q86. Tìm số lần xuất hiện liên tiếp dài nhất của một giá trị trong list (hay gặp trong xử lý chuỗi frame).**
```python
def longest_run(xs, value):
    best = cur = 0
    for x in xs:
        cur = cur + 1 if x == value else 0
        best = max(best, cur)
    return best
```

---

## 13. Hành vi & kinh nghiệm cá nhân

**Q87. Kể về một bug khó bạn đã gặp khi làm project này.**
*Gợi ý:* Chọn một bug thật, ví dụ: GUI bị đơ khi chạy model (vì inference trên main thread), hoặc Tk gọi từ thread nền gây lỗi. Trình bày theo STAR: Situation, Task, Action (đọc log, tách thread, dùng queue), Result (UI mượt, test được). Chuẩn bị đúng 1–2 câu chuyện như vậy.

**Q88. Nếu thời hạn gấp và bạn phải bỏ một tính năng, bạn bỏ gì?**
*Gợi ý:* Nói về ưu tiên: giữ phần ảnh hưởng an toàn (phát hiện, không mất cảnh báo), bỏ phần làm đẹp (GUI, overlay FPS). Giải thích tại sao.

**Q89. Vì sao bạn chọn YOLO + ByteTrack thay vì model khác?**
*Gợi ý:* Ultralytics dễ dùng, có pretrained COCO sẵn cho người, tracking tích hợp, cộng đồng lớn và có export nhiều định dạng. ByteTrack đơn giản, nhanh, không cần model re-ID riêng nên phù hợp CPU. Đánh đổi là ID switch khi bị che khuất nhiều.

**Q90. Bạn học AI Engineer như thế nào và đã làm gì ngoài project này?**
*Gợi ý:* Trả lời theo sự thật của bạn. Chuẩn bị một lộ trình rõ ràng: học gì, làm gì, đo kết quả thế nào.

**Q91. Bạn muốn làm việc gì tiếp theo trong project này?**
*Gợi ý:* Gợi ý thực tế: (1) đưa event dispatch ra queue, (2) đo MOT metrics, (3) sửa absence timeout theo timestamp video, (4) thêm re-ID để giảm event trùng, (5) export ONNX và đo FPS trên CPU.

---

## 14. Checklist trước buổi phỏng vấn

- [ ] Tự giải thích được luồng dữ liệu (Q2) trong 1 phút, không nhìn ghi chú.
- [ ] Chỉ được tên file/hàm cho: NMS, tracking, event dedupe, RTSP reconnect, SIGTERM, queue GUI.
- [ ] Nói được 3 điểm yếu và cách sửa (Q50, Q52, Q53).
- [ ] Viết được IoU và NMS trên giấy (Q81, Q82).
- [ ] Có 1–2 câu chuyện STAR về bug thật (Q87).
- [ ] Chạy được demo: `python main.py --gui --source data/videos/test.mp4 --track`.
- [ ] Biết đo FPS và số liệu thật để nói (không nói "chạy nhanh" mà không có số).
- [ ] Hỏi lại người phỏng vấn về bài toán thực tế của họ để thể hiện tư duy sản phẩm.
