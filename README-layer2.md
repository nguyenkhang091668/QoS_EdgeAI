# Tầng 2
Chạy: .venv-layer1/bin/python layer2_motion.py --browser
Xem http://127.0.0.1:8765. Ctrl+C để dừng.
Bỏ --browser để dùng cửa sổ OpenCV (Q/Esc để thoát).
Pi Camera: python3 layer2_motion.py --source picamera2 trên Pi có Picamera2/OpenCV.

Mask: medianBlur 3x3 → MORPH_OPEN kernel vuông 3x3 → contourArea >= 40.
Giữ lỗ nền trong mask. Diện tích contour khác số pixel trắng.
Ảnh mờ khi phương sai Laplacian trên grayscale gốc < 100.
valid = motion Tầng 1 (>500 pixel) AND còn contour hợp lệ AND không mờ.
Không áp lại ngưỡng 500 lên mask sau lọc.
Luôn cập nhật frame trước, kể cả frame bị loại.
Hiển thị frame | mask thô | mask đã lọc cùng số pixel, contour và độ nét.
Ngưỡng độ nét 100 cần hiệu chỉnh theo camera/ánh sáng.
Chưa có tầng 3–5. Camera thật chưa kiểm thử do macOS từ chối quyền.

Kiểm thử: .venv-layer1/bin/python -m unittest tests.test_layer2 -v
