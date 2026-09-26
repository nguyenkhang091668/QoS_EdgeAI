import unittest
import cv2
import numpy as np
from edge_orchestrator.Edge.layer3_roi import crop_roi, normalize_frame, process_layer3, render_roi

class Layer3Tests(unittest.TestCase):
    def test_exact_crop_and_independent_copy(self):
        frame = np.arange(480*640*3, dtype=np.uint32).reshape(480,640,3)
        roi = crop_roi(frame)
        np.testing.assert_array_equal(roi, frame[100:400,150:450])
        self.assertEqual(roi.shape, (300,300,3))
        roi[:] = 0
        self.assertTrue(frame[100:400,150:450].any())

    def test_invalid_size_and_normalization(self):
        frame = np.zeros((720,1280,3), np.uint8)
        with self.assertRaises(ValueError):
            crop_roi(frame)
        self.assertEqual(normalize_frame(frame).shape, (480,640,3))

    def test_inside_motion_and_render_does_not_modify_roi(self):
        frame = np.zeros((480,640,3), np.uint8)
        frame[180:280,200:300] = 255
        result = process_layer3(np.zeros((480,640),np.uint8), frame)
        self.assertTrue(result['roi_ready'])
        self.assertGreater(result['roi_pixels'], 0)
        expected = frame[100:400,150:450].copy()
        self.assertEqual(render_roi(frame,result).shape, (530,1240,3))
        np.testing.assert_array_equal(result['roi'], expected)

    def test_outside_motion_not_in_roi(self):
        frame = np.zeros((480,640,3),np.uint8)
        frame[10:90,10:90] = 255
        result = process_layer3(np.zeros((480,640),np.uint8),frame)
        self.assertTrue(result['valid'])
        self.assertEqual(result['roi_pixels'], 0)
        self.assertFalse(result['roi'].any())

    def test_first_frame_not_ready(self):
        frame = np.zeros((480,640,3),np.uint8)
        self.assertFalse(process_layer3(None,frame)['roi_ready'])

if __name__ == '__main__':
    unittest.main()
