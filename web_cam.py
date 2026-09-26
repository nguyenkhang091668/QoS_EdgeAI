# pyrefly: ignore [missing-import]
from flask import Flask, Response, render_template_string

import cv2, time

app = Flask(__name__)

# Khởi tạo USB Webcam
camera = cv2.VideoCapture(0)
camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480) 

def generate_mjpeg_stream():
    """Đọc từng frame từ USB Camera, nén JPEG và stream liên tục."""
    while True:
        success, frame = camera.read()
        if not success:
            time.sleep(0.1)
            continue
        # Nén hình ảnh sang định dạng .jpg (chất lượng 80%)
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 80]
        ret, buffer = cv2.imencode('.jpg', frame, encode_param)
        if not ret:
            continue
        frame_bytes = buffer.tobytes()
        # Trả về chuỗi multipart HTTP
        yield (b'--frame\r\n' + b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

@app.route('/video_feed')
def video_feed():
    """Endpoint trả về luồng video thực tế."""
    return Response(generate_mjpeg_stream(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/')
def index():
    """Giao diện Web hiển thị Camera trên Macbook."""
    html_code = """
        <div>
            <h2>GIÁM SÁT USB WEBCAM (CAM 04)</h2>
            <div>● LIVE STREAMING</div>
        </div>
    """
    return render_template_string(html_code)

if __name__ == '__main__':
    # Chạy Webserver trên IP 0.0.0.0, Port 5000 để Macbook truy cập được qua LAN/Tailscale
    print(" Khởi chạy Webserver Camera tại http://0.0.0.0:5001 ...")
    app.run(host='0.0.0.0', port=5001, threaded=True)
