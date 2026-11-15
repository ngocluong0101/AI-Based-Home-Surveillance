# Tran Ngoc Luong

**Computer Vision / AI Engineer Intern**
0387398224 · ngocluongg0101@gmail.com · linkedin.com/in/ngocluog11/ · github.com/ngocluong0101 · Hà Nội

---

## Objective

Fourth-year Software Engineering student focused on computer vision and video analytics. I built a multi-camera person detection system on NVIDIA DeepStream (C, GStreamer, TensorRT) and ran it end to end with a backend, a live dashboard and Docker. I am looking for an AI / Computer Vision internship where I can work on real-time video pipelines and GPU inference with experienced engineers.

## Education

**Thuy Loi University** — Bachelor of Software Engineering, 2023 – Present
GPA: 3.52 / 4.0

## Projects

### AI-Based Home Surveillance — multi-camera person detection and alerting
*2/2026 – Present · github.com/ngocluong0101/AI-Based-Home-Surveillance · live demo with simulated cameras: ngocluong0101.github.io/AI-Based-Home-Surveillance*
**Stack:** C, NVIDIA DeepStream, GStreamer, TensorRT, YOLOv8, Python (FastAPI), PostgreSQL, React + TypeScript, Docker

- Built a **C engine on NVIDIA DeepStream** that runs N RTSP/MP4 cameras in one GPU pipeline (YOLOv8n TensorRT FP16 + NvDCF tracking). Sustained **30 fps per camera with ~20 simulated 1080p streams** on one RTX 4060 Laptop.
- Measured **one shared pipeline vs one process per camera** (8 cameras: 1.2 vs 5.1 GiB RAM) and found the hardware video decoder, not inference, is the bottleneck.
- Made **event delivery reliable**: per-camera de-duplication ported from Python to C and tested against shared vectors; events are retried and spooled to disk when the backend is down, with idempotent inserts.
- Built a **FastAPI + PostgreSQL backend** and a **React dashboard** with live video, deployed with Docker Compose. Tested with simulated cameras and a short public RTSP stream; not yet with a physical camera.

### Military and Civilian Vehicle Detection
*8/2026 · github.com/ngocluong0101/MilitaryAndCivilianVehicles*
**Stack:** Python, PyTorch, YOLOv5

- Trained a YOLOv5 model to detect and classify military and civilian vehicles in images and video.
- Prepared the dataset (**[N images / N classes]**) and split it into training and validation sets; reached **[mAP@0.5 = ?]** on the validation set.

### RAG Chatbot
*8/2026 · github.com/ngocluong0101/ChatBot*
**Stack:** Python, LangChain, Hugging Face, FAISS, Ollama

- Built a retrieval-augmented chatbot that answers questions from PDF documents: text chunking, Hugging Face embeddings, FAISS vector search.
- Ran a local LLM through Ollama to generate answers grounded in the retrieved context, with no external API.

## Achievements

- **Vingroup 20,000 Applied AI Talent Program (VinUniversity × Vingroup)**, 10/2026: passed the entrance competency assessment (Round 2) and qualified for Cohort V. Enrollment deferred to a later intake so I can finish my university coursework.

## Skills

| Area | Skills |
|---|---|
| Languages | Python, C, SQL, Java |
| Computer Vision | Object detection and tracking (YOLOv5/v8), OpenCV, ONNX, TensorRT |
| Video & GPU | NVIDIA DeepStream, GStreamer, RTSP |
| ML & LLM | PyTorch, scikit-learn, NumPy, Pandas, RAG (LangChain, FAISS) |
| Backend & Tools | FastAPI, PostgreSQL, Docker, Linux, Git |
| Spoken | English: basic (reading and listening) |

---
---

# Ghi chú cho bạn (xóa phần này khi xuất CV)

## 1. Mình đã đổi gì và vì sao

| Cũ | Mới | Lý do |
|---|---|---|
| Project 1 ghi "Python, YOLO, OpenCV, ByteTrack, Telegram" và "hỗ trợ webcam" | Engine C trên DeepStream, TensorRT, FastAPI, React, Docker | CV cũ mô tả bản Python **đã bị xóa**. Nếu phỏng vấn viên mở GitHub sẽ thấy lệch, và bạn tự bán rẻ phần giỏi nhất |
| Bullet mô tả việc ("Developed...", "Supported...") | Bullet có **số đo và quyết định kỹ thuật** | HR kỹ thuật tìm "đã đo gì, chọn gì, vì sao" chứ không tìm danh sách công nghệ |
| Objective chung chung ("hope to work with experienced engineers") | Nêu cụ thể mình đã làm gì và muốn làm gì | Objective cũ ai cũng viết được |
| Skills xếp lẫn lộn | Nhóm theo lĩnh vực, đặt DeepStream/TensorRT/GStreamer lên trước | Khớp với hướng Video/Edge AI trong bảng chủ đề của bạn |
| Không có link demo | Thêm link demo GitHub Pages | HR bấm là xem được ngay |

Mình **bỏ**: "Classification, Regression, Neural Networks" làm dòng riêng (gộp vào hàng ML), và giữ Java/MySQL vì có trong CV gốc.

## 2. Phải kiểm tra / điền trước khi gửi

- [ ] **Ngày tháng:** CV gốc ghi project 1 là 2/2026-8/2026 còn hai project kia 8/2026-8/2026. Mình đổi project 1 thành "Present" vì dự án còn phát triển đến tháng 11. Hãy sửa theo thời gian thật.
- [ ] **Mục Achievements (Nhân tài AI Thực chiến):** chỉ ghi "passed" và "qualified for", **không** ghi "trainee/participant" vì chưa nhập học, và **không** đặt ở Certifications vì chưa có chứng chỉ. Hãy có **email xác nhận bảo lưu bằng văn bản** (hoặc ít nhất xác nhận nhận được đơn bảo lưu) phòng khi nhà tuyển dụng hỏi. Khi nhập học, đổi thành "Enrolled in Cohort X (started MM/YYYY)"; khi học xong và có chứng chỉ thì mới chuyển sang Certifications.
- [ ] **Project 2 (xe quân sự/dân sự):** điền `[N images / N classes]` và `[mAP@0.5]` nếu bạn có số thật. **Nếu không có thì xóa cả dòng đó**, đừng bịa. Có thể thay bằng một câu mô tả đúng việc bạn làm (gán nhãn, augmentation...).
- [ ] **Project 3 (RAG):** mình chỉ viết lại từ chữ trong CV cũ. Chỉ giữ "Hugging Face embeddings" nếu đúng là bạn dùng embedding của Hugging Face. Nếu có số đo (số tài liệu, thời gian trả lời) thì thêm vào.
- [ ] **Email:** CV gốc ghi `ngocluongg0101@gmail.com` (hai chữ g). Mình giữ nguyên, hãy kiểm tra đúng chưa.
- [ ] **Số test (~290 pytest, ~110 vitest):** chạy lại `pytest`, `make test-core`, `npm test` trước khi gửi, vì số này thay đổi.
- [ ] **"~20 simulated 1080p streams":** đúng là camera **giả lập** (phát lại cùng một video). Mình đã ghi chữ "simulated" và câu cuối project 1 nói rõ chưa thử camera vật lý. **Giữ nguyên hai chỗ này.** Nói thật từ đầu an toàn hơn bị hỏi dồn ở phỏng vấn.

## 3. Những thứ KHÔNG nên đưa vào CV (vì chưa làm)

Không ghi: Triton, INT8, Jetson/ARM64, OpenVINO, Prometheus/Grafana, H.265, CUDA kernel, "độ chính xác X%", "độ trễ X ms", "chạy 24/7", "đã triển khai production". Bảng chủ đề trong `image.png` có nhiều mục này, nhưng dự án **chưa** làm. Nếu phỏng vấn hỏi, dùng phần "Kiến thức nền" trong `interview/PHONG_VAN_DEEPSTREAM.md` và nói rõ "em biết khái niệm, chưa làm".

## 4. Về việc dùng AI khi làm dự án

CV không cần ghi dòng này, nhưng **phải sẵn sàng trả lời thật** nếu bị hỏi (xem Q89 trong file phỏng vấn). Quy tắc đơn giản: mọi dòng trong CV phải là thứ bạn giải thích và chỉ được trong code. Nếu có bullet nào bạn chưa tự tin giải thích, hãy ôn nó trước hoặc bỏ bớt.

## 5. Hai phiên bản Objective để chọn

- **Bản trên (mặc định):** hợp tuyển vị trí Video Analytics / DeepStream / Edge AI.
- **Bản AI chung:** *"Fourth-year Software Engineering student with hands-on experience in object detection, multi-object tracking and retrieval-augmented generation. Built a real-time multi-camera detection system and a local-LLM document chatbot. Seeking an AI Engineer internship to apply and deepen these skills on real-world projects."*

## 6. Cách xuất ra file gửi đi

- CV intern nên **một trang**. Nếu dài hơn, cắt bớt bullet của project 2 và 3 trước, giữ project 1.
- Chuyển `.md` sang PDF bằng một trong các cách: dán vào Google Docs/Word rồi xuất PDF, hoặc dùng `pandoc CV_Tran_Ngoc_Luong.md -o CV.pdf`. Bảng Skills có thể đổi thành danh sách gạch đầu dòng nếu bố cục bị vỡ.
- Đặt tên file gọn: `TranNgocLuong_AIEngineerIntern.pdf`.
- Với mỗi công ty, chỉnh Objective và thứ tự Skills cho khớp mô tả công việc của họ (nhắc đúng từ khóa họ dùng, nếu bạn thật sự có kỹ năng đó).
