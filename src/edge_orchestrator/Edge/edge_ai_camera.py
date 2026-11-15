from dataclasses import dataclass
from pathlib import Path
import copy
import threading
import time
from typing import Optional

import cv2
import numpy as np
if __package__:
    from .usb_camera import open_capture
else:
    from usb_camera import open_capture
if __package__:
    from .layer5_ai import Detector
else:
    from layer5_ai import Detector
if __package__:
    from .pipeline_edge_ai import Pipeline
else:
    from pipeline_edge_ai import Pipeline


@dataclass
class CameraPacket:
    """All images/metadata refer to frame_id; event exists only on fresh inference."""
    frame_id: int
    captured_at: float
    frame: np.ndarray              # BGR 640x480, with current valid overlays
    roi: np.ndarray                # BGR 300x300
    mask: np.ndarray               # filtered full-frame mask
    roi_mask: np.ndarray
    metadata: dict
    event: Optional[dict]          # Fresh ai_result event, including negative results
    source_id: str = "CAM03"
    raw_frame: Optional[np.ndarray] = None  # Opt-in BGR snapshot before ROI/box overlays

    def jpeg(self, quality=80):
        """Encode stream image without opening any camera/server."""
        if not isinstance(quality, int) or not 0 <= quality <= 100:
            raise ValueError('JPEG quality must be an integer in [0,100]')
        ok, data = cv2.imencode('.jpg', self.frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            raise RuntimeError('JPEG encoding failed')
        return data.tobytes()


class EdgeAICamera:
    """Use read() to own a USB/CSI camera OR process(frame) with an existing camera.

    Constructing the object has no hardware/model side effects. Operations are
    serialized, but call from ONE producer loop and distribute its packets.
    An event is an AI observation, not a command to change router/QoS state.
    """

    def __init__(self, model_path=None, camera_index=0, confidence=.5, classes=80,
                 threads=4, cooldown=2., *, detector=None, capture_factory=None,
                 clock=time.monotonic, v4l2=False, source="usb",
                 source_id="CAM03", async_ai=False, max_box_age_sec=2.0,
                 active_recheck_sec=0.8, idle_recheck_sec=1.5, retain_raw_frame=False):
        self.model_path = Path(model_path) if model_path else Path(__file__).with_name('yolov8n.pt')
        self.camera_index = camera_index
        self.v4l2 = v4l2
        self.confidence, self.classes, self.threads = confidence, classes, threads
        self.cooldown = cooldown
        self._detector = detector
        self._source = source
        self.source_id = source_id
        self.async_ai = bool(async_ai)
        self.max_box_age_sec = max_box_age_sec
        self.active_recheck_sec = active_recheck_sec
        self.idle_recheck_sec = idle_recheck_sec
        self.retain_raw_frame = bool(retain_raw_frame)
        self._capture_factory = capture_factory or cv2.VideoCapture
        self._clock = clock
        self._lock = threading.RLock()
        self._capture = None
        self._pipeline = None
        self._frame_id = 0
        self._last_ai_frame_id = None

    def _initialize(self):
        if self._pipeline is None:
            # Validate FSM config before allocating the model.
            if __package__:
                from .layer4_state import MayTrangThai_Cell4
            else:
                from layer4_state import MayTrangThai_Cell4
            MayTrangThai_Cell4(cooldown=self.cooldown, clock=self._clock)
            if not self.async_ai and self._detector is None:
                self._detector = Detector(self.model_path, self.confidence, self.classes, self.threads)
            self._pipeline = Pipeline(
                None if self.async_ai else self._detector,
                self.cooldown,
                clock=self._clock,
                max_box_age_sec=self.max_box_age_sec,
                active_recheck_sec=self.active_recheck_sec,
                idle_recheck_sec=self.idle_recheck_sec,
            )

    def apply_ai_result(self, ai_result, *, frame_id=None, captured_at=None, inferred_at=None):
        """Gắn kết quả suy luận bất đồng bộ vào pipeline của camera này (không giữ _lock camera)."""
        pipeline = self._pipeline
        if pipeline is None:
            return False
        accepted = pipeline.apply_ai_result(
            ai_result,
            frame_id=frame_id,
            captured_at=captured_at,
            inferred_at=inferred_at,
        )
        if accepted and frame_id is not None:
            self._last_ai_frame_id = frame_id
        return accepted

    def set_active_recheck_sec(self, interval_sec: float):
        """Điều chỉnh động chu kỳ quét lại khi đang có người cho camera này."""
        val = float(interval_sec)
        if val <= 0:
            raise ValueError("active_recheck_sec must be > 0")
        self.active_recheck_sec = val
        pipeline = self._pipeline
        if pipeline is not None:
            pipeline.set_active_recheck_sec(val)

    def clear_ai_state(self):
        """Xóa ngay bounding box của camera này khi lỗi hoặc dừng."""
        pipeline = self._pipeline
        if pipeline is not None:
            pipeline.clear_ai_state()
        self._last_ai_frame_id = None

    def open(self):
        """Open cv2.VideoCapture(camera_index) or Picamera2; repeated calls are idempotent."""
        with self._lock:
            if self._capture is not None:
                return self
            self._initialize()
            if self._source == "picamera2" and self._capture_factory is cv2.VideoCapture:
                if __package__:
                    from .layer1_motion import Camera
                else:
                    from layer1_motion import Camera
                cap = Camera("picamera2", self.camera_index)
            elif self.v4l2 and self._capture_factory is cv2.VideoCapture:
                cap = open_capture(
                    self.camera_index,
                    v4l2=True,
                    capture_factory=self._capture_factory,
                    fps=24,
                    buffer_size=1,
                )
            else:
                cap = open_capture(self.camera_index, v4l2=self.v4l2,
                               capture_factory=self._capture_factory)
            self._capture = cap
            self._pipeline = Pipeline(
                None if self.async_ai else self._detector,
                self.cooldown,
                clock=self._clock,
                max_box_age_sec=self.max_box_age_sec,
                active_recheck_sec=self.active_recheck_sec,
                idle_recheck_sec=self.idle_recheck_sec,
            )
            self._last_ai_frame_id = None
            return self

    def read(self):
        """Read and process one frame. Call open() first; raises on failure."""
        with self._lock:
            if self._capture is None:
                raise RuntimeError('Call open() before read(), or use process(frame)')
            try:
                if self._source == "picamera2":
                    frame = self._capture.read()
                    ok = frame is not None
                else:
                    ok, frame = self._capture.read()
                captured_at = time.time()
                if not ok or frame is None:
                    raise RuntimeError(f'Cannot read USB camera {self.camera_index}')
                return self._process(frame, captured_at)
            except Exception:
                self.close()
                raise

    def close(self):
        """Release only the camera this object opened; reset temporal state."""
        with self._lock:
            cap, self._capture = self._capture, None
            try:
                if cap is not None:
                    if self._source == "picamera2" and hasattr(cap, "close"):
                        cap.close()
                    else:
                        cap.release()
            finally:
                if self._pipeline is not None:
                    self._pipeline.clear_ai_state()
                self._pipeline = None
                self._last_ai_frame_id = None

    def process(self, frame, *, captured_at=None):
        """Process an existing BGR frame WITHOUT opening/closing its camera.

        Call before timestamp/overlay is drawn by the web server.
        Do not mix externally supplied frames with owned-camera read().
        """
        with self._lock:
            if self._capture is not None:
                raise RuntimeError('Camera is owned by this object; use read()')
            return self._process(frame, time.time() if captured_at is None else captured_at)

    def _process(self, frame, captured_at):
        if not isinstance(frame, np.ndarray) or frame.dtype != np.uint8:
            raise ValueError('Expected uint8 BGR frame')
        if frame.ndim != 3 or frame.shape[2] != 3 or min(frame.shape[:2]) == 0:
            raise ValueError('Expected non-empty HxWx3 BGR frame')
        raw_frame = frame.copy() if self.retain_raw_frame else None
        self._initialize()
        try:
            processed, result = self._pipeline.step(frame)
        except Exception:
            # Failed inference must not leave stale FSM/AI data on a retry.
            self._pipeline = None
            self._last_ai_frame_id = None
            raise
        self._frame_id += 1
        event = None
        if result.get("inferred", False):
            self._last_ai_frame_id = self._frame_id
            event = {
                "type": "ai_result",
                "source_id": self.source_id,
                "frame_id": self._frame_id,
                "captured_at": captured_at,
                **copy.deepcopy(result["ai"]),
            }
        metadata = {
            k: (copy.deepcopy(v) if isinstance(v, (dict, list)) else v)
            for k, v in result.items()
            if not isinstance(v, np.ndarray)
        }
        metadata.update(
            source_id=self.source_id,
            frame_id=self._frame_id,
            captured_at=captured_at,
            ai_frame_id=self._last_ai_frame_id,
            consecutive=self._pipeline.machine.bo_dem_chuyen_dong,
            cooldown_remaining=self._pipeline.machine.cooldown_remaining,
        )
        return CameraPacket(
            self._frame_id,
            captured_at,
            processed,
            result['roi'],
            result['clean'],
            result['roi_mask'],
            metadata,
            event,
            source_id=self.source_id,
            raw_frame=raw_frame,
        )
    release = close

    def __enter__(self):
        return self.open()

    def __exit__(self, exc_type, exc, traceback):
        self.close()

