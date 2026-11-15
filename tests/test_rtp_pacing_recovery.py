"""Reproduce startup schedule debt without requiring Raspberry Pi CPU contention."""
from pathlib import Path
import itertools
import shutil
import socket
import struct
import tempfile
import threading
import time
import unittest

import numpy as np

from edge_orchestrator.network.rtp_streamer import RtpStreamConfig, RtpStreamer


@unittest.skipUnless(shutil.which('ffmpeg'), 'Real FFmpeg required')
class PacingRecoveryTests(unittest.TestCase):
    def test_actual_rtp_clock_retains_the_gap_when_output_worker_is_delayed(self):
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(receiver.close)
        receiver.bind(('127.0.0.1', 0))
        receiver.settimeout(.05)
        with tempfile.TemporaryDirectory() as directory:
            config = RtpStreamConfig(destination='127.0.0.1', local_address='127.0.0.1',
                port=receiver.getsockname()[1], width=32, height=24, fps=10,
                sdp_path=str(Path(directory)/'stream.sdp'))
            stream = RtpStreamer(config, route_validator=lambda c: None)
            original_write = stream._write_frame
            calls = []
            def delayed_write(process, payload):
                calls.append(time.monotonic())
                if len(calls) == 3 and stream._stop.wait(.5):
                    return False
                return original_write(process, payload)
            stream._write_frame = delayed_write
            stream.start()
            timestamps = []
            try:
                stream.submit_frame(np.zeros((24, 32, 3), np.uint8), 1)
                deadline = time.monotonic() + 1.5
                while time.monotonic() < deadline:
                    try:
                        packet = receiver.recv(2048)
                    except socket.timeout:
                        continue
                    if packet[1] & 0x80:  # last RTP packet of a video frame
                        timestamps.append(struct.unpack('!I', packet[4:8])[0])
                self.assertNotEqual(stream.snapshot()['status'], 'ERROR')
                self.assertGreaterEqual(len(timestamps), 4)
                gaps = [((b-a) & 0xffffffff) / 90000 for a, b in zip(timestamps, timestamps[1:])]
                self.assertGreater(max(gaps), .35, gaps)
                self.assertTrue(all(gap > 0 for gap in gaps), gaps)
            finally:
                self.assertTrue(stream.close())

    def test_real_encoder_recovers_after_startup_delays_instead_of_permanent_error(self):
        self.run_slow_encoder(iter((.55, .55, 1.08)), 15)

    def test_sustained_slow_encoder_keeps_using_fresh_frames_with_bounded_queue(self):
        self.run_slow_encoder(itertools.repeat(.15), 22)

    def run_slow_encoder(self, delays, required_written):
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(receiver.close)
        receiver.bind(('127.0.0.1', 0))
        receiver.settimeout(.1)
        with tempfile.TemporaryDirectory() as directory:
            config = RtpStreamConfig(destination='127.0.0.1', local_address='127.0.0.1',
                port=receiver.getsockname()[1], sdp_path=str(Path(directory)/'stream.sdp'))
            stream = RtpStreamer(config, route_validator=lambda c: None)
            original_write = stream._write_frame
            write_times = []
            def delayed_write(process, payload):
                # Simulate the worker not getting CPU time while the camera still supplies frames.
                write_times.append(time.monotonic())
                if stream._stop.wait(next(delays, 0)):
                    return False
                return original_write(process, payload)
            stream._write_frame = delayed_write
            stop = threading.Event()
            def feed():
                frame = np.full((480, 640, 3), 80, np.uint8)
                frame_id = 0
                while not stop.is_set():
                    stream.submit_frame(frame, frame_id)
                    frame_id += 1
                    stop.wait(.025)
            producer = threading.Thread(target=feed)
            stream.start()
            producer.start()
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    snapshot = stream.snapshot()
                    if snapshot['status'] == 'ERROR' or snapshot['written'] >= required_written:
                        break
                    try:
                        receiver.recv(2048)
                    except socket.timeout:
                        pass
                snapshot = stream.snapshot()
                self.assertNotEqual(snapshot['status'], 'ERROR', snapshot)
                self.assertGreaterEqual(snapshot['written'], required_written, snapshot)
                self.assertGreater(snapshot['last_frame_id'], 50, snapshot)
                self.assertLessEqual(snapshot['pending_frames'], 1)
                self.assertGreater(snapshot['skipped_slots'], 40, snapshot)
                gaps = [b-a for a, b in zip(write_times[3:], write_times[4:])]
                self.assertTrue(all(gap >= .02 for gap in gaps), gaps)
            finally:
                stop.set()
                producer.join(1)
                self.assertTrue(stream.close())


if __name__ == '__main__':
    unittest.main()
