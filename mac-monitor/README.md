# Mac Monitor

Dashboard giám sát hệ thống Edge AI chạy trên Raspberry Pi.

Pi xử lý camera, AI và điều khiển QoS. Mac nhận video RTP,
lấy trạng thái Pi qua SSH và hiển thị dashboard chỉ đọc.

## Yêu cầu

- Python 3 và FFmpeg trên Mac.
- Kết nối SSH tới pi@100.103.159.73.
- Ứng dụng Pi đã chạy API giám sát và bộ phát RTP CAM03.

## Khởi chạy

Từ thư mục gốc repository, chạy: `bash mac-monitor/start.command`.

Nhập mật khẩu SSH tại Terminal nếu được hỏi và giữ Terminal mở.
Dashboard mở tại http://127.0.0.1:8787/.

Chỉ khởi chạy một phiên giám sát; đóng phiên Mac cũ và ffplay
đang nhận CAM03 trước khi mở phiên mới.

## Kết nối

- Web trên Mac: 127.0.0.1:8787.
- API Pi: 127.0.0.1:8765, chuyển tiếp qua SSH tới Mac:18765.
- Video CAM03: RTP/H.264, UDP8004 trên Mac.

## Dữ liệu

Hiển thị video, trạng thái AI, QoS R1/R2 và dữ liệu SNMP thiết bị.
Frame xem trực tiếp được thay thế trong RAM, không lưu ảnh liên tục.

## Kiểm tra

Từ thư mục gốc repository: `/usr/bin/python3 mac-monitor/doctor.py`.
