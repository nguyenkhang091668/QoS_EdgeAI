# EdgeAICamera — đóng gói camera + pipeline 5 tầng

File: edge_ai_camera.py. Không sửa web server, cấu hình, module mạng hay các tầng cũ.
Dùng lại pipeline_edge_ai.py và layer1_motion.py … layer5_ai.py; giữ chúng cùng thư mục
khi copy lên Pi. Đây là facade tái sử dụng các module, không phải file standalone.
Không cài thêm thư viện trong bước đóng gói này.

## Cách phù hợp với web server đã mở camera
Tạo một instance duy nhất bên trong camera_polling_thread, ngoài vòng lặp:

    from edge_ai_camera import EdgeAICamera
    ai_camera = EdgeAICamera(model_path="yolov8n.pt")
    try:
        while True:
            success, frame = cap.read()  # cap có sẵn của web server
            if not success:
                break
            packet = ai_camera.process(frame)  # TRƯỚC cv2.putText/timestamp
            frame = packet.frame
            jpeg_bytes = packet.jpeg()
            if packet.event is not None:
                print(packet.event)  # nối callback Event/Alert/QoS của bạn ở đây
    finally:
        ai_camera.close()

Ví dụ chỉ là đoạn để bạn chèn, không phải server hoàn chỉnh, không được chạy
thêm vòng đọc trên camera hiện có. Giữ phần mở/đóng cap, FFmpeg và telemetry
do hệ thống của bạn quản lý. process() KHÔNG mở/đóng camera cap đó.

## Hoặc để class sở hữu USB camera

    from edge_ai_camera import EdgeAICamera
    with EdgeAICamera(camera_index=0, model_path="yolov8n.pt") as camera:
        while True:
            packet = camera.read()
            jpeg_bytes = packet.jpeg()
            if packet.event:
                print(packet.event)

Chế độ này thay thế việc tự cv2.VideoCapture(0), không dùng song song hai chủ camera.
read() trả CameraPacket, KHÔNG phải tuple (ret, frame) của OpenCV.
Lỗi đọc/inference phát sinh exception và release camera do class sở hữu.
open() lặp không mở thêm camera. close()/release() lặp an toàn.
Import/khởi tạo class không tải model, không mở camera; tải model lúc open/process đầu.
Một instance sử dụng trong một producer thread; HTTP clients đọc kết quả do producer
phân phối, không tự gọi read(). Lock chỉ tuần tự hóa, không biến mỗi client thành subscriber.

## CameraPacket
- frame_id, captured_at: mã frame tăng dần trong lifetime instance, thời gian nhận frame.
- frame: BGR 640x480, không overlay, dùng stream hoặc jpeg().
- roi: BGR 300x300 tại Y=100:400, X=150:450.
- mask, roi_mask: mask đã lọc.
- metadata: trạng thái pipeline, consecutive, cooldown_remaining, ai_frame_id, ai_age.
- event: None nếu không inference ở frame này.
  Nếu inference: type=ai_result, frame_id, captured_at, detected, score, class_id,
  label, inference_ms và backend (nếu detector cung cấp).
Cả detected=True và False đều phát ai_result mới. Không có event không có nghĩa là
không có đối tượng. Không dùng lại metadata['ai'] để phát cảnh báo mới mỗi frame.
Event chỉ là dữ liệu; class không gửi cảnh báo, gọi SSH, chỉnh QoS hoặc tạo server.

## Cấu hình và hành vi
EdgeAICamera(model_path=..., camera_index=0, confidence=0.5, classes=80,
             threads=4, cooldown=2.0)
PT hoặc TFLite dùng Detector hiện có. Trên Pi cần runtime và model tương thích;
chưa kiểm chứng Pi/TFLite thật. Model mặc định nằm cạnh module (yolov8n.pt).
Đường dẫn model được truyền tương đối thì tính từ working directory của ứng dụng.
Ảnh kích thước khác được resize về 640x480, có thể thay đổi tỷ lệ hình.
Tầng 4 nhận motion hợp lệ có contour >=40 trong ROI; đủ 5 frame, cooldown 2s.
Inference đồng bộ nên read/process có thể chậm khi AI chạy.
Các ngưỡng còn lại giữ theo module hiện tại; không chỉnh cấu hình hệ thống.

## Kiểm thử
.venv-pipeline/bin/python -m unittest tests.test_edge_ai_camera -v
