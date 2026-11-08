"""Tang 1: frame differencing only; webcam or Pi Camera via Picamera2."""
import argparse
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np
if __package__:
    from .usb_camera import open_capture
else:
    from usb_camera import open_capture


def detect_motion(previous_gray, gray):
    diff = cv2.absdiff(gray, previous_gray)
    _, mask = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
    changed_pixels = cv2.countNonZero(mask)
    return mask, changed_pixels, changed_pixels > 500


class Camera:
    def __init__(self, source, index):
        self.pi = source == 'picamera2'
        if self.pi:
            from picamera2 import Picamera2
            from libcamera import Transform

            self.device = Picamera2(index)
            try:
                self.device.configure(
                    self.device.create_video_configuration(
                        main={
                            "size": (640, 480),
                            "format": "RGB888",
                        },
                        buffer_count=4,
                        controls={
                            # Khóa chu kỳ phơi sáng 20–24 FPS (41.6–50ms): đủ sáng kho & không tụt về 7–8 FPS
                            "FrameDurationLimits": (41666, 50000),
                        },
                        transform=Transform(
                            hflip=True,
                            vflip=True,
                        ),
                    )
                )
                self.device.start()
            except Exception:
                self.device.close()
                raise
        else:
            self.device = open_capture(index, v4l2=source == 'usb')

    def read(self):
        if self.pi:
            return self.device.capture_array('main')
        ok, frame = self.device.read()
        if not ok or frame is None:
            raise RuntimeError('Cannot read camera frame; camera disconnected or unavailable.')
        return frame

    def close(self):
        if self.pi:
            self.device.stop()
            self.device.close()
        else:
            self.device.release()


class Preview:
    def __init__(self, port, layer2=False, layer3=False, layer4=False, layer5=False):
        self.jpeg = None
        self.lock = threading.Lock()
        preview = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/':
                    body = '''<!doctype html><meta charset="utf-8">
<title>Edge AI - Tang 1</title>
<style>body{background:#131923;color:#eef;font:18px system-ui;margin:28px}
img{width:100%;max-width:1280px}p{color:#cbd5e1}</style>
<h1>Tầng 1 · Phát hiện chuyển động</h1>
<p>Trái: frame camera · Phải: mask nhị phân | Ngưỡng 25 | Motion khi &gt; 500 pixel</p>
<img id="preview" alt="Frame camera và mask">
<p id="state">Đang kết nối camera…</p>
<script>const img=document.getElementById('preview');
const state=document.getElementById('state');
img.onload=()=>{state.textContent='Đang nhận frame trực tiếp';setTimeout(update,100)};
img.onerror=()=>{state.textContent='Chưa nhận được frame / camera đã dừng';setTimeout(update,1000)};
function update(){img.src='/frame.jpg?t='+Date.now()}update();</script>'''
                    if layer2:
                        body = body.replace('Edge AI - Tang 1', 'Edge AI - Tang 2').replace(
                            'Tầng 1 · Phát hiện chuyển động', 'Tầng 2 · Lọc nhiễu và kiểm tra độ mờ').replace(
                            'Trái: frame camera · Phải: mask nhị phân | Ngưỡng 25 | Motion khi &gt; 500 pixel',
                            'Trái: frame · Giữa: mask Tầng 1 · Phải: mask sau lọc | Median 3×3 → Open 3×3 → Contour ≥ 40 | Laplacian ≥ 100')
                    if layer3:
                        body = body.replace('Edge AI - Tang 1', 'Edge AI - Tang 3').replace(
                            'Tầng 1 · Phát hiện chuyển động', 'Tầng 3 · Cắt vùng quan tâm').replace(
                            'Trái: frame camera · Phải: mask nhị phân | Ngưỡng 25 | Motion khi &gt; 500 pixel',
                            'Trái: frame 640×480 và khung ROI | Giữa: ROI 300×300 | Phải: mask ROI sau lọc | X=150:450, Y=100:400')
                    if layer4:
                        body = body.replace('Edge AI - Tang 3', 'Edge AI - Tang 4').replace(
                            'Tầng 3 · Cắt vùng quan tâm', 'Tầng 4 · Máy trạng thái chuyển động')
                    if layer5:
                        body = body.replace('Edge AI - Tang 3', 'Edge AI - Complete pipeline').replace(
                            'Tầng 3 · Cắt vùng quan tâm', 'Edge AI · Pipeline 5 tầng')
                    body = body.encode()
                    mime = 'text/html; charset=utf-8'
                elif self.path.split('?')[0] == '/frame.jpg':
                    with preview.lock:
                        body = preview.jpeg
                    if body is None:
                        self.send_error(503, 'Waiting for camera')
                        return
                    mime = 'image/jpeg'
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def update(self, frame):
        ok, jpeg = cv2.imencode('.jpg', frame)
        if ok:
            with self.lock:
                self.jpeg = jpeg.tobytes()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=['webcam', 'picamera2'], default='webcam')
    parser.add_argument('--camera', type=int, default=0)
    parser.add_argument('--browser', action='store_true', help='Local browser preview instead of GUI')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--seconds', type=float, default=0, help='0: run until Ctrl+C / Q / Esc')
    args = parser.parse_args()
    camera = preview = None
    try:
        camera = Camera(args.source, args.camera)
        if args.browser:
            preview = Preview(args.port)
            print(f'Preview: http://127.0.0.1:{args.port}', flush=True)
        previous_gray = None
        started = last_log = time.monotonic()
        count = 0
        while not args.seconds or time.monotonic() - started < args.seconds:
            frame = camera.read()
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if previous_gray is None or previous_gray.shape != gray.shape:
                mask, pixels, motion = np.zeros_like(gray), 0, False
                status = 'INITIALIZING'
            else:
                mask, pixels, motion = detect_motion(previous_gray, gray)
                status = 'MOTION' if motion else 'NO MOTION'
            previous_gray = gray
            count += 1
            color = (0, 0, 255) if motion else (0, 220, 0)
            # Draw after grayscale/difference so labels never affect detection.
            display = frame.copy()
            cv2.putText(display, f'{status} | pixels: {pixels}', (12, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, .65, color, 2)
            combined = np.hstack((display, cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)))
            if preview:
                preview.update(combined)
            else:
                cv2.imshow('Layer 1 - Frame | Mask (Q / Esc to quit)', combined)
                if cv2.waitKey(1) & 0xFF in (ord('q'), 27):
                    break
                if cv2.getWindowProperty('Layer 1 - Frame | Mask (Q / Esc to quit)',
                                         cv2.WND_PROP_VISIBLE) < 1:
                    break
            if time.monotonic() - last_log >= 1:
                print(f'frame={count} size={gray.shape[1]}x{gray.shape[0]} '
                      f'changed_pixels={pixels} motion={motion}', flush=True)
                last_log = time.monotonic()
    except KeyboardInterrupt:
        pass
    finally:
        if camera is not None:
            camera.close()
        if preview is not None:
            preview.close()
        if not args.browser:
            cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
