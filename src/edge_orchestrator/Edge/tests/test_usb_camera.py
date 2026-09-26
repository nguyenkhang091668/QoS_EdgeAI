import unittest
from unittest.mock import Mock, patch, call
import cv2
import numpy as np
from edge_orchestrator.Edge.usb_camera import open_capture
from edge_orchestrator.Edge.layer1_motion import Camera
from edge_orchestrator.Edge.pipeline_edge_ai import Pipeline, render_pipeline
from edge_orchestrator.Edge.edge_ai_camera import EdgeAICamera


class USBTests(unittest.TestCase):
    def test_v4l2_configuration(self):
        cap = Mock()
        factory = Mock(return_value=cap)
        self.assertIs(open_capture('/dev/video0', v4l2=True, capture_factory=factory), cap)
        factory.assert_called_once_with('/dev/video0', cv2.CAP_V4L2)
        self.assertEqual(cap.set.call_args_list, [
            call(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG')),
            call(cv2.CAP_PROP_FRAME_WIDTH, 640),
            call(cv2.CAP_PROP_FRAME_HEIGHT, 480),
            call(cv2.CAP_PROP_FPS, 30)])

    def test_failed_open_releases(self):
        cap = Mock(); cap.isOpened.return_value = False
        with self.assertRaises(RuntimeError):
            open_capture('/dev/video0', v4l2=True, capture_factory=Mock(return_value=cap))
        cap.release.assert_called_once()

    def test_camera_rejects_empty_frame(self):
        cap = Mock(); cap.read.return_value = (True, None)
        with patch('edge_orchestrator.Edge.layer1_motion.open_capture', return_value=cap) as opener:
            camera = Camera('usb', '/dev/video0')
            opener.assert_called_once_with('/dev/video0', v4l2=True)
            with self.assertRaises(RuntimeError): camera.read()
            camera.close()
        cap.release.assert_called_once()

    def test_edge_wrapper_uses_v4l2(self):
        cap = Mock(); factory = Mock(return_value=cap)
        with EdgeAICamera(camera_index='/dev/video0', v4l2=True,
                          detector=Mock(), capture_factory=factory):
            factory.assert_called_once_with('/dev/video0', cv2.CAP_V4L2)
        cap.release.assert_called_once()

    def test_motion_only_trigger_has_no_ai_result(self):
        pipeline = Pipeline(None, clock=lambda: 0.)
        triggers = 0
        for i in range(8):
            frame = np.zeros((480, 640, 3), np.uint8)
            x = 180 + (i % 6) * 15
            frame[140:260, x:x+120] = 255
            frame, result = pipeline.step(frame)
            triggers += result['triggered']
            self.assertIsNone(result['ai'])
            self.assertIsNone(result['ai_age'])
            self.assertFalse(result['ai_enabled'])
        self.assertEqual(triggers, 1)
        self.assertEqual(render_pipeline(frame, result, pipeline.machine).shape[2], 3)


if __name__ == '__main__':
    unittest.main()
