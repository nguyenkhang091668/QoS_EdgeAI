# Tầng 1 — phát hiện chuyển động

Chạy webcam trên Mac:

```sh
cd ~/Desktop/Test
.venv-layer1/bin/python layer1_motion.py --browser
```

Mở http://127.0.0.1:8765 để xem frame và mask. Ctrl+C để dừng.
Bỏ `--browser` để dùng cửa sổ OpenCV; Q/Esc để thoát.
`--camera 1` đổi camera; `--seconds 10` giới hạn thời gian chạy.

Luồng: BGR → grayscale → absdiff với frame trước → threshold 25 →
countNonZero → motion khi số pixel > 500. Frame đầu chỉ khởi tạo.
Ảnh được yêu cầu ở 640×480; kích thước thực được in trong terminal.
Không lưu video/ảnh vào đĩa. Browser chỉ phục vụ tại 127.0.0.1.
Chưa có lọc nhiễu, ROI, máy trạng thái hoặc AI (tầng 2–5).

Pi Camera V3 trên Raspberry Pi OS có Picamera2 và OpenCV:

```sh
python3 layer1_motion.py --source picamera2
```

Nếu dùng `--browser` trên Pi, truy cập từ trình duyệt trên chính Pi.
Picamera2 dùng RGB888 để trả mảng BGR phù hợp OpenCV:
https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf
Nhánh Pi Camera cần kiểm thử trên phần cứng Pi thực tế.

Môi trường `.venv-layer1` riêng tránh xung đột NumPy trong `.venv` cũ.
Tạo lại khi cần:

```sh
python3 -m venv .venv-layer1
.venv-layer1/bin/python -m pip install -r requirements-layer1.txt
```
