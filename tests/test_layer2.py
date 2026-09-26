import unittest
import cv2
import numpy as np
from layer2_motion import filter_mask, kiem_tra_anh_mo, process_frame

class Layer2Tests(unittest.TestCase):
    def test_isolated_noise_removed(self):
        mask = np.zeros((100, 100), np.uint8)
        mask[::3, ::3] = 255
        clean, contours = filter_mask(mask)
        self.assertEqual(cv2.countNonZero(clean), 0)
        self.assertEqual(len(contours), 0)

    def test_small_region_removed_large_region_preserved(self):
        mask = np.zeros((100,100), np.uint8)
        mask[10:16,10:16] = 255
        mask[40:70,40:70] = 255
        clean, contours = filter_mask(mask)
        self.assertEqual(len(contours), 1)
        self.assertEqual(clean[12,12], 0)
        self.assertEqual(clean[50,50], 255)

    def test_hole_preserved(self):
        mask = np.zeros((100,100), np.uint8)
        mask[10:80,10:80] = 255
        mask[30:60,30:60] = 0
        clean, _ = filter_mask(mask)
        self.assertEqual(clean[40,40], 0)

    def test_sharp_and_blurred_frames(self):
        sharp = np.zeros((100,100), np.uint8)
        sharp[25:75,25:75] = 255
        blurred = cv2.GaussianBlur(sharp, (31,31), 8)
        self.assertFalse(kiem_tra_anh_mo(sharp)[0])
        self.assertTrue(kiem_tra_anh_mo(blurred)[0])
        self.assertTrue(process_frame(np.zeros_like(sharp), sharp)['valid'])
        self.assertFalse(process_frame(np.zeros_like(blurred), blurred)['valid'])

    def test_first_static_and_resized_frames(self):
        gray = np.zeros((100,100), np.uint8)
        self.assertFalse(process_frame(None, gray)['valid'])
        self.assertFalse(process_frame(gray, gray)['valid'])
        self.assertFalse(process_frame(gray[:50], gray)['initialized'])

if __name__ == '__main__':
    unittest.main()
