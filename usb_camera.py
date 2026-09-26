"""Open a camera with the USB settings validated on Raspberry Pi."""
import cv2


def open_capture(device=0, *, v4l2=False, capture_factory=None):
    factory = capture_factory or cv2.VideoCapture
    cap = factory(device, cv2.CAP_V4L2) if v4l2 else factory(device)
    try:
        if not cap.isOpened():
            raise RuntimeError(f'Cannot open camera {device}; check device access and other camera apps.')
        if v4l2:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        if v4l2:
            cap.set(cv2.CAP_PROP_FPS, 30)
        return cap
    except Exception:
        cap.release()
        raise
