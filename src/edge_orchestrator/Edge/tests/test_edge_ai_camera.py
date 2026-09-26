import unittest
from unittest.mock import Mock
import cv2
import numpy as np
from edge_orchestrator.Edge.edge_ai_camera import EdgeAICamera

def frame(i):
    image=np.zeros((480,640,3),np.uint8)
    x=180+(i%6)*15
    image[140:260,x:x+120]=255
    return image

class Detector:
    def infer(self,roi):
        return dict(detected=True,label='test',score=.9,class_id=0,inference_ms=1.)

class CameraTests(unittest.TestCase):
    def test_external_frames_no_camera_and_fresh_event(self):
        capture=Mock()
        camera=EdgeAICamera(detector=Detector(),capture_factory=capture,clock=lambda:0.)
        packets=[camera.process(frame(i)) for i in range(8)]
        capture.assert_not_called()
        self.assertEqual([p.frame_id for p in packets if p.event],[6])
        self.assertEqual(packets[-1].metadata['ai_frame_id'],6)
        self.assertIsNone(packets[-1].event)
        packets[5].metadata['ai']['label']='edited'
        self.assertEqual(camera.process(frame(0)).metadata['ai']['label'],'test')

    def test_own_camera_idempotent_open_release(self):
        cap=Mock()
        cap.isOpened.return_value=True
        cap.read.return_value=(True,frame(0))
        factory=Mock(return_value=cap)
        with EdgeAICamera(detector=Detector(),capture_factory=factory) as camera:
            camera.open()
            packet=camera.read()
            self.assertEqual(packet.roi.shape,(300,300,3))
            with self.assertRaises(RuntimeError):
                camera.process(frame(0))
        camera.close()
        factory.assert_called_once_with(0)
        cap.release.assert_called_once()

    def test_failure_releases_camera(self):
        cap=Mock()
        cap.isOpened.return_value=True
        cap.read.return_value=(False,None)
        camera=EdgeAICamera(detector=Detector(),capture_factory=lambda _:cap).open()
        with self.assertRaises(RuntimeError): camera.read()
        cap.release.assert_called_once()

    def test_no_mutation_and_jpeg(self):
        source=frame(0)
        expected=source.copy()
        camera=EdgeAICamera(detector=Detector())
        packet=camera.process(source)
        packet.frame[:]=80
        np.testing.assert_array_equal(source,expected)
        decoded=cv2.imdecode(np.frombuffer(packet.jpeg(),np.uint8),cv2.IMREAD_COLOR)
        self.assertEqual(decoded.shape,(480,640,3))

    def test_read_requires_open_and_close_resets(self):
        camera=EdgeAICamera(detector=Detector())
        with self.assertRaises(RuntimeError): camera.read()
        for i in range(5): camera.process(frame(i))
        camera.close()
        self.assertFalse(camera.process(frame(5)).metadata['initialized'])

    def test_bad_frame_and_model_error_never_open_camera(self):
        factory=Mock()
        camera=EdgeAICamera(model_path='/missing/model.tflite',capture_factory=factory)
        with self.assertRaises(ValueError): camera.process(np.zeros((1,1),np.uint8))
        with self.assertRaises(FileNotFoundError): camera.open()
        factory.assert_not_called()

if __name__=='__main__': unittest.main()
