# Tầng 3 — ROI
Chạy từ Desktop/Test:
.venv-layer1/bin/python layer3_roi.py --browser
Mở http://127.0.0.1:8765. Ctrl+C để dừng.
Bỏ --browser để dùng cửa sổ OpenCV (Q/Esc thoát).
Trên Pi: python3 layer3_roi.py --source picamera2

ROI = frame[100:400, 150:450].copy(), kích thước 300x300.
Cắt cả ảnh màu gốc và mask đã qua Tầng 2, không vẽ nhãn vào dữ liệu ROI.
Frame khác kích thước được resize về 640x480 trước Tầng 1–3; có thể biến dạng
tỷ lệ nếu nguồn không phải 4:3. Tọa độ ROI thuộc ảnh sau resize.
Khung hiển thị kết thúc tại (449,399), đúng chỉ số cuối của lát cắt.
ROI/mask vẫn hiển thị để chẩn đoán khi Tầng 2 loại frame; roi_ready = valid Tầng 2.
roi_ready không đảm bảo chuyển động nằm trong ROI. roi_pixels cho biết số pixel
mask còn lại trong ROI. Tầng 3 chỉ cắt, chưa thêm điều kiện chặn theo vùng.
Không có tầng 4–5. Chưa xác minh webcam/Pi Camera thực tế do quyền camera macOS.

Kiểm thử:
.venv-layer1/bin/python -m unittest tests.test_layer2 tests.test_layer3 -v
