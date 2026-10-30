import threading
import time
import unittest
from unittest.mock import Mock
import cv2
import numpy as np
from PyQt5.QtCore import Qt
from edge_orchestrator.gui.ai_camera_worker import PendingROIJob, SharedYOLOInferenceWorker


class AlertPhotoTests(unittest.TestCase):
    def test_photo_and_people_refer_to_same_camera_inference_frame(self):
        detector = Mock()
        detector.infer.return_value = {"persons": [{}, {}], "detected": True, "class_id": 0}
        worker = SharedYOLOInferenceWorker(detector=detector)
        worker.register_camera("CAM03", lambda *args, **kwargs: None)
        done = threading.Event()
        events = []
        def observed(detected, event):
            events.append(event)
            done.set()
        worker.observation_signal.connect(observed, Qt.DirectConnection)
        worker.start()
        try:
            deadline = time.monotonic() + 2
            while not worker._running and time.monotonic() < deadline:
                time.sleep(0.005)
            full = np.full((240, 320, 3), (20, 80, 150), dtype=np.uint8)
            roi = np.zeros((32, 32, 3), dtype=np.uint8)
            job = PendingROIJob("CAM03", 42, time.time(), time.monotonic(), roi, full_frame=full)
            self.assertTrue(worker.submit_job(job))
            self.assertTrue(done.wait(2))
            event = events[0]
            self.assertEqual(event["source_id"], "CAM03")
            self.assertEqual(event["frame_id"], 42)
            self.assertEqual(len(event["persons"]), 2)
            decoded = cv2.imdecode(np.frombuffer(event["alert_photo"], np.uint8), cv2.IMREAD_COLOR)
            self.assertEqual(decoded.shape, full.shape)
            np.testing.assert_allclose(decoded[100, 100], full[100, 100], atol=3)
        finally:
            self.assertTrue(worker.stop(timeout_ms=2000))
