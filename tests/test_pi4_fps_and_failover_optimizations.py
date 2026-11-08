import asyncio
from pathlib import Path
import sys
import threading
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, Mock, patch

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
for _p in (str(ROOT_DIR), str(SRC_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import cv2
import numpy as np
from PyQt5.QtCore import Qt
from pysnmp.proto import rfc1905

from edge_orchestrator.core import config
from edge_orchestrator.Edge import layer2_motion as pkg_layer2
from edge_orchestrator.Edge import layer3_roi as pkg_layer3
from edge_orchestrator.Edge.edge_ai_camera import EdgeAICamera
from edge_orchestrator.Edge.layer1_motion import Camera
from edge_orchestrator.Edge.layer4_state import MayTrangThai_Cell4
from edge_orchestrator.Edge.pipeline_edge_ai import Pipeline
from edge_orchestrator.gui.ai_camera_worker import (
    AICameraWorker,
    PreparedPreviewFrame,
    SharedYOLOInferenceWorker,
    prepare_preview_rgb,
)
from edge_orchestrator.network.bandwidth_types import CollectorStatus
from edge_orchestrator.network.pysnmp_client import PySnmpV3Client
from edge_orchestrator.network.qos_telemetry import QoSTelemetryWorker
from edge_orchestrator.network.snmp_bandwidth import (
    CiscoBandwidthCollector,
    InterfaceCounters,
    SnmpReadError,
    SnmpTimeoutError,
)
from edge_orchestrator.network.snmp_bandwidth_worker import CiscoBandwidthWorker
from tests.test_dashboard_qos import DASHBOARD, DashboardHarness, NAMESPACE
import ast


# Bind updated Dashboard methods for headless AST verification
NAMESPACE["PreparedPreviewFrame"] = PreparedPreviewFrame
for _method_name in (
    "_on_real_camera_frame",
    "_on_real_camera_frame_packet",
    "_sync_camera_recheck_intervals",
    "_on_ai_state_change",
    "_recalculate_target_policy",
    "_queue_telegram_alert",
):
    _node = next(
        n for n in DASHBOARD.body
        if isinstance(n, ast.FunctionDef) and n.name == _method_name
    )
    exec(
        compile(ast.Module(body=[_node], type_ignores=[]), "dashboard.py", "exec"),
        NAMESPACE,
    )
    setattr(DashboardHarness, _method_name, NAMESPACE[_method_name])


class MotionPipelineEquivalenceAndSafetyTests(unittest.TestCase):
    """Nhóm A: Kiểm chứng độ tương đương toán học & an toàn 100% của luồng phát hiện di chuyển."""

    def test_01_laplacian_cv16s_matches_cv64f_to_machine_precision(self):
        rng = np.random.default_rng(41900966)
        black = np.zeros((480, 640), dtype=np.uint8)
        white = np.full((480, 640), 255, dtype=np.uint8)
        gradient = np.tile(np.linspace(0, 255, 640, dtype=np.uint8), (480, 1))
        sharp_box = np.zeros((480, 640), dtype=np.uint8)
        sharp_box[120:360, 180:460] = 240
        blurry_box = cv2.GaussianBlur(sharp_box, (31, 31), 9)
        checker = np.indices((480, 640)).sum(axis=0) % 2
        checker = (checker * 255).astype(np.uint8)
        warehouse_tex = rng.integers(0, 256, size=(480, 640), dtype=np.uint8)

        samples = [
            ("black", black),
            ("white", white),
            ("gradient", gradient),
            ("sharp_box", sharp_box),
            ("blurry_box", blurry_box),
            ("checker_extreme_1020", checker),
            ("random_warehouse", warehouse_tex),
        ]
        for name, gray in samples:
            with self.subTest(pattern=name):
                ref_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
                ref_blurry = ref_score < 100.0
                opt_blurry, opt_score = pkg_layer2.kiem_tra_anh_mo(gray, 100.0)
                self.assertEqual(opt_blurry, ref_blurry)
                self.assertAlmostEqual(opt_score, ref_score, places=5)

    def test_02_fast_idle_skips_heavy_ops_without_missing_any_motion_decision(self):
        frames = []
        base = np.zeros((480, 640, 3), dtype=np.uint8)
        # 5 khung tĩnh hoàn toàn
        for _ in range(5):
            frames.append(base.copy())
        # 2 khung nhiễu hạt rời rạc (>500 pixel đổi màu nhưng contour < 40)
        noisy = base.copy()
        noisy[::6, ::6] = 255
        frames.append(noisy)
        frames.append(base.copy())
        # 2 khung có chuyển động thật nhưng nằm NGOÀI ROI [10:90, 10:90]
        outside = base.copy()
        outside[10:90, 10:90] = 255
        frames.append(outside)
        frames.append(base.copy())
        # 2 khung chuyển động lớn nhưng bị mờ (Laplacian < 100)
        blurry = base.copy()
        blurry[160:320, 200:360] = 200
        blurry = cv2.GaussianBlur(blurry, (35, 35), 10)
        frames.append(blurry)
        # 6 khung chuyển động sắc nét đi xuyên qua ROI
        for step in range(6):
            moving = base.copy()
            x = 170 + step * 18
            moving[150:290, x : x + 90] = 255
            frames.append(moving)

        prev_ref = None
        prev_opt = None
        for idx, frm in enumerate(frames):
            res_ref = pkg_layer3.process_layer3(prev_ref, frm, fast_idle=False)
            prev_ref = cv2.cvtColor(frm, cv2.COLOR_BGR2GRAY)

            res_opt, gray_opt = pkg_layer3.process_layer3(
                prev_opt, frm, fast_idle=True, return_gray=True
            )
            prev_opt = gray_opt

            with self.subTest(frame_index=idx):
                self.assertEqual(res_opt["motion1"], res_ref["motion1"])
                self.assertEqual(res_opt["valid"], res_ref["valid"])
                self.assertEqual(res_opt["roi_ready"], res_ref["roi_ready"])
                np.testing.assert_array_equal(prev_opt, prev_ref)
                if res_ref["valid"]:
                    self.assertEqual(res_opt["roi_pixels"], res_ref["roi_pixels"])
                    np.testing.assert_array_equal(
                        res_opt["roi_mask"], res_ref["roi_mask"]
                    )

        # Chứng minh khi khung hình tĩnh (motion1 == False), filter_mask & kiem_tra_anh_mo KHÔNG bị gọi
        static_gray = np.zeros((480, 640), dtype=np.uint8)
        with patch.object(pkg_layer2, "filter_mask") as spy_filter, patch.object(
            pkg_layer2, "kiem_tra_anh_mo"
        ) as spy_blur:
            idle_res = pkg_layer2.process_frame(
                static_gray, static_gray, fast_idle=True
            )
            self.assertFalse(idle_res["valid"])
            spy_filter.assert_not_called()
            spy_blur.assert_not_called()

    def test_03_pipeline_step_reuses_single_bgr2gray_and_triggers_identically(self):
        pipe = Pipeline(None, cooldown=2.0, clock=lambda: 10.0)
        bgr2gray_calls = [0]
        real_cvt = cv2.cvtColor

        def counting_cvt(src, code, *args, **kwargs):
            if code == cv2.COLOR_BGR2GRAY:
                bgr2gray_calls[0] += 1
            return real_cvt(src, code, *args, **kwargs)

        triggered_at = []
        with patch("cv2.cvtColor", side_effect=counting_cvt):
            for i in range(8):
                frm = np.zeros((480, 640, 3), dtype=np.uint8)
                x = 175 + i * 14
                frm[140:280, x : x + 100] = 255
                _, res = pipe.step(frm)
                if res["triggered"]:
                    triggered_at.append(i)

        # 8 frame xử lý -> đúng 8 lần gọi BGR2GRAY (trước đây tốn 16 lần!)
        self.assertEqual(bgr2gray_calls[0], 8)
        # Frame 0 khởi tạo previous_gray; Frame 1..5 đủ 5 frame liên tiếp -> kích hoạt ở index 5
        self.assertEqual(triggered_at, [5])

    def test_04_picamera2_and_usb_v4l2_hardware_20_24_fps_exposure_and_buffer_config(self):
        # 1) Kiểm tra cấu hình Picamera2 (FrameDurationLimits 20-24 FPS + buffer_count=4 + lật 180 độ)
        fake_picam_cls = MagicMock()
        fake_picam_inst = MagicMock()
        fake_picam_cls.return_value = fake_picam_inst
        fake_transform_cls = MagicMock(side_effect=lambda **kw: kw)

        picam_mod = types.ModuleType("picamera2")
        picam_mod.Picamera2 = fake_picam_cls
        libcam_mod = types.ModuleType("libcamera")
        libcam_mod.Transform = fake_transform_cls

        with patch.dict(
            sys.modules, {"picamera2": picam_mod, "libcamera": libcam_mod}
        ):
            cam = Camera("picamera2", 0)
            fake_picam_inst.create_video_configuration.assert_called_once_with(
                main={"size": (640, 480), "format": "RGB888"},
                buffer_count=4,
                controls={"FrameDurationLimits": (41666, 50000)},
                transform={"hflip": True, "vflip": True},
            )
            cam.close()

        # 2) Kiểm tra cấu hình USB V4L2 trên phần cứng thật (MJPG, 640x480, FPS=24, BUFFERSIZE=1)
        fake_cap = MagicMock()
        fake_cap.isOpened.return_value = True
        with patch("cv2.VideoCapture", return_value=fake_cap) as patched_vc:
            edge_cam = EdgeAICamera(
                camera_index=0,
                source="usb",
                v4l2=True,
                async_ai=True,
                capture_factory=patched_vc,
            )
            edge_cam.open()
            set_calls = [c.args for c in fake_cap.set.call_args_list]
            self.assertIn(
                (cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG")), set_calls
            )
            self.assertIn((cv2.CAP_PROP_FRAME_WIDTH, 640), set_calls)
            self.assertIn((cv2.CAP_PROP_FRAME_HEIGHT, 480), set_calls)
            self.assertIn((cv2.CAP_PROP_FPS, 24), set_calls)
            self.assertIn((cv2.CAP_PROP_BUFFERSIZE, 1), set_calls)
            edge_cam.close()

    def test_05_slow_walker_at_24fps_still_triggers_5_consecutive_frames_cleanly(self):
        """Kiểm chứng vật lý Δt=41.7ms (24 FPS): người đi rất chậm (dịch 3px/frame) vẫn chốt đủ 5 frame trong ~0.21s."""
        now_box = [0.0]
        pipe = Pipeline(None, cooldown=2.0, clock=lambda: now_box[0])
        triggered_time = None

        for step in range(10):
            frm = np.zeros((480, 640, 3), dtype=np.uint8)
            # Bóng người cao 120px, rộng 45px, mỗi frame (41.7ms) chỉ nhích 3px (~72 px/giây, đi rón rén chậm)
            x = 200 + step * 3
            frm[160:280, x : x + 45] = 220
            _, res = pipe.step(frm)
            if res["triggered"] and triggered_time is None:
                triggered_time = now_box[0]
            now_box[0] += 1.0 / 24.0

        self.assertIsNotNone(triggered_time)
        # Kích hoạt ngay ở frame thứ 6 (5 khoảng Δt = 5 / 24s ≈ 0.208s)
        self.assertAlmostEqual(triggered_time, 5.0 / 24.0, places=3)

    def test_06_config_ttl_recheck_and_box_age_invariants_prevent_expiry_and_blink(self):
        # Đảm bảo chu kỳ quét khi kho trống nhỏ hơn TTL ít nhất 1.5s -> không bao giờ hụt TTL khi đếm ngược 10s
        self.assertLessEqual(
            config.AI_IDLE_RECHECK_SEC + 1.5, config.AI_OBSERVATION_TTL_SEC
        )
        # Đảm bảo chu kỳ giữ ALERT nhỏ hơn tuổi thọ bounding box ít nhất 1.2s -> khung xanh PERSON không bao giờ chớp tắt
        self.assertLessEqual(
            config.AI_ALERT_HOLD_RECHECK_SEC + 1.2, config.AI_MAX_BOX_AGE_SEC
        )
        # Đảm bảo khi có chuyển động (triggered=True), should_infer bật TỨC THÌ bất chấp idle_recheck_sec=2.5s
        pipe = Pipeline(
            None,
            clock=lambda: 100.0,
            idle_recheck_sec=2.5,
            active_recheck_sec=0.4,
            max_box_age_sec=2.4,
        )
        for i in range(6):
            frm = np.zeros((480, 640, 3), dtype=np.uint8)
            frm[150:270, 180 + i * 12 : 260 + i * 12] = 255
            _, res = pipe.step(frm)
        self.assertTrue(res["triggered"])
        self.assertTrue(res["should_infer"])
        self.assertEqual(res["infer_reason"], "motion_trigger")


class GuiDecouplingAndDynamicRecheckTests(unittest.TestCase):
    """Nhóm B: Tách nhịp vẽ GUI 20 FPS, chuyển đổi RGB trên luồng phụ & giãn động YOLO Recheck."""

    def test_07_prepare_preview_rgb_downscales_and_produces_contiguous_rgb(self):
        bgr = np.zeros((480, 640, 3), dtype=np.uint8)
        bgr[:, :] = [15, 45, 230]  # B=15, G=45, R=230
        snapshot = bgr.copy()

        prepared = prepare_preview_rgb(bgr, 320, 240)
        self.assertIsInstance(prepared, PreparedPreviewFrame)
        self.assertEqual(prepared.rgb.shape, (240, 320, 3))
        self.assertTrue(prepared.rgb.flags["C_CONTIGUOUS"])
        # Đã đổi sang RGB (R=230, G=45, B=15)
        self.assertEqual(tuple(prepared.rgb[0, 0]), (230, 45, 15))
        # Không làm biến đổi ảnh BGR gốc 640x480 dùng cho AI/Telegram
        np.testing.assert_array_equal(bgr, snapshot)

    def test_08_dashboard_renders_prepared_preview_without_main_thread_resize_or_cvtcolor(self):
        d = DashboardHarness()
        fake_lbl = MagicMock()
        fake_lbl.width.return_value = 400
        fake_lbl.height.return_value = 300
        d.lbl_camera = fake_lbl
        d.lbl_cam03 = fake_lbl

        fake_worker = MagicMock()
        fake_worker.latest_emitted_frame_id = 5
        d.ai_camera_workers = {"CAM03": fake_worker}

        bgr = np.zeros((480, 640, 3), dtype=np.uint8)
        prepared = prepare_preview_rgb(bgr, 400, 300)

        with patch.dict(
            NAMESPACE,
            {
                "QImage": MagicMock(return_value="qimg_obj"),
                "QPixmap": MagicMock(fromImage=MagicMock(return_value="qpix_obj")),
            },
        ), patch("cv2.resize", wraps=cv2.resize) as spy_resize, patch(
            "cv2.cvtColor", wraps=cv2.cvtColor
        ) as spy_cvt:
            d._on_real_camera_frame_packet("CAM03", 5, 123.4, prepared)
            # Main Thread KHÔNG tốn bất kỳ lệnh cv2.resize hay cv2.cvtColor nào!
            spy_resize.assert_not_called()
            spy_cvt.assert_not_called()
            fake_worker.set_target_label_size.assert_called_once_with(400, 300)
            fake_lbl.setPixmap.assert_called_once_with("qpix_obj")

    def test_09_ai_camera_worker_processes_100_percent_frames_while_throttling_gui_to_20fps(self):
        """Chạy 10 frame chụp siêu nhanh (cách nhau 5ms) có chứa chuỗi 5 frame di chuyển:
        Tầng 1–4 xử lý đủ 100% 10 frame để bắt trọn motion_trigger và giữ ảnh Telegram 640x480,
        trong khi tín hiệu vẽ GUI được điều tiết mượt mà."""
        seq_frames = []
        for idx in range(10):
            frm = np.zeros((480, 640, 3), dtype=np.uint8)
            if 1 <= idx <= 6:
                x = 170 + idx * 14
                frm[140:280, x : x + 95] = 255
            seq_frames.append(frm)

        read_idx = [0]
        fake_cap = MagicMock()
        fake_cap.isOpened.return_value = True

        yolo_worker = SharedYOLOInferenceWorker(
            detector=MagicMock(
                infer=MagicMock(
                    return_value={
                        "detected": True,
                        "class_id": 0,
                        "persons": [{"xyxy": [10, 10, 90, 160], "score": 0.9}],
                        "inference_ms": 5.0,
                    }
                )
            ),
            camera_order=("CAM03",),
        )
        submitted_jobs = []
        orig_submit = yolo_worker.submit_job

        def spy_submit(job):
            submitted_jobs.append(job)
            return orig_submit(job)

        yolo_worker.submit_job = spy_submit
        yolo_worker.start()

        cam_worker = AICameraWorker(
            camera_index=0,
            source="usb",
            source_id="CAM03",
            shared_yolo_worker=yolo_worker,
            max_gui_fps=20.0,
            preview_max_size=(320, 240),
            idle_recheck_sec=10.0,
        )

        def fake_read():
            i = read_idx[0]
            if i >= len(seq_frames):
                cam_worker.request_stop() if hasattr(cam_worker, "request_stop") else setattr(cam_worker, "_running", False)
                return True, seq_frames[-1]
            read_idx[0] += 1
            if read_idx[0] == len(seq_frames):
                cam_worker._running = False
            time.sleep(0.004)
            return True, seq_frames[i]

        fake_cap.read.side_effect = fake_read
        emitted_gui_packets = []
        cam_worker.frame_packet_signal.connect(
            lambda src, fid, cap_at, payload: emitted_gui_packets.append((fid, payload))
        )

        try:
            with patch(
                "edge_orchestrator.Edge.edge_ai_camera.open_capture",
                return_value=fake_cap,
            ):
                cam_worker.run()

            # Đủ 10 frame đã đi qua Tầng 1-4 -> bắt trúng đúng 1 lần motion_trigger ở frame thứ 6
            self.assertEqual(cam_worker.triggers_count, 1)
            motion_jobs = [j for j in submitted_jobs if j.reason == "motion_trigger"]
            self.assertEqual(len(motion_jobs), 1)
            # Ảnh full_frame phục vụ Telegram vẫn nguyên vẹn 640x480 BGR!
            self.assertEqual(motion_jobs[0].full_frame.shape, (480, 640, 3))
            # GUI được điều tiết (< 10 gói trong 40ms) nhưng LUÔN phát frame đầu tiên & frame có motion_trigger!
            self.assertLess(len(emitted_gui_packets), 10)
            emitted_ids = [fid for fid, _ in emitted_gui_packets]
            self.assertIn(1, emitted_ids)
            self.assertIn(motion_jobs[0].frame_id, emitted_ids)
            self.assertIsInstance(emitted_gui_packets[0][1], PreparedPreviewFrame)
            self.assertEqual(emitted_gui_packets[0][1].rgb.shape, (240, 320, 3))
        finally:
            yolo_worker.stop(timeout_ms=2000)

    def test_10_dynamic_yolo_recheck_fast_04s_in_normal_and_relaxed_10s_in_alert(self):
        d = DashboardHarness()
        d.simulate_ai = True
        d.telegram_alerts = MagicMock(enabled=False)
        d._apply_network_policy = MagicMock()
        d._ai_state = "NORMAL"
        d._detect_count = 0
        d.lbl_detect_count = MagicMock()
        d.lbl_status = MagicMock()
        d.lbl_mode_badge = MagicMock()

        edge_cam = EdgeAICamera(
            detector=MagicMock(),
            async_ai=True,
            active_recheck_sec=0.4,
            idle_recheck_sec=2.5,
        )
        edge_cam._initialize()
        worker = AICameraWorker(source_id="CAM03", active_recheck_sec=0.4)
        worker._camera_ref = edge_cam
        d.ai_camera_workers = {"CAM03": worker}

        # Chuyển sang ALERT -> tự động giãn active_recheck_sec lên 1.0s (giảm 60% tải YOLO)
        d._on_ai_state_change("ALERT")
        self.assertAlmostEqual(edge_cam._pipeline.active_recheck_sec, 1.0)

        # Quay về NORMAL -> tự động khôi phục 0.4s để sẵn sàng Fast-Attack cho lần xâm nhập kế tiếp
        d._on_ai_state_change("NORMAL")
        self.assertAlmostEqual(edge_cam._pipeline.active_recheck_sec, 0.4)


class SnmpBatchingAndKhung4FailoverTests(unittest.TestCase):
    """Nhóm C: Gom gói SNMPv3 theo thiết bị & bảo toàn 100% Khung 4 + mọi kịch bản Failover."""

    class BatchSpySnmpClient:
        def __init__(self, devices_cfg):
            self.resolve_calls = []
            self.read_calls = []
            self.timeout_hosts = set()
            self.missing_interfaces = set()  # (host, iface_name)
            self.nosuchinstance_ports = set()  # (host, if_index)
            self.down_ports = set()  # (host, if_index)
            self.octets_by_port = {}  # (host, if_index) -> (in_oct, out_oct)
            self._if_map = {}

            next_idx_by_host = {}
            for cfg in devices_cfg.values():
                h = cfg["host"]
                iface = cfg["interface"]
                idx = next_idx_by_host.get(h, 1)
                self._if_map[(h, iface)] = idx
                self.octets_by_port[(h, idx)] = (1_000_000, 2_000_000)
                next_idx_by_host[h] = idx + 1

        def resolve_if_index(self, host, interface_name):
            res = self.resolve_if_indices(host, [interface_name])
            if interface_name not in res:
                raise SnmpReadError(f"Not found: {interface_name}")
            return res[interface_name]

        def resolve_if_indices(self, host, interface_names):
            self.resolve_calls.append((host, tuple(interface_names)))
            if host in self.timeout_hosts:
                raise SnmpTimeoutError(f"SNMP timeout from {host}")
            out = {}
            for name in interface_names:
                if (host, name) in self.missing_interfaces:
                    continue
                if (host, name) in self._if_map:
                    out[name] = self._if_map[(host, name)]
            return out

        def read_interface(self, host, if_index):
            res = self.read_interfaces(host, [if_index])
            entry = res[if_index]
            if isinstance(entry, Exception):
                raise entry
            return entry

        def read_interfaces(self, host, if_indices):
            self.read_calls.append((host, tuple(if_indices)))
            if host in self.timeout_hosts:
                raise SnmpTimeoutError(f"SNMP timeout from {host}")
            out = {}
            for idx in if_indices:
                if (host, idx) in self.nosuchinstance_ports:
                    out[idx] = SnmpReadError(
                        f"NoSuchInstance for ifIndex {idx} on {host}"
                    )
                    continue
                oper = 2 if (host, idx) in self.down_ports else 1
                in_oct, out_oct = self.octets_by_port.get(
                    (host, idx), (1_000_000, 2_000_000)
                )
                out[idx] = InterfaceCounters(
                    admin_status=1,
                    oper_status=oper,
                    in_octets=in_oct,
                    out_octets=out_oct,
                )
            return out

    def _make_full_topology_collector(self):
        mono = [100.0]
        wall = [1000.0]
        spy = self.BatchSpySnmpClient(config.SNMP_DEVICES)
        collector = CiscoBandwidthCollector(
            devices=config.SNMP_DEVICES,
            snmp_client=spy,
            offline_failures=3,
            rate_window_sec=6.0,
            monotonic_clock=lambda: mono[0],
            wall_clock=lambda: wall[0],
        )
        return collector, spy, mono, wall

    def test_11_15_snmp_sources_batch_into_5_host_requests_preserving_all_15_samples(self):
        collector, spy, mono, wall = self._make_full_topology_collector()
        worker = CiscoBandwidthWorker(
            collector=collector, interval_sec=2.0, max_workers=5
        )
        self.assertEqual(len(collector.source_ids), 15)
        self.assertEqual(len(collector.host_groups), 5)

        with ThreadPoolExecutor(max_workers=5) as executor:
            # Chu kỳ 1: Khởi tạo baseline -> chỉ 5 lần walk + 5 lần GET (thay vì 15 walk + 15 GET!)
            first_samples = worker._poll_cycle(executor)
            self.assertEqual(len(spy.resolve_calls), 5)
            self.assertEqual(len(spy.read_calls), 5)
            self.assertEqual(
                [s.source_id for s in first_samples], list(collector.source_ids)
            )
            self.assertTrue(
                all(s.status == CollectorStatus.WARMING_UP for s in first_samples)
            )

            # Tăng counter sau 6.0s (mô phỏng tải 8 Mbps = +6_000_000 bytes / 6s)
            mono[0] += 6.0
            wall[0] += 6.0
            for key in list(spy.octets_by_port):
                in_o, out_o = spy.octets_by_port[key]
                spy.octets_by_port[key] = (in_o + 6_000_000, out_o + 6_000_000)

            second_samples = worker._poll_cycle(executor)
            # Không gọi lại resolve_if_indices, chỉ gửi đúng 5 gói GET cho 15 cổng!
            self.assertEqual(len(spy.resolve_calls), 5)
            self.assertEqual(len(spy.read_calls), 10)
            self.assertEqual(len(second_samples), 15)
            for s in second_samples:
                self.assertEqual(s.status, CollectorStatus.ONLINE)
                self.assertAlmostEqual(s.value_mbps, 8.0, places=3)

    def test_12_failover_single_wan_link_down_marks_only_r1_wan_down_while_r1_other_ports_stay_online(self):
        """Kịch bản Failover đứt cáp lẻ R1 Gi0/0 WAN: R1_WAN đỏ INTERFACE_DOWN, nhưng R1_DSW1 & R1_R2
        trên cùng gói gộp R1 vẫn xanh ONLINE và nhánh dự phòng R2_WAN gánh tải 8 Mbps bình thường."""
        collector, spy, mono, wall = self._make_full_topology_collector()
        collector.poll_all()

        r1_host = config.SNMP_DEVICES["R1_WAN"]["host"]
        r1_wan_idx = spy._if_map[(r1_host, "GigabitEthernet0/0")]
        spy.down_ports.add((r1_host, r1_wan_idx))

        mono[0] += 6.0
        wall[0] += 6.0
        for key in list(spy.octets_by_port):
            in_o, out_o = spy.octets_by_port[key]
            spy.octets_by_port[key] = (in_o + 6_000_000, out_o + 6_000_000)

        by_id = {s.source_id: s for s in collector.poll_all()}
        self.assertEqual(by_id["R1_WAN"].status, CollectorStatus.INTERFACE_DOWN)
        self.assertEqual(by_id["R1_DSW1"].status, CollectorStatus.ONLINE)
        self.assertEqual(by_id["R1_R2"].status, CollectorStatus.ONLINE)
        self.assertEqual(by_id["R2_WAN"].status, CollectorStatus.ONLINE)
        self.assertAlmostEqual(by_id["R2_WAN"].value_mbps, 8.0, places=3)

    def test_13_failover_entire_router_r1_power_off_marks_r1_offline_without_delaying_r2_or_dsw2(self):
        """Kịch bản Failover sập nguồn toàn bộ R1: 3 cổng R1 chuyển OFFLINE sau 3 chu kỳ,
        không làm nghẽn ThreadPool 5 luồng của R2/DSW2, và tự phục hồi WARMING_UP -> ONLINE khi bật lại."""
        collector, spy, mono, wall = self._make_full_topology_collector()
        worker = CiscoBandwidthWorker(
            collector=collector, interval_sec=2.0, max_workers=5
        )
        with ThreadPoolExecutor(max_workers=5) as executor:
            worker._poll_cycle(executor)

            r1_host = config.SNMP_DEVICES["R1_WAN"]["host"]
            spy.timeout_hosts.add(r1_host)

            for step in range(1, 4):
                mono[0] += 2.0
                wall[0] += 2.0
                for key in list(spy.octets_by_port):
                    in_o, out_o = spy.octets_by_port[key]
                    spy.octets_by_port[key] = (in_o + 1_000_000, out_o + 1_000_000)
                by_id = {s.source_id: s for s in worker._poll_cycle(executor)}
                expected_r1 = (
                    CollectorStatus.OFFLINE
                    if step >= 3
                    else CollectorStatus.COLLECTOR_ERROR
                )
                for r1_sid in ("R1_DSW1", "R1_R2", "R1_WAN"):
                    self.assertEqual(by_id[r1_sid].status, expected_r1)

            # Trong lúc R1 OFFLINE, R2 & DSW2 vẫn ONLINE đầy đủ
            self.assertEqual(by_id["R2_WAN"].status, CollectorStatus.ONLINE)
            self.assertEqual(by_id["DSW2_R2"].status, CollectorStatus.ONLINE)

            # Bật nguồn R1 trở lại -> lấy baseline mới (WARMING_UP) rồi lên ONLINE
            spy.timeout_hosts.discard(r1_host)
            mono[0] += 2.0
            wall[0] += 2.0
            recovered = {s.source_id: s for s in worker._poll_cycle(executor)}
            self.assertEqual(
                recovered["R1_WAN"].status, CollectorStatus.WARMING_UP
            )

    def test_14_per_port_nosuchinstance_isolation_in_pysnmp_client_and_collector(self):
        """Kiểm chứng an toàn RFC 3416: nếu xóa Port-channel1 (trả về NoSuchInstance trong gói 12 OID),
        chỉ riêng DSW1_PO1 báo COLLECTOR_ERROR còn DSW1_ASW và DSW1_R1 vẫn ONLINE bình thường!"""
        client = PySnmpV3Client(
            username="snmpuser",
            auth_key="authpass1234",
            priv_key="privpass1234",
        )
        fake_session = MagicMock()
        var_binds = [
            # if_index=1 (Gi0/2): hợp lệ
            ("1.3.6.1.2.1.2.2.1.7.1", 1),
            ("1.3.6.1.2.1.2.2.1.8.1", 1),
            ("1.3.6.1.2.1.31.1.1.1.6.1", 5000),
            ("1.3.6.1.2.1.31.1.1.1.10.1", 6000),
            # if_index=2 (Port-channel1 bị xóa): trả về NoSuchInstance!
            ("1.3.6.1.2.1.2.2.1.7.2", rfc1905.NoSuchInstance()),
            ("1.3.6.1.2.1.2.2.1.8.2", rfc1905.NoSuchInstance()),
            ("1.3.6.1.2.1.31.1.1.1.6.2", rfc1905.NoSuchInstance()),
            ("1.3.6.1.2.1.31.1.1.1.10.2", rfc1905.NoSuchInstance()),
            # if_index=3 (Gi0/0): hợp lệ
            ("1.3.6.1.2.1.2.2.1.7.3", 1),
            ("1.3.6.1.2.1.2.2.1.8.3", 1),
            ("1.3.6.1.2.1.31.1.1.1.6.3", 9000),
            ("1.3.6.1.2.1.31.1.1.1.10.3", 12000),
        ]

        async def fake_get_cmd(*args, **kwargs):
            return (None, 0, 0, var_binds)

        async def fake_create_target(host):
            return "target"

        with patch(
            "edge_orchestrator.network.pysnmp_client.get_cmd",
            side_effect=fake_get_cmd,
        ), patch.object(client, "_create_target", side_effect=fake_create_target):
            res = asyncio.run(
                client._read_interfaces_async(
                    fake_session, "10.255.255.11", [1, 2, 3]
                )
            )

        self.assertIsInstance(res[1], InterfaceCounters)
        self.assertEqual(res[1].in_octets, 5000)
        self.assertIsInstance(res[2], SnmpReadError)
        self.assertIsInstance(res[3], InterfaceCounters)
        self.assertEqual(res[3].out_octets, 12000)

        # Kiểm chứng ở tầng Collector: DSW1_PO1 lỗi độc lập, DSW1_ASW & DSW1_R1 vẫn ONLINE
        collector, spy, mono, wall = self._make_full_topology_collector()
        collector.poll_all()
        dsw1_host = config.SNMP_DEVICES["DSW1_PO1"]["host"]
        po1_idx = spy._if_map[(dsw1_host, "Port-channel1")]
        spy.nosuchinstance_ports.add((dsw1_host, po1_idx))

        mono[0] += 6.0
        wall[0] += 6.0
        by_id = {
            s.source_id: s for s in collector.poll_host_group(dsw1_host)
        }
        self.assertEqual(by_id["DSW1_PO1"].status, CollectorStatus.COLLECTOR_ERROR)
        self.assertIsNone(collector._states["DSW1_PO1"].if_index)
        self.assertEqual(by_id["DSW1_ASW"].status, CollectorStatus.ONLINE)
        self.assertEqual(by_id["DSW1_R1"].status, CollectorStatus.ONLINE)

    def test_15_pysnmp_resolve_if_indices_walks_once_and_stops_early(self):
        client = PySnmpV3Client(
            username="snmpuser",
            auth_key="authpass1234",
            priv_key="privpass1234",
        )
        visited_rows = [0]

        class FakeVal:
            def __init__(self, txt):
                self._txt = txt

            def prettyPrint(self):
                return self._txt

        table = [
            ((1, 3, 6, 1, 2, 1, 31, 1, 1, 1, 1, 1), FakeVal("Gi0/0")),
            ((1, 3, 6, 1, 2, 1, 31, 1, 1, 1, 1, 2), FakeVal("Gi0/1")),
            ((1, 3, 6, 1, 2, 1, 31, 1, 1, 1, 1, 12), FakeVal("Gi0/2.99")),
            ((1, 3, 6, 1, 2, 1, 31, 1, 1, 1, 1, 99), FakeVal("Loopback0")),
            ((1, 3, 6, 1, 2, 1, 31, 1, 1, 1, 1, 100), FakeVal("Null0")),
        ]

        async def fake_walk_cmd(*args, **kwargs):
            for oid, val in table:
                visited_rows[0] += 1
                yield (None, 0, 0, [(oid, val)])

        async def fake_create_target(host):
            return "target"

        with patch(
            "edge_orchestrator.network.pysnmp_client.walk_cmd",
            side_effect=fake_walk_cmd,
        ), patch.object(client, "_create_target", side_effect=fake_create_target):
            resolved = asyncio.run(
                client._resolve_if_indices_async(
                    MagicMock(),
                    "10.255.255.1",
                    [
                        "GigabitEthernet0/2.99",
                        "GigabitEthernet0/1",
                        "GigabitEthernet0/0",
                    ],
                )
            )

        self.assertEqual(
            resolved,
            {
                "GigabitEthernet0/0": 1,
                "GigabitEthernet0/1": 2,
                "GigabitEthernet0/2.99": 12,
            },
        )
        # Dừng walk ngay ở dòng thứ 3 khi đã tìm đủ 3 cổng, không duyệt tiếp Loopback0/Null0!
        self.assertEqual(visited_rows[0], 3)


class StreamingTsharkTelemetryTests(unittest.TestCase):
    """Nhóm D: Kiểm chứng phiên tshark -l chạy nền liên tục (loại bỏ fork 1.17GB mỗi 2s)."""

    def test_16_tshark_compute_metrics_from_lines_bitrate_udp_gro_loss_and_jitter(self):
        lines = [
            "8000\t1200\t100\t0.010",
            "8000\t2300\t101\t0.018",  # Gói gộp GRO (~2 gói) nhảy tới seq 103 -> 0% loss!
            "8000\t1200\t103\t0.026",
            "8002\t1000\t50\t0.005",
            "8002\t1000\t52\t0.015",  # Mất seq 51 -> 1/3 = 33.33% loss
        ]
        ports = {
            "8000": {"ID": "CAM01", "IP": "192.168.10.11"},
            "8002": {"ID": "CAM02", "IP": "192.168.10.12"},
        }
        metrics = {
            m["ID"]: m
            for m in QoSTelemetryWorker.compute_metrics_from_lines(
                lines, 2.0, telemetry_ports=ports
            )
        }
        self.assertAlmostEqual(metrics["CAM01"]["Bitrate_Mbps"], 0.019, places=3)
        self.assertEqual(metrics["CAM01"]["Loss_Percent"], 0.0)
        self.assertAlmostEqual(metrics["CAM01"]["Jitter_ms"], 8.0, places=2)
        self.assertEqual(metrics["CAM02"]["Loss_Percent"], 33.33)

    def test_17_streaming_tshark_worker_spawns_only_one_process_across_multiple_windows_and_stops_fast(self):
        emitted_batches = []
        popen_calls = []

        class FakeStreamingPopen:
            def __init__(self, cmd, **kwargs):
                popen_calls.append(cmd)
                self._terminated = threading.Event()
                self.stderr = MagicMock(read=MagicMock(return_value=""))

                def _gen():
                    yield "8000\t1250\t1\t0.010\n"
                    yield "8000\t1250\t2\t0.020\n"
                    # Giữ tiến trình sống qua nhiều cửa sổ (kể cả cửa sổ im lặng NO_STREAM)
                    while not self._terminated.wait(timeout=0.05):
                        pass

                self.stdout = _gen()

            def poll(self):
                return 0 if self._terminated.is_set() else None

            def terminate(self):
                self._terminated.set()

            def wait(self, timeout=None):
                self._terminated.wait(timeout=timeout)
                return 0

            def kill(self):
                self._terminated.set()

        worker = QoSTelemetryWorker(simulate=False)
        worker.telemetry_signal.connect(
            lambda m: emitted_batches.append(m), Qt.DirectConnection
        )

        with patch(
            "edge_orchestrator.network.qos_telemetry.BANDWIDTH_POLL_INTERVAL_SEC",
            0.2,
        ), patch(
            "edge_orchestrator.network.qos_telemetry.subprocess.Popen",
            side_effect=FakeStreamingPopen,
        ):
            runner = threading.Thread(target=worker.run, daemon=True)
            runner.start()
            deadline = time.monotonic() + 2.0
            while len(emitted_batches) < 2 and time.monotonic() < deadline:
                time.sleep(0.02)

            t_stop = time.monotonic()
            worker.stop()
            runner.join(timeout=1.0)
            self.assertFalse(runner.is_alive())
            stop_ms = (time.monotonic() - t_stop) * 1000.0

        # Chỉ spawn ĐÚNG 1 tiến trình tshark -l duy nhất qua nhiều cửa sổ đo!
        self.assertEqual(len(popen_calls), 1)
        self.assertIn("-l", popen_calls[0])
        self.assertIn("-n", popen_calls[0])
        self.assertGreaterEqual(len(emitted_batches), 2)
        # Cửa sổ 1 có dữ liệu (>0 Mbps), cửa sổ 2 im lặng vẫn phát đều đặn 0.0 Mbps (NO_STREAM) mà không treo!
        cam01_w1 = next(x for x in emitted_batches[0] if x["Port"] == 8000)
        cam01_w2 = next(x for x in emitted_batches[1] if x["Port"] == 8000)
        self.assertGreater(cam01_w1["Bitrate_Mbps"], 0.0)
        self.assertEqual(cam01_w2["Bitrate_Mbps"], 0.0)
        self.assertLess(stop_ms, 300.0)

    def test_18_streaming_tshark_worker_emits_collector_error_on_nonzero_exit(self):
        errors = []

        class FailingPopen:
            def __init__(self, cmd, **kwargs):
                self.stdout = iter([])
                self.stderr = MagicMock(
                    read=MagicMock(return_value="tshark: Permission denied on tailscale0")
                )

            def poll(self):
                return 1

            def terminate(self):
                pass

            def wait(self, timeout=None):
                return 1

        worker = QoSTelemetryWorker(simulate=False)
        worker.collector_error_signal.connect(
            lambda msg: errors.append(msg), Qt.DirectConnection
        )

        with patch(
            "edge_orchestrator.network.qos_telemetry.BANDWIDTH_POLL_INTERVAL_SEC",
            0.2,
        ), patch(
            "edge_orchestrator.network.qos_telemetry.subprocess.Popen",
            side_effect=FailingPopen,
        ):
            runner = threading.Thread(target=worker.run, daemon=True)
            runner.start()
            deadline = time.monotonic() + 1.5
            while not errors and time.monotonic() < deadline:
                time.sleep(0.02)
            worker.stop()
            runner.join(timeout=1.0)
            self.assertFalse(runner.is_alive())

        self.assertTrue(any("Permission denied" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
