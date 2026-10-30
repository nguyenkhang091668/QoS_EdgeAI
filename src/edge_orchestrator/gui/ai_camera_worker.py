import copy
from dataclasses import dataclass
import threading
import time
from typing import Callable, Dict, Optional
import cv2
import numpy as np

from PyQt5.QtCore import QThread, pyqtSignal

from edge_orchestrator.Edge import EdgeAICamera
from edge_orchestrator.Edge.layer5_ai import Detector
from edge_orchestrator.network.local_camera_bandwidth import (
    EncodedBandwidthMeter,
)


@dataclass
class PendingROIJob:
    source_id: str
    frame_id: int
    captured_at: float
    submitted_at: float
    roi: np.ndarray
    reason: str = "motion_trigger"
    full_frame: Optional[np.ndarray] = None


class SharedYOLOInferenceWorker(QThread):
    """Một worker YOLO dùng chung cho CAM03 & CAM04, lấy mẫu luân phiên (Round-Robin) từ 2 pending slot."""

    observation_signal = pyqtSignal(bool, dict)
    error_signal = pyqtSignal(str, str)   # (source_id, message)
    status_signal = pyqtSignal(str)       # "RUNNING" | "STOPPED" | "ERROR"

    def __init__(
        self,
        model_path=None,
        confidence=0.5,
        classes=80,
        threads=2,
        camera_order=("CAM03", "CAM04"),
        detector=None,
        parent=None,
    ):
        super().__init__(parent)
        self.model_path = model_path
        self.confidence = float(confidence)
        self.classes = int(classes)
        self.threads = int(threads)
        self._detector = detector
        self._cond = threading.Condition()
        self._running = False
        self._turn_order = list(camera_order)
        self._turn_index = 0
        self._pending_slots: Dict[str, Optional[PendingROIJob]] = {
            cam_id: None for cam_id in self._turn_order
        }
        self._camera_callbacks: Dict[str, Callable] = {}
        self._active_sources: set[str] = set()
        self._stats_by_camera: Dict[str, dict] = {
            cam_id: {
                "submitted": 0,
                "inferred": 0,
                "overwritten_slots": 0,
                "last_infer_ms": 0.0,
                "last_queue_wait_ms": 0.0,
                "last_ai_age_ms": 0.0,
            }
            for cam_id in self._turn_order
        }

    def register_camera(self, source_id: str, apply_result_callback: Callable):
        with self._cond:
            if source_id not in self._pending_slots:
                self._pending_slots[source_id] = None
                self._turn_order.append(source_id)
                self._stats_by_camera[source_id] = {
                    "submitted": 0,
                    "inferred": 0,
                    "overwritten_slots": 0,
                    "last_infer_ms": 0.0,
                    "last_queue_wait_ms": 0.0,
                    "last_ai_age_ms": 0.0,
                }
            self._camera_callbacks[source_id] = apply_result_callback
            self._active_sources.add(source_id)

    def unregister_camera(self, source_id: str):
        with self._cond:
            self._pending_slots[source_id] = None
            self._camera_callbacks.pop(source_id, None)
            self._active_sources.discard(source_id)

    def submit_job(self, job: PendingROIJob) -> bool:
        """Đặt 1 ROI vào slot của camera; mẫu mới ghi đè mẫu cũ nếu chưa kịp lấy."""
        with self._cond:
            if not self._running or job.source_id not in self._active_sources:
                return False
            stats = self._stats_by_camera.setdefault(
                job.source_id,
                {
                    "submitted": 0,
                    "inferred": 0,
                    "overwritten_slots": 0,
                    "last_infer_ms": 0.0,
                    "last_queue_wait_ms": 0.0,
                    "last_ai_age_ms": 0.0,
                },
            )
            if self._pending_slots.get(job.source_id) is not None:
                stats["overwritten_slots"] += 1
            self._pending_slots[job.source_id] = job
            stats["submitted"] += 1
            self._cond.notify()
            return True

    def get_diagnostics(self) -> dict:
        with self._cond:
            return copy.deepcopy(self._stats_by_camera)

    def _pop_next_job_locked(self) -> Optional[PendingROIJob]:
        if not self._turn_order:
            return None
        n = len(self._turn_order)
        for offset in range(n):
            idx = (self._turn_index + offset) % n
            cam_id = self._turn_order[idx]
            job = self._pending_slots.get(cam_id)
            if job is not None:
                self._pending_slots[cam_id] = None
                self._turn_index = (idx + 1) % n
                return job
        return None

    def run(self):
        with self._cond:
            self._running = True

        try:
            if self._detector is None:
                self._detector = Detector(
                    self.model_path,
                    self.confidence,
                    self.classes,
                    self.threads,
                )
                if hasattr(self._detector, "warmup"):
                    self._detector.warmup()
            self.status_signal.emit("RUNNING")

            while True:
                with self._cond:
                    while self._running and all(
                        v is None for v in self._pending_slots.values()
                    ):
                        self._cond.wait(timeout=0.5)
                    if not self._running:
                        break
                    job = self._pop_next_job_locked()
                    if job is None:
                        continue
                    callback = self._camera_callbacks.get(job.source_id)

                # Chạy YOLO hoàn toàn NGOÀI lock để không chặn luồng capture 30FPS
                infer_started_mono = time.monotonic()
                queue_wait_ms = max(0.0, (infer_started_mono - job.submitted_at) * 1000.0)
                ai_result = self._detector.infer(job.roi)
                inferred_mono = time.monotonic()
                infer_ms = float(
                    ai_result.get(
                        "inference_ms",
                        (inferred_mono - infer_started_mono) * 1000.0,
                    )
                )
                ai_age_ms = max(0.0, (time.time() - job.captured_at) * 1000.0)

                with self._cond:
                    is_still_active = (
                        self._running and job.source_id in self._active_sources
                    )
                    stats = self._stats_by_camera.get(job.source_id)
                    if stats is not None:
                        stats["inferred"] += 1
                        stats["last_infer_ms"] = round(infer_ms, 2)
                        stats["last_queue_wait_ms"] = round(queue_wait_ms, 2)
                        stats["last_ai_age_ms"] = round(ai_age_ms, 2)
                        overwritten = stats["overwritten_slots"]
                    else:
                        overwritten = 0

                if not is_still_active:
                    continue

                if callback is not None:
                    callback(
                        ai_result,
                        frame_id=job.frame_id,
                        captured_at=job.captured_at,
                        inferred_at=inferred_mono,
                    )

                persons = ai_result.get("persons", [])
                object_detected = bool(ai_result.get("detected", False))
                class_id = ai_result.get("class_id", -1)
                person_detected = bool(
                    persons or (object_detected and class_id == 0)
                )

                event = {
                    "type": "ai_result",
                    "source_id": job.source_id,
                    "frame_id": job.frame_id,
                    "captured_at": job.captured_at,
                    "inferred_at": inferred_mono,
                    "reason": job.reason,
                    "queue_wait_ms": round(queue_wait_ms, 2),
                    "ai_age_ms": round(ai_age_ms, 2),
                    "overwritten_slots": overwritten,
                    **copy.deepcopy(ai_result),
                }
                if person_detected:
                    photo_frame = job.full_frame if job.full_frame is not None else job.roi
                    ok, encoded = cv2.imencode(".jpg", photo_frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                    if ok:
                        event["alert_photo"] = encoded.tobytes()
                self.observation_signal.emit(person_detected, event)

        except Exception as error:
            self.error_signal.emit("YOLO", str(error))
            self.status_signal.emit("ERROR")
        finally:
            with self._cond:
                self._running = False
                for k in self._pending_slots:
                    self._pending_slots[k] = None
            self._detector = None
            self.status_signal.emit("STOPPED")

    def stop(self, timeout_ms: int = 5000) -> bool:
        """Đánh thức worker đang chờ, đợi suy luận hiện tại kết thúc rồi mới giải phóng model."""
        with self._cond:
            self._running = False
            for k in self._pending_slots:
                self._pending_slots[k] = None
            self._cond.notify_all()
        if self.isRunning():
            return bool(self.wait(timeout_ms))
        return True


class AICameraWorker(QThread):
    frame_signal = pyqtSignal(object)
    frame_packet_signal = pyqtSignal(str, int, float, object)  # (source_id, frame_id, captured_at, frame)
    observation_signal = pyqtSignal(bool, dict)
    bandwidth_signal = pyqtSignal(
        str,
        float,
        float,
    )
    error_signal = pyqtSignal(str)
    status_signal = pyqtSignal(str)

    def __init__(
        self,
        camera_index=0,
        model_path=None,
        confidence=0.5,
        v4l2=False,
        source="usb",
        source_id="CAM03",
        bandwidth_interval_sec=2.0,
        jpeg_quality=80,
        shared_yolo_worker: Optional[SharedYOLOInferenceWorker] = None,
        max_box_age_sec=2.0,
        active_recheck_sec=0.8,
        idle_recheck_sec=1.5,
        parent=None,
    ):
        super().__init__(parent)

        self.camera_index = camera_index
        self.model_path = model_path
        self.confidence = confidence
        self.v4l2 = bool(v4l2)
        self.source = source
        self.source_id = source_id
        self.bandwidth_interval_sec = float(bandwidth_interval_sec)
        self.jpeg_quality = int(jpeg_quality)
        self.shared_yolo_worker = shared_yolo_worker
        self.max_box_age_sec = float(max_box_age_sec)
        self.active_recheck_sec = float(active_recheck_sec)
        self.idle_recheck_sec = float(idle_recheck_sec)
        self._running = False
        self._camera_ref: Optional[EdgeAICamera] = None

        # Bộ đếm nghiệm thu trên Pi 4
        self.preview_fps = 0.0
        self.max_frame_gap_ms = 0.0
        self.triggers_count = 0
        self.periodic_count = 0
        self.latest_emitted_frame_id = 0
        self.dropped_gui_frames = 0

    def get_diagnostics(self) -> dict:
        return {
            "source_id": self.source_id,
            "preview_fps": round(self.preview_fps, 2),
            "max_frame_gap_ms": round(self.max_frame_gap_ms, 2),
            "triggers_count": self.triggers_count,
            "periodic_count": self.periodic_count,
            "latest_emitted_frame_id": self.latest_emitted_frame_id,
            "dropped_gui_frames": self.dropped_gui_frames,
        }

    def run(self):
        self._running = True
        camera = None
        meter = EncodedBandwidthMeter(interval_sec=self.bandwidth_interval_sec)
        use_async_yolo = self.shared_yolo_worker is not None

        try:
            camera = EdgeAICamera(
                model_path=self.model_path,
                camera_index=self.camera_index,
                confidence=self.confidence,
                v4l2=self.v4l2,
                source=self.source,
                source_id=self.source_id,
                async_ai=use_async_yolo,
                max_box_age_sec=self.max_box_age_sec,
                active_recheck_sec=self.active_recheck_sec,
                idle_recheck_sec=self.idle_recheck_sec,
            )
            self._camera_ref = camera

            camera.open()
            if use_async_yolo:
                self.shared_yolo_worker.register_camera(
                    self.source_id,
                    camera.apply_ai_result,
                )
            self.status_signal.emit("RUNNING")

            fps_window_start = time.monotonic()
            fps_window_frames = 0
            last_frame_mono = None
            last_jpeg_size = 0

            while self._running:
                packet = camera.read()
                now_mono = time.monotonic()

                if last_frame_mono is not None:
                    gap_ms = (now_mono - last_frame_mono) * 1000.0
                    if gap_ms > self.max_frame_gap_ms:
                        self.max_frame_gap_ms = gap_ms
                last_frame_mono = now_mono

                fps_window_frames += 1
                elapsed_fps = now_mono - fps_window_start
                if elapsed_fps >= 1.0:
                    self.preview_fps = fps_window_frames / elapsed_fps
                    fps_window_frames = 0
                    fps_window_start = now_mono

                if last_jpeg_size == 0 or packet.frame_id % 15 == 1:
                    last_jpeg_size = len(packet.jpeg(quality=self.jpeg_quality))
                measurement = meter.observe(last_jpeg_size)
                if measurement is not None:
                    value_mbps, sampled_at = measurement
                    self.bandwidth_signal.emit(
                        self.source_id,
                        value_mbps,
                        sampled_at,
                    )

                # Gửi frame kèm định danh (source_id, frame_id, captured_at) cho GUI ngay lập tức.
                self.latest_emitted_frame_id = packet.frame_id
                self.frame_packet_signal.emit(
                    self.source_id,
                    packet.frame_id,
                    packet.captured_at,
                    packet.frame,
                )
                self.frame_signal.emit(packet.frame)

                if use_async_yolo:
                    meta = packet.metadata
                    if meta.get("should_infer", False):
                        reason = meta.get("infer_reason") or "motion_trigger"
                        if reason == "motion_trigger":
                            self.triggers_count += 1
                        else:
                            self.periodic_count += 1
                        self.shared_yolo_worker.submit_job(
                            PendingROIJob(
                                source_id=self.source_id,
                                frame_id=packet.frame_id,
                                captured_at=packet.captured_at,
                                submitted_at=now_mono,
                                roi=packet.roi.copy(),
                                reason=reason,
                                full_frame=packet.frame.copy(),
                            )
                        )
                elif packet.event is not None:
                    event = dict(packet.event)
                    persons = event.get("persons", [])
                    object_detected = bool(event.get("detected", False))
                    class_id = event.get("class_id", -1)
                    person_detected = bool(
                        persons or (object_detected and class_id == 0)
                    )
                    event["source_id"] = self.source_id
                    if person_detected:
                        ok, encoded = cv2.imencode(".jpg", packet.frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                        if ok:
                            event["alert_photo"] = encoded.tobytes()
                    self.observation_signal.emit(
                        person_detected,
                        event,
                    )

        except Exception as error:
            if camera is not None:
                camera.clear_ai_state()
            self.error_signal.emit(str(error))
            self.status_signal.emit("ERROR")
        finally:
            if use_async_yolo and self.shared_yolo_worker is not None:
                self.shared_yolo_worker.unregister_camera(self.source_id)
            if camera is not None:
                camera.close()
                self._camera_ref = None
                self.status_signal.emit("STOPPED")

    def request_stop(self):
        self._running = False

    def stop(self, timeout_ms: int = 3000) -> bool:
        self._running = False
        if self.isRunning():
            return bool(self.wait(timeout_ms))
        return True