# Quay dữ liệu cho V2-4 — việc chỉ bạn làm được

Viết 2026-11-19. Dữ liệu xô xát công khai không dùng được và UR Fall chỉ có một phòng/một người, nên **tập test phải tự quay**.
Đây là việc quyết định số M1/M2/M4 có đáng tin hay không. Làm theo thứ tự dưới đây; mọi thứ sau khi quay đã có công cụ (xem mục 7).

## 1. Cần bao nhiêu (và vì sao)

Khoảng tin cậy rất rộng khi ít mẫu: 27/30 ngã bắt được → recall 90% nhưng khoảng 95% là **74-97%**; 5/5 chỉ cho 57-100%. Con số dưới đây
là mức tối thiểu để khoảng tin cậy đủ hẹp để nói được một câu; nhiều hơn thì tốt hơn.

| Thứ | Tối thiểu | Ghi chú |
|---|---|---|
| Người | **≥ 5** (test dùng ≥ 2 người **không** xuất hiện ở train/val) | gồm cả người cao/thấp, tóc/áo khác nhau |
| Cảnh (camera đặt ở đâu) | **≥ 3** (test dùng ≥ 1 cảnh **không** có ở train/val) | phòng khách, hành lang, phòng ngủ/bếp; đổi góc camera cũng tính là cảnh khác |
| Cú ngã | **≥ 40** (test ≥ 20) | mục 3 |
| Ca khó không phải ngã | **≥ 40 clip** | mục 3 |
| Xô xát diễn | **≥ 20 clip** (test ≥ 10) | mục 4; nói rõ "diễn, quy mô nhỏ" ở mọi số |
| Video không sự cố (cho M2) | **≥ 6 giờ** (test ≥ 3 giờ), nhiều buổi/nhiều ngày | mục 5 — **không lặp vòng** |

## 2. Thiết bị và cách đặt

- Camera cố định (điện thoại trên chân máy, hoặc webcam/camera IP có sẵn), **1920×1080, 30 fps, H.264** (cùng loại nguồn mà engine được đo M3).
- Đặt ở **cao 2-2,5 m, nghiêng xuống** như camera an ninh trong nhà; một vài clip đặt ngang tầm ngực để kiểm độ nhạy với góc.
- Ánh sáng ban ngày + một ít buổi tối bật đèn. Không dùng tính năng chống rung/tự zoom/tự crop của điện thoại.
- Đừng cắt ghép: một clip = một lần quay liền, **mỗi clip 30-120 s** cho clip có sự cố (đủ vào-ngã-nằm-đứng dậy, ít nhất 5 s nằm yên sau ngã), clip ngoài ra dài tùy ý.
- Tên file: `<cảnh>_<người>_<số>.mp4`, ví dụ `livingroom_ann_03.mp4`, để trong `datasets/own/clips/` (thư mục này không bao giờ vào git).

## 3. Té ngã và ca khó

An toàn trước hết: **có đệm/nệm dày, không ngã thật, không ai bị đau.** Ngã "diễn" (khuỵu/đổ người xuống nệm, tay chống nhẹ) là chấp nhận được:
model tư thế chỉ thấy khung xương; vẫn nên thử đủ hướng.

Cú ngã (tính vào M1, mỗi cú một nhãn `positive`; nên có cả các biến thể, mỗi loại ≥ 4):
- đang đứng ngã **ra trước / ra sau / sang bên**;
- đang đi rồi ngã (vấp);
- ngã từ ghế (ngồi mất thăng bằng);
- **khuỵu xuống từ từ rồi nằm** (ngã chậm — hay bị bỏ sót);
- ngã **bị che một phần** bởi bàn ghế (`case: occluded`), ngã chỉ thấy nửa người (`case: fall_half`);
- ngã khi ở xa camera (người cao < 150 px trong khung 1080p, `case: far`) — V2-2 cho thấy đây là chỗ pose yếu.
Ngã rồi tự đứng dậy trong < 2 s: nhãn `ambiguous` (không tính đúng/sai, xem DEFINITIONS 1.1).

Ca khó **không phải ngã** (clip riêng, `has_incident=false`, ghi tag trong `--tags`; ≥ 5 clip mỗi tag):
`sit_hard` (ngồi sụp xuống ghế/sofa), `lie_sofa` (nằm xuống sofa/giường có chủ ý), `pick_up` (cúi nhặt đồ sát sàn), `kneel` (quỳ, ngồi xổm),
`exercise` (yoga/hít đất/giãn cơ), `floor_play` (bò, chơi dưới sàn), `stumble` (vấp rồi đứng lại ngay).
Với giường/sofa khai báo `lie_zones` trong nhãn (đa giác theo pixel của clip) để kiểm "vùng được phép nằm".

## 4. Xô xát diễn

Chỉ người **đồng ý bằng văn bản**; dùng kỹ thuật "gần chạm": động tác đấm/đá/xô thật nhanh nhưng dừng cách người kia vài cm, không ai bị đau, có đệm/khoảng
trống phía sau. Không quay trẻ em, không quay người chưa đồng ý, không đóng kịch bạo lực nơi công cộng.
- ≥ 20 clip hai người (một vài clip ba người), mỗi clip 2-8 giây hành động liên tục ≥ 2 s (DEFINITIONS 1.2);
- ca khó **không phải xô xát** (≥ 5 clip mỗi tag): `hug` (ôm chặt), `playful_push` (đùa đẩy một lần), `arm_wrestle` (vật tay), `high_five`, `dance`, `grab_object` (giằng đồ nhẹ nhàng).

## 5. Video không sự cố cho M2

M2 là "báo giả/camera/giờ" — chỉ có nghĩa nếu video là **sinh hoạt bình thường thật, nhiều giờ khác nhau, không lặp**.
Cách rẻ nhất: đặt camera ghi 1-2 giờ liền trong lúc nhà có người đi lại, nấu ăn, xem TV, quét nhà, làm việc (người trong nhà đồng ý). Làm ở ≥ 3 cảnh,
≥ 3 buổi khác nhau. Một giờ cảnh trống không mấy giá trị (báo giả hiếm khi xảy ra khi không có người); **nhiều người đi lại** là cái cần.
Trước khi dùng phải **xem lướt toàn bộ** để chắc không có ngã/xô xát thật bị sót (nếu có, gán nhãn như clip có sự cố).

## 6. Đồng ý và quyền riêng tư

- Mỗi người xuất hiện ký (hoặc nhắn tin xác nhận) một dòng: đồng ý bị quay để làm dữ liệu đánh giá một project cá nhân, video chỉ lưu trên máy, không đăng lên mạng.
- Không đưa video vào git (`datasets/*` đã được gitignore). Nếu muốn trưng ảnh trong portfolio: chỉ ảnh của người cho phép rõ ràng, hoặc làm mờ mặt.
- Ghi vào `datasets/README.md` danh sách "người A, B, C... (đã đồng ý ngày ...)" — không ghi tên thật vào repo công khai.

## 7. Sau khi quay (công cụ đã có)

```bash
python=eval/.venv/bin/python
# 1. tạo nhãn trống cho từng clip, rồi điền events (xem new_label.py)
$python eval/e2e/new_label.py datasets/own/clips/livingroom_ann_03.mp4 --scene livingroom --person ann --split train
#    chia split THEO NGƯỜI VÀ CẢNH ngay từ lúc quay: người/cảnh dành cho test không bao giờ xuất hiện ở train/val
$python eval/e2e/check_labels.py                       # kiểm nhãn, in còn thiếu bao nhiêu so với mục tiêu của tập test
# 2. engine thật chạy pose trên từng clip (chậm, làm một lần mỗi clip; dùng nice)
$python eval/tools/make_pgie_config.py --base pgie_yolov8n_pose.txt --batch 1 --out eval/work/pgie_pose_b1.txt
$python eval/e2e/make_dumps.py --splits train,val
# 3. chạy luật trên dump và in M1/M2 (nhanh, thử ngưỡng bao nhiêu lần cũng được trên train/val)
$python eval/e2e/run_eval.py --splits val --tag baseline
# 4. khi đã ngừng chỉnh luật: khóa tập test, rồi đo MỘT lần
$python eval/e2e/lock_test.py
$python eval/e2e/make_dumps.py --splits test
$python eval/e2e/run_eval.py --splits test --final --tag baseline
```
`dev` = clip công khai (UR Fall) mà luật đã được chỉnh theo — in bảng cho vui, không bao giờ ghi vào `eval/results/`.

**Ghi nhãn thời gian:** mở clip bằng `mpv` (phím `.` và `,` bước từng khung) hoặc VLC; ghi giây trên timeline của clip. `impact_s` = khung đầu tiên hông/vai chạm sàn;
`start_s` = lúc bắt đầu mất thăng bằng; `end_s` = lúc bắt đầu đứng dậy hoặc hết clip. Sai số vài khung (±0,1 s) là chấp nhận được.
Nhờ có sẵn `impact_s` mà M4 đo được; một mình `start_s` thì không.

## 8. Việc còn lại sau khi có dữ liệu

- **M4 trên đường thật**: `eval/e2e/latency.py --clips <tên clip ngã, không đuôi> --repeats 3` (clip phải nằm ở `eval/work/urfall/`; sửa `trial()` nếu để ở `datasets/own/clips/`). Cần nhãn `impact_s` ghi tay từng khung (mốc UR Fall quá thô). Bước 3 ở trên chỉ cho độ trễ của luật.
- **M2 sau VLM** chờ V2-6.
- Đọc ≥ 20 ca sai, ghi nguyên nhân vào nhật ký, sửa luật, đo lại (ghi cả trước và sau).
