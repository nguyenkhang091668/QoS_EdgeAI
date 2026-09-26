from edge_orchestrator.core.config import SIMULATE_TELEMETRY
from edge_orchestrator.network.qos_telemetry import QoSTelemetryWorker
import os
import sys
import time
import cv2
import threading
import subprocess
from flask import Flask, Response, render_template, jsonify
from edge_orchestrator.Edge import EdgeAICamera


# Đảm bảo python tìm thấy package
sys.path.append(os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', '..', '..')))

app = Flask(__name__)

# --- OpenCV Camera Setup ---
latest_frame = None


def start_ffmpeg_stream():
    """Tự động chạy ffmpeg đẩy luồng RTP cho CAM04 (Tailscale)."""
    # Đợi vài giây để OpenCV kịp mở Camera trước, tranh chấp tài nguyên trên macOS
    time.sleep(3)
    cmd = [
        "ffmpeg", "-y",
        "-f", "avfoundation",
        "-pixel_format", "uyvy422",
        "-framerate", "30",
        "-video_size", "640x480",
        "-i", "0",
        "-vcodec", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-pkt_size", "1200",
        "-f", "rtp",
        "rtp://100.108.25.63:8006"
    ]
    print("[INFO] Đang khởi chạy tự động FFMPEG stream cho CAM04...")
    try:
        # Chạy ẩn FFMPEG dưới nền (tắt stderr/stdout để khỏi rác log)
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("[INFO] FFMPEG stream đã chạy ngầm thành công!")
    except Exception as e:
        print(f"[ERROR] Không thể chạy FFMPEG: {e}")

def camera_polling_thread():
    """Luồng độc lập chuyên đọc dữ liệu từ Camera để tránh xung đột Thread trên macOS."""
    global latest_frame
    cap = None
    ai_camera = None
    try:
        cap = cv2.VideoCapture(0)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        if not cap.isOpened():
            print("[ERROR] Camera failed to initialize!")
            return
        print("[INFO] USB Camera initialized successfully in background thread.")
        
        # Gọi luồng phụ khởi chạy ffmpeg sau khi cv2 đã mở Camera
        threading.Thread(target=start_ffmpeg_stream, daemon=True).start()

        # Một instance cho toàn bộ vòng đời luồng; dùng camera đã mở ở trên.
        ai_camera = EdgeAICamera()

        while True:
            success, frame = cap.read()
            if success:
                # Xử lý frame gốc trước khi vẽ timestamp; không mở camera thứ hai.
                packet = ai_camera.process(frame)
                frame = packet.frame
                if packet.event is not None:
                    print(f"[EDGE AI] {packet.event}")

                # Thêm timestamp lên hình ảnh (OSD)
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                cv2.putText(frame, timestamp, (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

                # Nén hình ảnh sang định dạng .jpg (chất lượng 80%)
                encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 80]
                ret, buffer = cv2.imencode('.jpg', frame, encode_param)
                if ret:
                    latest_frame = buffer.tobytes()
            else:
                time.sleep(0.1)
    except Exception as e:
        print(f"[ERROR] Camera Thread Error: {e}")
    finally:
        if ai_camera is not None:
            ai_camera.close()
        if cap is not None:
            cap.release()


def generate_mjpeg_stream():
    """Lấy frame từ biến toàn cục và stream qua HTTP."""
    while True:
        if latest_frame is not None:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + latest_frame + b'\r\n')
            time.sleep(0.03)  # Giới hạn ~30 FPS để không ngốn CPU
        else:
            time.sleep(0.1)


# --- Telemetry Data Store ---
latest_telemetry = []


def update_telemetry_data(metrics_list):
    """Callback nhận dữ liệu từ QoSTelemetryWorker và cập nhật biến toàn cục."""
    global latest_telemetry
    latest_telemetry = metrics_list

# --- Flask Routes ---


@app.route('/')
def index():
    """Giao diện Dashboard chính (HTML/CSS/JS)."""
    return render_template('index.html')


@app.route('/video_feed')
def video_feed():
    """Endpoint trả về luồng video MJPEG."""
    return Response(generate_mjpeg_stream(), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/api/telemetry')
def api_telemetry():
    """Endpoint trả về dữ liệu QoS dạng JSON cho Frontend."""
    return jsonify(latest_telemetry)


def qos_polling_thread():
    """Luồng độc lập để chạy get_qos_metrics mà không cần PyQt Event Loop."""
    global latest_telemetry
    print(
        f"[INFO] Khởi động QoS Telemetry Worker (Simulate={SIMULATE_TELEMETRY})...")
    worker = QoSTelemetryWorker(simulate=SIMULATE_TELEMETRY)

    while True:
        try:
            if SIMULATE_TELEMETRY:
                time.sleep(1.0)
                # Sinh dữ liệu giả (không dùng get_qos_metrics để tránh sudo)
                metrics = []
                import random
                from edge_orchestrator.core.config import TELEMETRY_PORTS
                for port, info in TELEMETRY_PORTS.items():
                    metrics.append({
                        "ID": info["ID"],
                        "IP": info["IP"],
                        "Port": int(port),
                        "Bitrate_Mbps": round(random.uniform(0.5, 1.2), 2),
                        "Loss_Percent": 0.0,
                        "Jitter_ms": round(random.uniform(1.0, 4.0), 1),
                        "Timestamp": time.strftime("%H:%M:%S")
                    })
                latest_telemetry = metrics
            else:
                # get_qos_metrics sẽ tự block 2 giây (duration:2 của tshark)
                metrics = worker.get_qos_metrics()
                latest_telemetry = metrics
                time.sleep(0.1)
        except Exception as e:
            print(f"[ERROR] QoS Polling Thread: {e}")
            time.sleep(2)


if __name__ == '__main__':
    if not SIMULATE_TELEMETRY:
        print("[INFO] Yêu cầu quyền sudo để chạy tshark thu thập QoS...")
        subprocess.run(["sudo", "-v"], check=True)

    # 1. Khởi chạy luồng Telemetry chạy ngầm (Sử dụng chuẩn Threading)
    t_qos = threading.Thread(target=qos_polling_thread, daemon=True)
    t_qos.start()

    # 2. Khởi chạy luồng Camera chạy ngầm
    t_cam = threading.Thread(target=camera_polling_thread, daemon=True)
    t_cam.start()

    # 3. Khởi chạy Web Server
    print("[INFO] Khởi chạy Webserver Central Dashboard tại http://0.0.0.0:5001 ...")
    app.run(host='0.0.0.0', port=5001, threaded=True)
