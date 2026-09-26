import unittest
import numpy as np
from edge_orchestrator.Edge.layer4_state import MayTrangThai_Cell4
from edge_orchestrator.Edge.layer3_roi import process_layer3

class Layer4Tests(unittest.TestCase):
    def setUp(self):
        self.now = 0.
        self.machine = MayTrangThai_Cell4(clock=lambda: self.now)

    def test_exactly_five_and_single_pulse(self):
        self.assertEqual([self.machine.update(True) for _ in range(6)],
                         [False,False,False,False,True,False])
        self.assertEqual(self.machine.trigger_count, 1)

    def test_gap_resets_instead_of_decrements(self):
        for _ in range(4):
            self.machine.update(True)
        self.machine.update(False)
        self.assertEqual(self.machine.bo_dem_chuyen_dong, 0)
        self.assertEqual([self.machine.update(True) for _ in range(5)],
                         [False,False,False,False,True])

    def test_cooldown_boundary_requires_new_five_frames(self):
        for _ in range(5):
            self.machine.update(True)
        self.now = 1.999
        for _ in range(20):
            self.assertFalse(self.machine.update(True))
        self.now = 2.
        self.assertFalse(self.machine.update(True))
        self.assertEqual(self.machine.bo_dem_chuyen_dong, 1)
        self.assertEqual([self.machine.update(True) for _ in range(4)],
                         [False,False,False,True])

    def test_zero_cooldown_still_requires_five(self):
        machine = MayTrangThai_Cell4(cooldown=0)
        pulses = [machine.update(True) for _ in range(10)]
        self.assertEqual([i+1 for i,x in enumerate(pulses) if x], [5,10])

    def test_invalid_configuration(self):
        for value in (-1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                MayTrangThai_Cell4(cooldown=value)
        for value in (0, 1.5, True):
            with self.assertRaises(ValueError):
                MayTrangThai_Cell4(so_khung_lien_tiep=value)

    def test_full_pipeline_motion_sequence(self):
        previous = None
        pulses = []
        for i in range(6):
            frame = np.zeros((480,640,3), np.uint8)
            frame[140:260,180+i*15:300+i*15] = 255
            result = process_layer3(previous,frame)
            pulses.append(self.machine.update(result['roi_ready']))
            import cv2
            previous = cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
        self.assertEqual(pulses,[False,False,False,False,False,True])

if __name__ == '__main__':
    unittest.main()
