# Edge — pipeline 5 tầng và class camera

Bộ file được sao chép từ Desktop/Test; bản gốc và web server không bị sửa.
Các import nội bộ đã đổi để dùng được như package hoặc chạy script trực tiếp.

## Dùng trong hệ thống
Khi thư mục Desktop/Test/src nằm trong Python import path:

    from edge_orchestrator.Edge import EdgeAICamera
    camera = EdgeAICamera()
    packet = camera.process(frame)  # frame do web server đang đọc
    jpeg_bytes = packet.jpeg()
    event = packet.event
    camera.close()  # gọi khi dừng, không đặt trong mỗi vòng lặp

Model mặc định là yolov8n.pt trong chính thư mục Edge.
process(frame) không mở thêm USB camera; gọi trước khi vẽ timestamp lên frame.
Nếu class tự sở hữu camera, dùng with EdgeAICamera(camera_index=0) as camera,
sau đó packet = camera.read(). Không dùng song song hai chủ camera.

## Chạy độc lập từ Desktop/Test
    .venv-pipeline/bin/python src/edge_orchestrator/Edge/pipeline_edge_ai.py --browser

## Kiểm thử bản trong Edge
    PYTHONPATH=src .venv-pipeline/bin/python -m unittest discover -s src/edge_orchestrator/Edge/tests -v

## Thành phần
- layer1_motion.py đến layer5_ai.py: thuật toán từng tầng.
- pipeline_edge_ai.py: ghép pipeline.
- edge_ai_camera.py và __init__.py: giao diện camera/package.
- yolov8n.pt: model có sẵn.
- requirements-pipeline.txt: thư viện cần dùng; không kèm môi trường ảo.
- tests/: kiểm thử đã đổi import sang package Edge.
- README-edge-ai-camera.md, README-pipeline.md: tài liệu API/thuật toán gốc.
  Các lệnh ở hai tài liệu gốc dùng bố cục Desktop/Test; khi dùng bản đóng gói,
  ưu tiên các lệnh và import trong README này.

Không kèm file TFLite vì chưa có model đó. Chưa xác minh phần cứng Pi.
Event là dữ liệu trả về; không tự gửi alert, sửa QoS hay mở web server.
