# datasets/

Dữ liệu dùng để **đo** SafeGuard (ROADMAP_V2). Thư mục này **không bao giờ được commit** (xem `.gitignore`): license của hầu hết
các bộ dữ liệu cấm hoặc hạn chế phân phối lại. Ở đây chỉ giữ README này.

Cách tải và dựng lại: `eval/prepare_data.py` (tải COCO val2017 + MOT17, giải nén vào `datasets/coco`, `datasets/mot17`).
`datasets/_downloads/` là nơi chứa file zip gốc (xóa được sau khi đã giải nén).

## Đang dùng (V2-1)

| Bộ dữ liệu | Dùng để | License / điều khoản | Đã xác minh? |
|---|---|---|---|
| **COCO val2017** (+ `instances_val2017.json`) | AP của detector lớp `person` (PyTorch vs ONNX vs TensorRT) | Chưa đọc được văn bản điều khoản (trang cocodataset.org trả nội dung rỗng khi tải tự động). **Hãy tự đọc https://cocodataset.org/#termsofuse.** Theo trí nhớ: chú thích CC BY 4.0, ảnh theo license Flickr từng ảnh — *trí nhớ, chưa kiểm* | **Chưa** |
| **MOT17** (train: 7 chuỗi có ground truth) | MOTA / IDF1 / ID switch của tracker | "CC BY-NC-SA 3.0 ... you may not use this work for commercial purposes"; chuỗi lấy từ nguồn thứ ba có thể kèm điều kiện gốc. Tải trực tiếp, không form. Nguồn: https://motchallenge.net/data/MOT17/ | Có (2026-11-19) |

MOT17 là cảnh **đường phố**, không phải trong nhà: nó đo được tracker có chạy đúng không (ID switch, phân mảnh), nhưng **không** thay thế được
dữ liệu hành lang/phòng sinh hoạt của miền đích. Nhãn MOT cho cảnh trong nhà sẽ tự gán ở V2-4.

## Dự kiến cho V2-4 (chưa tải gì)

| Bộ dữ liệu | Nội dung liên quan | Điều khoản đã đọc (2026-11-19) | Hệ quả |
|---|---|---|---|
| **UR Fall Detection** (V2-5: cam0 RGB, all 30 fall + 40 ADL sequences, downloaded and used for training/CV; `datasets/urfall/_zips/` ≈ 4 GB can be deleted after `eval/train/prepare_urfall.py`) | 30 chuỗi ngã + 40 sinh hoạt thường (Kinect RGB+depth) | CC BY-NC-SA 4.0, "non-commercial academic use"; tải trực tiếp; yêu cầu trích dẫn Kwolek & Kępski 2014 | Dùng được cho đánh giá cá nhân; không đưa dữ liệu lên GitHub |
| **RWF-2000** | 2000 clip 5 s, bạo lực / không (camera giám sát) | "not allowed to be modified and redistributed" và "not allowed for commercial purpose" nếu không có phép của SMIIP Lab; **trang ghi video hiện không tải được** ("video files are currently not available") | Có thể phải xin phép tác giả hoặc bỏ |
| **NTU RGB+D / 120** | Có lớp ngã (A43), đấm (A50), đá (A51), đẩy (A52) | Chỉ cho nghiên cứu học thuật, **dự án cá nhân/độc lập không được chấp nhận**, phải dùng email tổ chức, duyệt tay; RGB 136 GB | **Loại**: không đủ điều kiện xin |
| **UBI-Fights** | Đánh nhau (video giám sát) | Chưa đọc được (máy chủ từ chối kết nối) | Chưa xác minh |
| **Le2i Fall** | Ngã trong nhà | Chưa kiểm (chưa có URL chính thức) | Chưa xác minh |
| **Real Life Violence Situations (Kaggle)** | Bạo lực / không | Chưa đọc được license trên trang Kaggle | Chưa xác minh |

Nguồn kiểm: UR Fall http://fenix.ur.edu.pl/mkepski/ds/uf.html · RWF-2000 https://github.com/mchengny/RWF2000-Video-Database-for-Violence-Detection ·
NTU https://rose1.ntu.edu.sg/dataset/actionRecognition/ · MOT17 https://motchallenge.net/data/MOT17/.

**Hệ quả quan trọng cho kế hoạch:** với dữ liệu xô xát, hai nguồn công khai lớn nhất hoặc không tải được (RWF-2000) hoặc không xin được (NTU RGB+D).
Phần lớn dữ liệu xô xát/ngã có thể phải **tự quay** (có người đồng ý, đóng kịch an toàn) — đã nêu ở V2-4, nay là điều chắc chắn hơn là tùy chọn.

Quy tắc chung: ghi nguồn + license mỗi khi thêm bộ dữ liệu; không đưa dữ liệu (kể cả một vài khung hình) vào repo hoặc trang demo; trích dẫn đúng bài báo
trong README khi công bố số liệu.
