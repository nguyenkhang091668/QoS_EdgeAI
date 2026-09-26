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
    frame: np.ndarray              # BGR 640x480, no overlays
    roi: np.ndarray                # BGR 300x300
    mask: np.ndarray               # filtered full-frame mask
    roi_mask: np.ndarray
    metadata: dict
    event: Optional[dict]          # Fresh ai_result event, including negative results

    def jpeg(self, quality=80):
        """Encode stream image without opening any camera/server."""
        if not isinstance(quality, int) or not 0 <= quality <= 100:
            raise ValueError('JPEG quality must be an integer in [0,100]')
        ok, data = cv2.imencode('.jpg', self.frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            raise RuntimeError('JPEG encoding failed')
        return data.tobytes()


class EdgeAICamera:
    """Use read() to own a USB camera OR process(frame) with an existing camera.

    Constructing the object has no hardware/model side effects. Operations are
    serialized, but call from ONE producer loop and distribute its packets.
    An event is an AI observation, not a command to change router/QoS state.
    """

    def __init__(self, model_path=None, camera_index=0, confidence=.5, classes=80,
                 threads=4, cooldown=2., *, detector=None, capture_factory=None,
                 clock=time.monotonic, v4l2=False):
        self.model_path = Path(model_path) if model_path else Path(__file__).with_name('yolov8n.pt')
        self.camera_index = camera_index
        self.v4l2 = v4l2
        self.confidence, self.classes, self.threads = confidence, classes, threads
        self.cooldown = cooldown
        self._detector = detector
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
            if self._detector is None:
                self._detector = Detector(self.model_path, self.confidence, self.classes, self.threads)
            self._pipeline = Pipeline(self._detector, self.cooldown, clock=self._clock)

    def open(self):
        """Open cv2.VideoCapture(camera_index); repeated calls are idempotent."""
        with self._lock:
            if self._capture is not None:
                return self
            self._initialize()
            cap = open_capture(self.camera_index, v4l2=self.v4l2,
                               capture_factory=self._capture_factory)
            self._capture = cap
            # Do not compare a prior externally supplied frame to the new camera.
            self._pipeline = Pipeline(self._detector, self.cooldown, clock=self._clock)
            self._last_ai_frame_id = None
            return self

    def read(self):
        """Read and process one frame. Call open() first; raises on failure."""
        with self._lock:
            if self._capture is None:
                raise RuntimeError('Call open() before read(), or use process(frame)')
            try:
                ok, frame = self._capture.read()
                captured_at = time.time()
                if not ok or frame is None:
                    raise RuntimeError(f'Cannot read USB camera {self.camera_index}')
                return self._process(frame, captured_at)
            except Exception:
                self.close()
                raise

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
        self._initialize()
        try:
            processed, result = self._pipeline.step(frame.copy())
        except Exception:
            # Failed inference must not leave stale FSM/AI data on a retry.
            self._pipeline = None
            self._last_ai_frame_id = None
            raise
        self._frame_id += 1
        event = None
        if result['triggered']:
            self._last_ai_frame_id = self._frame_id
            event = dict(type='ai_result', frame_id=self._frame_id, captured_at=captured_at,
                         **copy.deepcopy(result['ai']))
        metadata = {k:copy.deepcopy(v) for k,v in result.items() if not isinstance(v,np.ndarray)}
        metadata.update(frame_id=self._frame_id, captured_at=captured_at,
                        ai_frame_id=self._last_ai_frame_id,
                        consecutive=self._pipeline.machine.bo_dem_chuyen_dong,
                        cooldown_remaining=self._pipeline.machine.cooldown_remaining)
        return CameraPacket(self._frame_id, captured_at, processed.copy(),
                            result['roi'].copy(), result['clean'].copy(),
                            result['roi_mask'].copy(), metadata, event)

    def close(self):
        """Release only the camera this object opened; reset temporal state."""
        with self._lock:
            cap, self._capture = self._capture, None
            try:
                if cap is not None:
                    cap.release()
            finally:
                self._pipeline = None
                self._last_ai_frame_id = None

    release = close

    def __enter__(self):
        return self.open()

    def __exit__(self, exc_type, exc, traceback):
        self.close()
