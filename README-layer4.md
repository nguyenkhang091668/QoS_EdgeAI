# Tầng 4 — máy trạng thái
Chạy:
.venv-layer1/bin/python layer4_state.py --browser --cooldown 2
Xem http://127.0.0.1:8765. Ctrl+C để dừng.
Bỏ --browser để dùng cửa sổ OpenCV; Q/Esc để thoát.
Trên Pi có Picamera2/OpenCV: python3 layer4_state.py --source picamera2

MayTrangThai_Cell4.update(valid_motion) trả True đúng một frame khi đủ 5
frame hợp lệ liên tiếp. Một frame False đặt bộ đếm về 0, không giảm 1.
Dùng đồng hồ monotonic; cooldown mặc định 2 giây, chỉnh bằng --cooldown.
Trong cooldown không tích lũy frame. Hết cooldown đếm lại từ frame hợp lệ đầu tiên.
Cooldown 0 vẫn cần chuỗi 5 frame mới cho mỗi lần kích hoạt.
Đầu vào là roi_ready của Tầng 3 (valid từ Tầng 2); chưa thêm điều kiện
chuyển động phải nằm trong ROI. Frame đầu tiên không được tính.
Giao diện hiển thị IDLE/COUNTING/TRIGGER/COOLDOWN, bộ đếm và tổng số lần kích hoạt.
TRIGGER được in ngay khi xảy ra; chưa gọi AI hay triển khai Tầng 5.

16 kiểm thử Tầng 2–4 đạt, gồm chuỗi ảnh tổng hợp xuyên pipeline:
.venv-layer1/bin/python -m unittest tests.test_layer2 tests.test_layer3 tests.test_layer4 -v
Camera thật vẫn chưa xác minh do macOS từ chối quyền.
