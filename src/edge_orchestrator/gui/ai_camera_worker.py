import time
import cv2

from PyQt5.QtCore import QThread, pyqtSignal

from edge_orchestrator.Edge import EdgeAICamera
from edge_orchestrator.network.local_camera_bandwidth import (
    EncodedBandwidthMeter,
)


class AICameraWorker(QThread):
    frame_signal = pyqtSignal(object)
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
        source_id="CAM03",
        bandwidth_interval_sec=2.0,
        jpeg_quality=80,
        parent=None,
    ):
        super().__init__(parent)

        self.camera_index = camera_index
        self.model_path = model_path
        self.confidence = confidence
        self.v4l2 = bool(v4l2)
        self.source_id = source_id
        self.bandwidth_interval_sec = float(bandwidth_interval_sec)
        self.jpeg_quality = int(jpeg_quality)
        self._running = False

    def run(self):
        self._running = True
        camera = None
        meter = EncodedBandwidthMeter(interval_sec=self.bandwidth_interval_sec)

        try:
            camera = EdgeAICamera(
                model_path=self.model_path,
                camera_index=self.camera_index,
                confidence=self.confidence,
                v4l2=self.v4l2,
            )

            camera.open()
            self.status_signal.emit("RUNNING")

            while self._running:
                packet = camera.read()
                encoded_frame = packet.jpeg(quality=self.jpeg_quality)
                measurement = meter.observe(len(encoded_frame))
                if measurement is not None:
                    value_mbps, sampled_at = measurement
                    self.bandwidth_signal.emit(
                        self.source_id,
                        value_mbps,
                        sampled_at,
                    )

                # Gửi frame cho GUI.
                self.frame_signal.emit(packet.frame.copy())

                # packet.event chỉ tồn tại khi vừa chạy inference.
                if packet.event is not None:
                    event = packet.event

                    object_detected = bool(
                        event.get("detected", False)
                    )
                    class_id = event.get("class_id", -1)

                    # YOLO COCO: class 0 là person.
                    person_detected = (
                        object_detected
                        and class_id == 0
                    )

                    self.observation_signal.emit(
                        person_detected,
                        event,
                    )

        except Exception as error:
            self.error_signal.emit(str(error))
            self.status_signal.emit("ERROR")
        finally:
            if camera is not None:
                camera.close()
                self.status_signal.emit("STOPPED")

    def stop(self):
        self._running = False
        self.wait(3000)
