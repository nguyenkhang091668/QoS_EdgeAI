import threading
import time
import unittest
from unittest.mock import Mock
import numpy as np

from edge_orchestrator.Edge.edge_ai_camera import EdgeAICamera
from edge_orchestrator.Edge.pipeline_edge_ai import Pipeline
from edge_orchestrator.gui.ai_camera_worker import (
    AICameraWorker,
    PendingROIJob,
    SharedYOLOInferenceWorker,
)
from tests.test_dashboard_qos import DASHBOARD, DashboardHarness, NAMESPACE
import ast
from edge_orchestrator.core import config
from edge_orchestrator.network.bandwidth_types import CollectorStatus

# Extract additional methods from MainDashboard for headless unit testing
NAMESPACE["config"] = config
NAMESPACE["np"] = np
NAMESPACE["CollectorStatus"] = CollectorStatus
NAMESPACE["BANDWIDTH_SOURCE_IDS"] = ("R1_WAN", "CAM03", "CAM04")
for method_name in (
    "_get_fresh_person_state",
    "_on_ai_camera_error",
    "_on_ai_camera_status",
    "_on_real_camera_frame_packet",
    "_tick_bandwidth",
):
    node = next(
        n for n in DASHBOARD.body if isinstance(n, ast.FunctionDef) and n.name == method_name
    )
    exec(compile(ast.Module(body=[node], type_ignores=[]), "dashboard.py", "exec"), NAMESPACE)
    setattr(DashboardHarness, method_name, NAMESPACE[method_name])


class Khung2DualCameraAITests(unittest.TestCase):
    def test_1_capture_does_not_wait_for_yolo_and_single_device_open(self):
        """1. Tách YOLO khỏi luồng capture; mỗi camera chỉ mở 1 thiết bị; preview không chờ inference."""
        cap = Mock()
        cap.isOpened.return_value = True
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        cap.read.return_value = (True, frame)
        factory = Mock(return_value=cap)

        cam = EdgeAICamera(
            camera_index=0,
            source="usb",
            source_id="CAM03",
            async_ai=True,
            capture_factory=factory,
        )
        cam.open()
        cam.open()  # Idempotent: chỉ mở 1 lần
        factory.assert_called_once_with(0)
        self.assertIsNone(cam._detector)  # Không nạp Detector trên luồng capture

        packet = cam.read()
        self.assertEqual(packet.source_id, "CAM03")
        self.assertEqual(packet.frame_id, 1)
        self.assertGreater(packet.captured_at, 0)
        cam.close()
        cap.release.assert_called_once()

    def test_2_single_pending_slot_per_camera_overwrites_and_lock_free_yolo(self):
        """2. Mỗi camera giữ tối đa 1 ROI đang chờ (mới đè cũ), không giữ lock khi chạy YOLO."""
        infer_entered = threading.Event()
        allow_infer_finish = threading.Event()
        observed_events = []

        class SlowDetector:
            def infer(self, roi):
                infer_entered.set()
                allow_infer_finish.wait(timeout=2.0)
                return {
                    "detected": True,
                    "class_id": 0,
                    "label": "person",
                    "score": 0.91,
                    "inference_ms": 12.0,
                    "persons": [{"xyxy": [10, 10, 80, 150], "score": 0.91}],
                }

        worker = SharedYOLOInferenceWorker(
            detector=SlowDetector(),
            camera_order=("CAM03", "CAM04"),
        )
        worker.register_camera(
            "CAM03",
            lambda res, frame_id=None, captured_at=None, inferred_at=None: observed_events.append(
                ("CAM03", frame_id, captured_at)
            ),
        )
        worker.register_camera(
            "CAM04",
            lambda res, frame_id=None, captured_at=None, inferred_at=None: observed_events.append(
                ("CAM04", frame_id, captured_at)
            ),
        )
        worker.start()
        try:
            # Wait until worker is running
            deadline = time.monotonic() + 1.0
            while not worker._running and time.monotonic() < deadline:
                time.sleep(0.01)

            roi = np.zeros((300, 300, 3), dtype=np.uint8)
            # Gửi job 1 của CAM03 -> worker lấy ngay và đi vào SlowDetector.infer()
            self.assertTrue(
                worker.submit_job(PendingROIJob("CAM03", 1, 100.0, time.monotonic(), roi))
            )
            self.assertTrue(infer_entered.wait(timeout=1.0))

            # Trong lúc YOLO đang chạy cho frame 1, luồng capture gửi liên tiếp frame 2, 3, 4 KHÔNG BỊ CHẶN
            t0 = time.monotonic()
            for fid in (2, 3, 4):
                self.assertTrue(
                    worker.submit_job(PendingROIJob("CAM03", fid, 100.0 + fid, time.monotonic(), roi))
                )
            submit_elapsed_ms = (time.monotonic() - t0) * 1000.0
            self.assertLess(submit_elapsed_ms, 20.0)

            # Slot của CAM03 chỉ giữ mẫu mới nhất (frame_id=4), đã ghi đè 2 mẫu cũ (2 và 3)
            self.assertEqual(worker._pending_slots["CAM03"].frame_id, 4)
            diag = worker.get_diagnostics()
            self.assertEqual(diag["CAM03"]["overwritten_slots"], 2)

            # Gửi thêm mẫu của CAM04 để kiểm tra lấy luân phiên (Round-Robin)
            self.assertTrue(
                worker.submit_job(PendingROIJob("CAM04", 10, 200.0, time.monotonic(), roi))
            )
            allow_infer_finish.set()

            deadline = time.monotonic() + 1.0
            while len(observed_events) < 3 and time.monotonic() < deadline:
                time.sleep(0.01)

            processed_order = [(src, fid) for src, fid, _ in observed_events]
            # Sau CAM03(frame 1), tới lượt luân phiên CAM04(frame 10), rồi quay lại CAM03(frame 4)
            self.assertEqual(processed_order, [("CAM03", 1), ("CAM04", 10), ("CAM03", 4)])
        finally:
            allow_infer_finish.set()
            self.assertTrue(worker.stop(timeout_ms=2000))

    def test_3_per_camera_observations_cam04_never_overwrites_cam03(self):
        """3. Phân biệt dữ liệu 2 camera: CAM04 báo không có người không được ghi đè CAM03 đang có người."""
        d = DashboardHarness()
        d._active_ai_cameras = {"CAM03", "CAM04"}
        d._ai_observations_by_camera = {}
        d._latest_person_detected = False
        d._latest_ai_observation_at = None
        d._latest_ai_event = None

        # CAM03 phát hiện có người ở frame 12
        d._on_ai_observation(
            True,
            {"source_id": "CAM03", "frame_id": 12, "captured_at": 1000.0},
        )
        # Ngay sau đó CAM04 báo không có người ở frame 5
        d._on_ai_observation(
            False,
            {"source_id": "CAM04", "frame_id": 5, "captured_at": 1000.1},
        )

        self.assertTrue(d._ai_observations_by_camera["CAM03"]["person_detected"])
        self.assertFalse(d._ai_observations_by_camera["CAM04"]["person_detected"])
        self.assertTrue(d._latest_person_detected)
        self.assertIs(d._get_fresh_person_state(), True)

        # Kết quả cũ đến trễ (frame_id=9 < 12 của CAM03) bị bỏ qua, không ghi đè frame 12
        d._on_ai_observation(
            False,
            {"source_id": "CAM03", "frame_id": 9, "captured_at": 999.0},
        )
        self.assertTrue(d._ai_observations_by_camera["CAM03"]["person_detected"])

    def test_4_periodic_checks_and_expired_data_is_not_confirmed_no_person(self):
        """4. Giữ kiểm tra định kỳ (khi đứng yên & khi không người); dữ liệu hết hạn không coi là không người."""
        now_box = [10.0]
        pipe = Pipeline(
            None,
            cooldown=2.0,
            clock=lambda: now_box[0],
            max_box_age_sec=2.0,
            active_recheck_sec=0.8,
            idle_recheck_sec=1.5,
        )
        static_frame = np.zeros((480, 640, 3), dtype=np.uint8)

        # Lúc t=10.0s chưa tới hạn định kỳ
        _, res0 = pipe.step(static_frame)
        self.assertFalse(res0["should_infer"])

        # Lúc t=11.6s (sau 1.6s >= idle_recheck_sec=1.5s): tự động yêu cầu kiểm tra định kỳ dù không có motion
        now_box[0] = 11.6
        _, res1 = pipe.step(static_frame)
        self.assertTrue(res1["periodic_due"])
        self.assertTrue(res1["should_infer"])
        self.assertEqual(res1["infer_reason"], "periodic_idle")

        # Giả sử kết quả AI trả về có người đứng yên
        pipe.apply_ai_result(
            {
                "detected": True,
                "class_id": 0,
                "persons": [{"xyxy": [20, 20, 100, 180], "score": 0.88}],
            },
            frame_id=2,
            captured_at=11.6,
            inferred_at=11.6,
        )
        # Khi người đứng yên, chỉ sau 0.85s (>= active_recheck_sec=0.8s) đã kích hoạt periodic_person
        now_box[0] = 12.45
        _, res2 = pipe.step(static_frame)
        self.assertTrue(res2["periodic_due"])
        self.assertEqual(res2["infer_reason"], "periodic_person")

        # Kiểm tra trên Dashboard: nếu mẫu có người bị hết hạn (age > TTL) mà chưa có mẫu âm tính mới -> trả về None (không phải False)
        d = DashboardHarness()
        d._active_ai_cameras = {"CAM03"}
        d._ai_observations_by_camera = {
            "CAM03": {
                "source_id": "CAM03",
                "person_detected": True,
                "observed_at": time.monotonic() - 10.0,  # Đã quá hạn TTL 3.5s
                "captured_at": 100.0,
                "frame_id": 5,
                "event": {},
            }
        }
        self.assertIsNone(d._get_fresh_person_state())

        # Chỉ khi có kết quả inference mới xác nhận person_detected=False còn hạn -> mới trả về False
        d._on_ai_observation(
            False,
            {"source_id": "CAM03", "frame_id": 6, "captured_at": 110.0},
        )
        self.assertIs(d._get_fresh_person_state(), False)

    def test_5_bounding_box_cleared_on_no_person_expired_or_error(self):
        """5. Quản lý tuổi bounding box: xóa box khi kết quả mới không người, quá cũ hoặc camera lỗi."""
        now_box = [50.0]
        pipe = Pipeline(
            None,
            clock=lambda: now_box[0],
            max_box_age_sec=2.0,
        )
        blank = np.zeros((480, 640, 3), dtype=np.uint8)

        # Có người lúc t=50.0 -> vẽ box màu xanh lá (0, 255, 0) tại x=150+20=170, y=100+20=120
        pipe.apply_ai_result(
            {
                "detected": True,
                "class_id": 0,
                "persons": [{"xyxy": [20, 20, 100, 120], "score": 0.95}],
            },
            frame_id=1,
            captured_at=50.0,
            inferred_at=50.0,
        )
        disp_fresh, res_fresh = pipe.step(blank)
        self.assertFalse(res_fresh["box_expired"])
        self.assertEqual(tuple(disp_fresh[120, 170]), (0, 255, 0))

        # Quá tuổi max_box_age_sec (t=52.5s -> age=2.5s > 2.0s) -> tự động ẩn box
        now_box[0] = 52.5
        disp_old, res_old = pipe.step(blank)
        self.assertTrue(res_old["box_expired"])
        self.assertEqual(tuple(disp_old[120, 170]), (0, 0, 0))

        # Có người lại lúc t=53.0s rồi nhận kết quả mới không có người lúc t=53.2s -> xóa box ngay
        pipe.apply_ai_result(
            {
                "detected": True,
                "class_id": 0,
                "persons": [{"xyxy": [20, 20, 100, 120], "score": 0.95}],
            },
            frame_id=2,
            captured_at=53.0,
            inferred_at=53.0,
        )
        now_box[0] = 53.2
        pipe.apply_ai_result(
            {"detected": False, "class_id": -1, "persons": []},
            frame_id=3,
            captured_at=53.2,
            inferred_at=53.2,
        )
        disp_neg, _ = pipe.step(blank)
        self.assertEqual(tuple(disp_neg[120, 170]), (0, 0, 0))

        # Khi camera lỗi -> clear_ai_state() xóa sạch box ngay lập tức
        pipe.apply_ai_result(
            {
                "detected": True,
                "class_id": 0,
                "persons": [{"xyxy": [20, 20, 100, 120], "score": 0.95}],
            },
            frame_id=4,
            captured_at=53.3,
            inferred_at=53.3,
        )
        pipe.clear_ai_state()
        disp_err, _ = pipe.step(blank)
        self.assertEqual(tuple(disp_err[120, 170]), (0, 0, 0))

    def test_6_safe_stop_wakes_worker_waits_for_inference_and_releases_cleanly(self):
        """6. Dừng và giải phóng an toàn: đánh thức worker đang chờ, đợi inference dở dang xong mới giải phóng."""
        in_flight = threading.Event()
        finish_flight = threading.Event()
        infer_completed_before_release = [False]

        class SafeDetector:
            def infer(self, roi):
                in_flight.set()
                finish_flight.wait(timeout=1.0)
                infer_completed_before_release[0] = True
                return {"detected": False, "class_id": -1, "persons": [], "inference_ms": 25.0}

        yolo_worker = SharedYOLOInferenceWorker(detector=SafeDetector())
        yolo_worker.register_camera("CAM03", lambda *a, **kw: None)
        yolo_worker.start()

        deadline = time.monotonic() + 1.0
        while not yolo_worker._running and time.monotonic() < deadline:
            time.sleep(0.01)

        roi = np.zeros((300, 300, 3), dtype=np.uint8)
        yolo_worker.submit_job(PendingROIJob("CAM03", 1, time.time(), time.monotonic(), roi))
        self.assertTrue(in_flight.wait(timeout=1.0))

        # Cho phép inference hoàn tất sau 30ms trong lúc stop() đang đợi
        threading.Timer(0.03, finish_flight.set).start()
        stopped = yolo_worker.stop(timeout_ms=2000)
        self.assertTrue(stopped)
        self.assertTrue(infer_completed_before_release[0])
        self.assertFalse(yolo_worker.isRunning())
        self.assertIsNone(yolo_worker._detector)

    def test_7_pi_acceptance_metrics_single_cam03_then_dual_cam03_cam04(self):
        """7. Đo và nghiệm thu: thử CAM03 trước, rồi 2 camera với YOLO 60ms mà preview không khựng."""
        class SimulatedPiNCNNDetector:
            def infer(self, roi):
                time.sleep(0.06)  # Giả lập 60ms NCNN inference trên Pi 4
                return {
                    "detected": True,
                    "class_id": 0,
                    "label": "person",
                    "score": 0.89,
                    "inference_ms": 60.0,
                    "persons": [{"xyxy": [30, 30, 120, 200], "score": 0.89}],
                }

        # Bước A: Chạy riêng CAM03, đóng rồi mở lại sang Bước B (CAM03 + CAM04)
        yolo1 = SharedYOLOInferenceWorker(
            detector=SimulatedPiNCNNDetector(),
            camera_order=("CAM03",),
        )
        yolo1.start()
        deadline = time.monotonic() + 1.0
        while not yolo1._running and time.monotonic() < deadline:
            time.sleep(0.01)

        cam03_results = []
        yolo1.register_camera("CAM03", lambda res, **kw: cam03_results.append((res, kw)))
        roi = np.zeros((300, 300, 3), dtype=np.uint8)
        yolo1.submit_job(PendingROIJob("CAM03", 1, time.time(), time.monotonic(), roi))
        deadline = time.monotonic() + 1.0
        while not cam03_results and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(cam03_results), 1)
        diag1 = yolo1.get_diagnostics()["CAM03"]
        self.assertGreaterEqual(diag1["last_infer_ms"], 50.0)
        self.assertTrue(yolo1.stop(timeout_ms=2000))

        # Bước B: Mở lại với 2 camera (CAM03 + CAM04) dùng chung 1 worker YOLO
        yolo2 = SharedYOLOInferenceWorker(
            detector=SimulatedPiNCNNDetector(),
            camera_order=("CAM03", "CAM04"),
        )
        yolo2.start()
        deadline = time.monotonic() + 1.0
        while not yolo2._running and time.monotonic() < deadline:
            time.sleep(0.01)

        dual_results = []
        yolo2.register_camera("CAM03", lambda res, **kw: dual_results.append(("CAM03", kw["frame_id"])))
        yolo2.register_camera("CAM04", lambda res, **kw: dual_results.append(("CAM04", kw["frame_id"])))

        # Mô phỏng luồng preview 30 FPS (33ms/frame) đẩy job vào lúc YOLO đang bận 60ms
        max_submit_block_ms = 0.0
        for f_id in range(1, 5):
            t0 = time.monotonic()
            yolo2.submit_job(PendingROIJob("CAM03", f_id, time.time(), t0, roi))
            yolo2.submit_job(PendingROIJob("CAM04", f_id, time.time(), t0, roi))
            block_ms = (time.monotonic() - t0) * 1000.0
            max_submit_block_ms = max(max_submit_block_ms, block_ms)
            time.sleep(0.015)

        # Khoảng khựng do đẩy job sang YOLO gần như bằng 0 (< 5ms dù YOLO tốn 60ms/lần)
        self.assertLess(max_submit_block_ms, 5.0)

        deadline = time.monotonic() + 1.0
        while len(dual_results) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)

        diag2 = yolo2.get_diagnostics()
        self.assertGreaterEqual(diag2["CAM03"]["inferred"], 1)
        self.assertGreaterEqual(diag2["CAM04"]["inferred"], 1)
        self.assertTrue(yolo2.stop(timeout_ms=2000))

    def test_8_gui_latest_frame_slot_and_blitting_optimization(self):
        """8. Tối ưu hiển thị Pi 4: bỏ qua frame cũ dồn toa trong Qt queue và dùng Blitting cho đồ thị Khung 1."""
        d = DashboardHarness()
        rendered_frames = []
        d._on_real_camera_frame = lambda frame, source_id="CAM03": rendered_frames.append(
            (source_id, int(frame[0, 0, 0]))
        )

        fake_worker_cam03 = Mock()
        fake_worker_cam03.latest_emitted_frame_id = 10
        fake_worker_cam03.dropped_gui_frames = 0
        d.ai_camera_workers = {"CAM03": fake_worker_cam03}

        f8 = np.full((480, 640, 3), 8, dtype=np.uint8)
        f9 = np.full((480, 640, 3), 9, dtype=np.uint8)
        f10 = np.full((480, 640, 3), 10, dtype=np.uint8)

        # Frame 8 và 9 đã cũ hơn latest_emitted_frame_id=10 -> bị bỏ qua ngay lập tức
        d._on_real_camera_frame_packet("CAM03", 8, 100.0, f8)
        d._on_real_camera_frame_packet("CAM03", 9, 100.03, f9)
        d._on_real_camera_frame_packet("CAM03", 10, 100.06, f10)

        self.assertEqual(fake_worker_cam03.dropped_gui_frames, 2)
        self.assertEqual(rendered_frames, [("CAM03", 10)])
        self.assertEqual(d._latest_frame_meta_by_camera["CAM03"]["frame_id"], 10)

        # Kiểm tra Blitting trong _tick_bandwidth: chỉ gọi canvas.draw() ở nhịp đầu tiên, các nhịp sau dùng restore_region + blit
        d.bandwidth_store = Mock()
        d.bandwidth_store.snapshot.return_value = {
            src: {
                "status": NAMESPACE["CollectorStatus"].ONLINE,
                "history": [(time.time() - 1.0, 2.5)],
            }
            for src in ("R1_WAN", "CAM03", "CAM04")
        }
        d.bandwidth_lines = {
            src: Mock() for src in ("R1_WAN", "CAM03", "CAM04")
        }
        d._bandwidth_legend_signature = None
        d.ax = Mock()
        d.ax.get_ylim.return_value = (0.0, 10.0)
        d.ax.bbox = "bbox_obj"
        d.ax.get_legend.return_value = "legend_obj"
        d.canvas = Mock()
        d.canvas.width.return_value = 500
        d.canvas.height.return_value = 280
        d.canvas.copy_from_bbox.return_value = "cached_bg"
        d._update_device_status_table = Mock()

        # Nhịp 1: khởi tạo cache nền tĩnh -> gọi canvas.draw() đúng 1 lần
        d._tick_bandwidth()
        self.assertEqual(d.canvas.draw.call_count, 1)
        self.assertEqual(d.canvas.blit.call_count, 1)

        # Nhịp 2 & 3 (cùng trạng thái, cùng ylim): KHÔNG gọi lại canvas.draw(), chỉ restore_region + blit
        d._tick_bandwidth()
        d._tick_bandwidth()
        self.assertEqual(d.canvas.draw.call_count, 1)
        self.assertEqual(d.canvas.restore_region.call_count, 3)
        self.assertEqual(d.canvas.blit.call_count, 3)


if __name__ == "__main__":
    unittest.main()
