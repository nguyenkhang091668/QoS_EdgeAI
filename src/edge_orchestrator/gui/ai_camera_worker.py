import time
import cv2

from PyQt5.QtCore import QThread, pyqtSignal

from edge_orchestrator.Edge import EdgeAICamera


class AICameraWorker(QThread):
    frame_signal = pyqtSignal(object)
    observation_signal = pyqtSignal(bool, dict)
    error_signal = pyqtSignal(str)
    status_signal = pyqtSignal(str)


    def __init__(
        self,
        camera_index=0,
        model_path=None,
        confidence=0.5,
        parent=None,
    ):
        super().__init__(parent)

        self.camera_index = camera_index
        self.model_path = model_path
        self.confidence = confidence
        self._running = False

    def run(self):
        self._running = True
        camera = None

        try:
            camera = EdgeAICamera(
                model_path=self.model_path,
                camera_index=self.camera_index,
                confidence=self.confidence,
            )

            camera.open()
            self.status_signal.emit("RUNNING")

            while self._running:
                packet = camera.read()

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