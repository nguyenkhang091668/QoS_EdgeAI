"""Native startup barrier: exercise real worker bodies without claiming Qt integration."""
import ast
import copy
from dataclasses import replace
from pathlib import Path
import shutil
import socket
import tempfile
import threading
import time
import typing
import unittest
from unittest.mock import Mock

import numpy as np

from edge_orchestrator.network.rtp_streamer import RtpStreamConfig, RtpStreamer


def wait_for(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.01)
    return False


class StreamStartupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = replace(RtpStreamConfig(), width=32, height=24,
            sdp_path=str(Path(temporary.name)/'test.sdp'))
        self.frame = np.full((24,32,3), 80, np.uint8)

    def test_waiting_for_ai_keeps_capture_nonblocking_and_stop_does_not_spawn(self):
        ready = threading.Event()
        popen = Mock()
        stream = RtpStreamer(self.config, ready_event=ready,
                             route_validator=lambda c: None, process_factory=popen)
        self.addCleanup(stream.close)
        stream.start()
        for frame_id in range(20):
            self.assertTrue(stream.submit_frame(self.frame, frame_id))
        time.sleep(.05)
        popen.assert_not_called()
        self.assertLessEqual(stream.snapshot()['pending_frames'], 1)
        self.assertTrue(stream.close(1))
        popen.assert_not_called()

    def test_ready_gate_does_not_launch_with_old_frame(self):
        ready = threading.Event()
        popen = Mock(side_effect=RuntimeError('fresh-frame launch boundary'))
        config = replace(self.config, max_frame_age_sec=.05)
        stream = RtpStreamer(config, ready_event=ready,
                             route_validator=lambda c: None, process_factory=popen)
        self.addCleanup(stream.close)
        stream.start()
        stream.submit_frame(self.frame, 1)
        time.sleep(.07)
        ready.set()
        time.sleep(.06)
        popen.assert_not_called()
        stream.submit_frame(self.frame, 2)
        self.assertTrue(wait_for(lambda: stream.snapshot()['status'] == 'ERROR'))
        popen.assert_called_once()

    @unittest.skipUnless(shutil.which('ffmpeg'), 'Real FFmpeg required')
    def test_real_rtp_flow_begins_after_ai_ready(self):
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(receiver.close)
        receiver.bind(('127.0.0.1', 0))
        receiver.settimeout(.15)
        ready = threading.Event()
        config = replace(self.config, destination='127.0.0.1', local_address='127.0.0.1',
                         port=receiver.getsockname()[1])
        stream = RtpStreamer(config, ready_event=ready, route_validator=lambda c: None)
        stop = threading.Event()
        def feed():
            frame_id = 0
            while not stop.is_set():
                stream.submit_frame(self.frame, frame_id)
                frame_id += 1
                stop.wait(.03)
        producer = threading.Thread(target=feed)
        stream.start()
        producer.start()
        try:
            with self.assertRaises(socket.timeout):
                receiver.recv(2048)
            ready.set()
            deadline = time.monotonic() + 3
            packets = []
            while time.monotonic() < deadline:
                try:
                    packets.append(receiver.recv(2048))
                except socket.timeout:
                    pass
                if stream.snapshot()['written'] >= 15:
                    break
            self.assertGreater(len(packets), 3)
            self.assertEqual(stream.snapshot()['status'], 'RUNNING')
            self.assertGreaterEqual(stream.snapshot()['written'], 15)
            self.assertTrue(all(packet[1] & 0x7f == 96 for packet in packets))
        finally:
            stop.set()
            producer.join(1)
            self.assertTrue(stream.close())


class SharedReadyTests(unittest.TestCase):
    def worker(self, warmup):
        class Signal:
            def __init__(self, *args): self.calls = []
            def emit(self, *args): self.calls.append(args)
        class Thread:
            def __init__(self, parent): pass
            def isRunning(self): return False
        detector = Mock()
        detector.warmup.side_effect = warmup
        namespace = dict(QThread=Thread, pyqtSignal=Signal, threading=threading, time=time,
                         Dict=typing.Dict, Optional=typing.Optional, Callable=typing.Callable,
                         PendingROIJob=object, Detector=Mock(return_value=detector), copy=copy)
        path = Path(__file__).parents[1]/'src/edge_orchestrator/gui/ai_camera_worker.py'
        cls = next(item for item in ast.parse(path.read_text()).body
                   if isinstance(item, ast.ClassDef) and item.name == 'SharedYOLOInferenceWorker')
        exec(compile(ast.Module(body=[cls], type_ignores=[]), str(path), 'exec'), namespace)
        return namespace['SharedYOLOInferenceWorker'](camera_order=('CAM03',)), detector

    def test_ready_is_set_only_after_warmup_and_cleared_on_stop(self):
        entered, finish = threading.Event(), threading.Event()
        def warmup():
            entered.set()
            finish.wait(2)
        worker, detector = self.worker(warmup)
        self.assertFalse(worker.ready_event.is_set())
        runner = threading.Thread(target=worker.run)
        runner.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertFalse(worker.ready_event.is_set())
            finish.set()
            self.assertTrue(worker.ready_event.wait(1))
            worker.stop()
            runner.join(1)
            self.assertFalse(runner.is_alive())
            self.assertFalse(worker.ready_event.is_set())
            detector.warmup.assert_called_once()
        finally:
            finish.set()
            worker.stop()
            runner.join(2)

    def test_failed_warmup_never_reports_ready(self):
        def warmup(): raise RuntimeError('warmup fixture failure')
        worker, detector = self.worker(warmup)
        worker.run()
        self.assertFalse(worker.ready_event.is_set())
        self.assertNotIn(('RUNNING',), worker.status_signal.calls)
        self.assertIn(('ERROR',), worker.status_signal.calls)

    def test_stop_during_warmup_does_not_release_encoder(self):
        entered, finish = threading.Event(), threading.Event()
        def warmup():
            entered.set()
            finish.wait(2)
        worker, detector = self.worker(warmup)
        runner = threading.Thread(target=worker.run)
        runner.start()
        try:
            self.assertTrue(entered.wait(1))
            worker.stop()
            finish.set()
            runner.join(1)
            self.assertFalse(worker.ready_event.is_set())
            self.assertNotIn(('RUNNING',), worker.status_signal.calls)
        finally:
            finish.set()
            worker.stop()
            runner.join(2)


if __name__ == '__main__': unittest.main()
