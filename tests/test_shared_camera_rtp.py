import ast
from dataclasses import replace
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import typing
import unittest
from unittest.mock import Mock

import numpy as np

from edge_orchestrator.Edge.edge_ai_camera import EdgeAICamera
from edge_orchestrator.network.rtp_streamer import RtpStreamConfig, RtpStreamer


def wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.02)
    return False


class CameraFrameTests(unittest.TestCase):
    def test_clean_snapshot_isolated_from_preview_and_source_without_another_capture(self):
        factory = Mock()
        camera = EdgeAICamera(async_ai=True, retain_raw_frame=True, capture_factory=factory)
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        packet = camera.process(frame)
        self.assertIsNotNone(packet.raw_frame)
        self.assertTrue(np.any(packet.frame != packet.raw_frame))  # ROI overlay
        packet.frame[:] = 200
        frame[:] = 255
        self.assertFalse(packet.raw_frame.any())
        self.assertEqual(packet.frame_id, 1)
        factory.assert_not_called()
        camera.close()

    def test_disabled_path_does_not_retain_an_extra_frame(self):
        camera = EdgeAICamera(async_ai=True)
        self.assertIsNone(camera.process(np.zeros((480, 640, 3), np.uint8)).raw_frame)
        camera.close()


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = replace(RtpStreamConfig(), sdp_path=str(Path(self.directory.name)/'test.sdp'))

    def test_route_failure_does_not_spawn_encoder(self):
        popen = Mock()
        def deny(config):
            raise RuntimeError("wrong interface")
        stream = RtpStreamer(self.config, route_validator=deny, process_factory=popen)
        stream.start()
        self.assertTrue(wait_until(lambda: stream.snapshot()['status'] == 'ERROR'))
        self.assertFalse(stream.submit_frame(np.zeros((480, 640, 3), np.uint8), 1))
        self.assertTrue(stream.close())
        popen.assert_not_called()

    def test_waiting_close_never_spawns_encoder(self):
        popen = Mock()
        stream = RtpStreamer(self.config, route_validator=lambda c: None, process_factory=popen)
        stream.start()
        self.assertTrue(stream.close())
        popen.assert_not_called()

    def test_stalled_pipe_is_bounded_and_does_not_block_capture_submission(self):
        processes = []
        def stalled(argv, **kwargs):
            p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
            processes.append(p)
            return p
        config = replace(self.config, write_timeout_sec=.2)
        stream = RtpStreamer(config, route_validator=lambda c: None, process_factory=stalled)
        stream.start()
        frame = np.zeros((480, 640, 3), np.uint8)
        started = time.monotonic()
        for i in range(30):
            stream.submit_frame(frame, i)
        self.assertLess(time.monotonic() - started, .5)
        self.assertLessEqual(stream.snapshot()['pending_frames'], 1)
        self.assertTrue(wait_until(lambda: stream.snapshot()['status'] == 'ERROR'))
        self.assertTrue(stream.close())
        self.assertIsNotNone(processes[0].poll())

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg required for actual RTP transport')
    def test_actual_ffmpeg_emits_h264_rtp_and_stops_when_camera_stales(self):
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(receiver.close)
        receiver.bind(('127.0.0.1', 0))
        receiver.settimeout(.05)
        packets = []
        with tempfile.TemporaryDirectory() as directory:
            config = RtpStreamConfig(destination='127.0.0.1', local_address='127.0.0.1',
                                     port=receiver.getsockname()[1], width=32, height=24,
                                     fps=10, bitrate_kbps=60, max_frame_age_sec=.4,
                                     sdp_path=str(Path(directory) / 'stream.sdp'))
            stream = RtpStreamer(config, route_validator=lambda c: None)
            try:
                stream.start()
                for i in range(12):
                    frame = np.full((24, 32, 3), i * 10, np.uint8)
                    self.assertTrue(stream.submit_frame(frame, i))
                    try:
                        packets.append(receiver.recv(2048))
                    except socket.timeout:
                        pass
                    time.sleep(.04)
                self.assertTrue(Path(config.sdp_path).is_file())
                self.assertGreater(len(packets), 2)
                self.assertTrue(all(p[0] >> 6 == 2 and p[1] & 0x7f == 96 for p in packets))
                self.assertTrue(all(len(p) <= 1000 for p in packets))
                self.assertTrue(wait_until(lambda: stream.snapshot()['status'] == 'ERROR', 2))
                self.assertIn('stale', stream.snapshot()['error'].lower())
            finally:
                self.assertTrue(stream.close())
                receiver.close()


class WorkerWiringTests(unittest.TestCase):
    """Run the actual capture-loop methods headlessly; no fake Qt event-loop claims."""
    def worker(self, broken_encoder=False, shared_yolo=None):
        class Signal:
            def __init__(self, *args): self.calls = []
            def emit(self, *args): self.calls.append(args)
        class Thread:
            def __init__(self, parent): pass
            def receivers(self, signal): return 0
        image = np.zeros((480, 640, 3), np.uint8)
        camera = Mock()
        camera.read.side_effect = lambda: self.one_frame(worker, image)
        camera_factory = Mock(return_value=camera)
        encoder = Mock()
        encoder.close.return_value = True
        if broken_encoder:
            encoder.submit_frame.side_effect = BrokenPipeError('encoder failed')
        namespace = dict(QThread=Thread, pyqtSignal=Signal, Optional=typing.Optional,
                         SharedYOLOInferenceWorker=object, EdgeAICamera=camera_factory,
                         RtpStreamer=Mock(return_value=encoder), RtpStreamConfig=RtpStreamConfig,
                         EncodedBandwidthMeter=Mock(), time=time,
                         prepare_preview_rgb=lambda *a: a[0])
        namespace['EncodedBandwidthMeter'].return_value.observe.return_value = None
        path = Path(__file__).parents[1] / 'src/edge_orchestrator/gui/ai_camera_worker.py'
        cls = next(n for n in ast.parse(path.read_text()).body
                   if isinstance(n, ast.ClassDef) and n.name == 'AICameraWorker')
        exec(compile(ast.Module(body=[cls], type_ignores=[]), str(path), 'exec'), namespace)
        self.encoder_factory = namespace['RtpStreamer']
        worker = namespace['AICameraWorker'](rtp_config={}, shared_yolo_worker=shared_yolo)
        worker.run()
        return worker, camera, camera_factory, encoder, image

    def one_frame(self, worker, image):
        worker._running = False
        return types.SimpleNamespace(raw_frame=image, frame_id=7, frame=image + 1,
                                     captured_at=123., metadata={}, event=None,
                                     jpeg=lambda **kw: b'jpeg')

    def test_one_capture_feeds_preview_and_raw_rtp_same_frame_identity(self):
        worker, camera, factory, encoder, image = self.worker()
        factory.assert_called_once()
        self.assertTrue(factory.call_args.kwargs['retain_raw_frame'])
        camera.open.assert_called_once()
        camera.read.assert_called_once()
        args = encoder.submit_frame.call_args.args
        self.assertIs(args[0], image)
        self.assertEqual(args[1], 7)
        self.assertEqual(worker.frame_packet_signal.calls[0][1], 7)
        encoder.close.assert_called_once()
        camera.close.assert_called_once()

    def test_capture_worker_passes_shared_ai_readiness_to_rtp(self):
        shared = Mock()
        shared.ready_event = threading.Event()
        self.worker(shared_yolo=shared)
        self.assertIs(self.encoder_factory.call_args.kwargs['ready_event'], shared.ready_event)

    def test_encoder_failure_keeps_ai_camera_and_preview_out_of_error_state(self):
        worker, camera, factory, encoder, image = self.worker(True)
        self.assertEqual(worker.error_signal.calls, [])
        self.assertNotIn(('ERROR',), worker.status_signal.calls)
        self.assertEqual(worker.rtp_status_signal.calls[0][1], 'ERROR')
        self.assertEqual(len(worker.frame_packet_signal.calls), 1)
        camera.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
