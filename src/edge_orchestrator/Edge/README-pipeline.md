# Edge AI — pipeline 5 tầng

Chạy webcam trên Mac:
    cd ~/Desktop/Test
    .venv-pipeline/bin/python pipeline_edge_ai.py --browser

Mở http://127.0.0.1:8765 sau khi chương trình báo Preview.
Ctrl+C để dừng. Bỏ --browser để dùng OpenCV (Q/Esc thoát).
Camera macOS cần được cấp quyền; hiện vẫn chưa xác minh live camera.
Không lưu/đẩy frame lên mạng; preview chỉ bind 127.0.0.1.

## Luồng xử lý
1. Resize frame về 640x480 nếu cần, grayscale, absdiff, threshold 25, >500 pixel.
2. Median 3x3, opening 3x3, contour >=40, Laplacian variance >=100.
3. Crop màu và mask: Y=100:400, X=150:450, ROI 300x300.
4. Yêu cầu valid Tầng 2 VÀ contour >=40 trong ROI, đủ 5 frame liên tiếp.
   Frame không hợp lệ reset bộ đếm. Cooldown 2 giây, hết cooldown đếm lại.
5. Chỉ khi trigger mới chạy model trên ROI màu chưa vẽ nhãn; confidence mặc định 0.5.

Điểm thay đổi so với các script riêng: bản tích hợp chặn chuyển động ngoài ROI.
AI trả detected, score, class_id, label, inference_ms. Preview ghi LAST AI và
tuổi kết quả để không hiểu nhầm là dự đoán frame hiện tại. Frame đầu chỉ khởi tạo.
Inference hiện chạy đồng bộ; camera có thể giảm FPS trong lúc AI xử lý.
Thời gian cooldown tính từ lúc trigger, bao gồm thời gian inference.
Ảnh/video thử không chứng minh độ chính xác hoặc tốc độ trên Pi.

## Model
Mặc định dùng yolov8n.pt có sẵn, không cần thu thập dữ liệu để chạy thử.
PT dùng Ultralytics, CPU, input 320; score cao nhất trong detections sau NMS.
Không có bounding-box overlay vì mục tiêu hiện tại là xác nhận có đối tượng.

TFLite:
    .venv-pipeline/bin/python pipeline_edge_ai.py --model /path/model.tflite --classes 80 --browser
Hỗ trợ YOLOv8 detection raw, một input NHWC [1,H,W,3], một output
[1,4+C,N] hoặc [1,N,4+C]; không hỗ trợ embedded NMS, segmentation, pose.
Preprocess: letterbox, BGR→RGB, chia 255; quantize INT8/UINT8 theo scale/zero_point.
Output integer được dequantize trước khi so confidence. Per-axis I/O bị từ chối.
Với model tùy biến phải đặt --classes đúng số lớp; nhãn hiển thị class_<id>.
TFLite dùng max class probability để xác nhận có đối tượng; không vẽ bbox/NMS.
Chưa có file .tflite trong dự án nên chưa chạy inference TFLite thật.
Tham khảo:
https://ai.google.dev/edge/api/tflite/python/tf/lite/Interpreter
https://docs.ultralytics.com/modes/export/

## Video và Pi
Video: thêm --video /path/video.mp4 --headless để chạy không GUI.
Video được xử lý nhanh theo khả năng CPU, cooldown dùng thời gian thực, không theo timestamp video.
Pi Camera: --source picamera2 (cần Picamera2 của Raspberry Pi OS).
Môi trường macOS không sao chép sang Pi: cần tạo môi trường theo kiến trúc Pi và cài
runtime tương thích. Ưu tiên TFLite trên Pi sau khi có model và đo hiệu năng thực tế.

## Kiểm thử
    .venv-pipeline/bin/python -m unittest tests.test_layer2 tests.test_layer3 tests.test_layer4 tests.test_layer5 -v

Môi trường .venv-pipeline riêng, không phụ thuộc .venv cũ.
requirement.txt ở thư mục gốc là bộ dependency chung cho Edge và ứng dụng chính.
Các file requirements cũ đều trỏ về file chung (kể cả tên macos-lock, hiện không còn là lock riêng).
Cài từ thư mục gốc: python -m pip install -r requirement.txt
Bộ phiên bản lấy từ snapshot macOS; chưa xác minh khả năng cài trên Raspberry Pi.
Nội dung requirements trước khi gộp được lưu tại dependency-backups/requirements-before-merge.json.
Ngưỡng độ mờ 100, diện tích 40, motion 500 cần hiệu chỉnh theo cảnh thực tế.
