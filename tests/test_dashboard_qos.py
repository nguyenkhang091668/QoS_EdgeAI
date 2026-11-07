"""Exercise actual dashboard slots without starting cameras, collectors or Qt GUI."""
import ast
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import time

from edge_orchestrator.network.qos_sync import QoSSyncEvent

SOURCE = (Path(__file__).resolve().parents[1] / "src/edge_orchestrator/gui/dashboard.py").read_text()
DASHBOARD = next(n for n in ast.parse(SOURCE).body if isinstance(n, ast.ClassDef) and n.name == "MainDashboard")


class Status(Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    INTERFACE_DOWN = "INTERFACE_DOWN"
    COLLECTOR_ERROR = "COLLECTOR_ERROR"
    STALE = "STALE"


class DashboardHarness:
    pass


NAMESPACE = {"CollectorStatus": Status, "time": time}
for name in ("_recalculate_target_policy", "_on_network_state_change",
             "_apply_network_policy", "_tick_qos_sync", "_on_qos_sync_result",
             "_manual_qos", "_on_ai_observation", "_on_snmp_bandwidth_samples",
             "_run_acl_remediation"):
    node = next(n for n in DASHBOARD.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), "dashboard.py", "exec"), NAMESPACE)
    setattr(DashboardHarness, name, NAMESPACE[name])


class DashboardQoSTests(unittest.TestCase):
    def setUp(self):
        self.dashboard = DashboardHarness()
        d = self.dashboard
        d._ai_state = "NORMAL"
        d._network_state = "NORMAL"
        d._desired_policy = "NORMAL"
        d._qos_closing = False
        d.simulate_netmiko = False
        d.network_controllers = {"R1": Mock(), "R2": Mock()}
        d.qos_sync = Mock()
        d.log_console = Mock()

    def test_ai_normal_requests_normal_even_when_network_is_degraded(self):
        d = self.dashboard
        d._network_state = "QOS_DEGRADED"
        d._desired_policy = "ALERT"
        d._recalculate_target_policy()
        d.qos_sync.set_desired_policy.assert_called_once_with("NORMAL")
        d.qos_sync.reconcile.assert_called_once()

    def test_ai_alert_requests_alert(self):
        d = self.dashboard
        d._ai_state = "ALERT"
        d._recalculate_target_policy()
        d.qos_sync.set_desired_policy.assert_called_once_with("ALERT")

    def test_network_transitions_never_control_qos(self):
        d = self.dashboard
        for state in ("QOS_DEGRADED", "NORMAL"):
            d._on_network_state_change(state)
        d.qos_sync.set_desired_policy.assert_not_called()
        d.qos_sync.reconcile.assert_not_called()

    def test_request_slot_never_calls_synchronous_controller_operations(self):
        d = self.dashboard
        self.assertTrue(d._apply_network_policy("ALERT"))
        for controller in d.network_controllers.values():
            self.assertEqual(controller.mock_calls, [])
        self.assertFalse(hasattr(d, "_applied_policy"))

    def test_periodic_retry_does_not_require_snmp_status_change(self):
        d = self.dashboard
        for _ in range(3):
            d._tick_qos_sync()
        self.assertEqual(d.qos_sync.reconcile.call_count, 3)

    def test_result_logs_name_and_actual_verified_result(self):
        d = self.dashboard
        d._on_qos_sync_result(QoSSyncEvent("R2", "ALERT", True, "Đã xác minh ALERT"))
        d.log_console.assert_called_with("[DYNAMIC QOS] [R2] Đã xác minh ALERT", alert=False)
        d._on_qos_sync_result(QoSSyncEvent("R1", "ALERT", False, "SSH timeout"))
        d.log_console.assert_called_with("[DYNAMIC QOS] [R1] SSH timeout", alert=True)

    def test_no_operations_or_results_after_close(self):
        d = self.dashboard
        d._qos_closing = True
        self.assertFalse(d._apply_network_policy("ALERT"))
        d._tick_qos_sync()
        d._on_qos_sync_result(QoSSyncEvent("R1", "ALERT", True, "late result"))
        d.qos_sync.reconcile.assert_not_called()
        d.log_console.assert_not_called()

    def test_repeated_ai_observations_update_data_without_logging(self):
        d = self.dashboard
        event = {"class_id": 0, "score": 0.9}
        for _ in range(30):
            d._on_ai_observation(True, event)
        d.log_console.assert_not_called()
        self.assertTrue(d._latest_person_detected)
        self.assertIs(d._latest_ai_event, event)
        self.assertGreater(d._latest_ai_observation_at, 0)

    def test_snmp_empty_batch_and_interface_down_never_launch_ssh(self):
        d = self.dashboard
        d.bandwidth_store = Mock()
        d.bandwidth_store.update.return_value = True
        d._bandwidth_status_cache = {}
        d._on_snmp_bandwidth_samples([])
        d._on_snmp_bandwidth_samples([
            SimpleNamespace(source_id="R1_DSW1", status=Status.INTERFACE_DOWN, error=None),
            SimpleNamespace(source_id="R2_WAN", status=Status.ONLINE, error=None),
        ])
        d.qos_sync.reconcile.assert_not_called()
        self.assertEqual(d._bandwidth_status_cache["R1_DSW1"], Status.INTERFACE_DOWN)

    def test_manual_qos_still_requests_policy(self):
        d = self.dashboard
        d._manual_qos("alert")
        d.qos_sync.set_desired_policy.assert_called_once_with("ALERT")

    def test_acl_preserves_explicit_r1_target(self):
        d = self.dashboard
        d._run_acl_remediation("192.168.10.99", "abcd")
        d.network_controllers["R1"].isolate_attacker.assert_called_once_with("192.168.10.99", "abcd")
        d.network_controllers["R2"].isolate_attacker.assert_not_called()

    def test_controller_initialization_failure_does_not_discard_other_router(self):
        start = SOURCE.index("        self.network_controllers = {}")
        end = SOURCE.index("        self._latest_person_detected", start)
        import textwrap
        namespace = {
            "NetworkController": Mock(side_effect=[RuntimeError("R1 init failed"), Mock()]),
            "RouterQoSSynchronizer": Mock(), "bridge": Mock(),
        }
        exec(textwrap.dedent(SOURCE[start:end]), namespace, {"self": self.dashboard})
        self.assertNotIn("R1", self.dashboard.network_controllers)
        self.assertIn("R2", self.dashboard.network_controllers)
        self.assertEqual(self.dashboard._network_controller_errors["R1"], "R1 init failed")

    def test_fast_attack_slow_release_filters_single_frame_and_transient_glimpse(self):
        from unittest.mock import patch
        from edge_orchestrator.core.decision_engine import AIDecisionEngine

        engine = AIDecisionEngine(
            alert_trigger_sec=0.8,
            recover_trigger_sec=10.0,
            min_alert_confirmations=2,
        )
        # 1) Single YOLO hallucination frame followed by delay must NOT trigger ALERT
        with patch("edge_orchestrator.core.decision_engine.time.monotonic", side_effect=[100.0, 100.0, 101.2]):
            engine = AIDecisionEngine(alert_trigger_sec=0.8, recover_trigger_sec=10.0, min_alert_confirmations=2)
            self.assertIsNone(engine.update(True))
            # Only 1 confirmation so far; if person leaves at 101.2s -> stays NORMAL
            self.assertIsNone(engine.update(False))
            self.assertEqual(engine.current_state, "NORMAL")

        # 2) Accidental transient step-in (< 0.8s) even with 2 frames must NOT trigger ALERT
        with patch("edge_orchestrator.core.decision_engine.time.monotonic", side_effect=[200.0, 200.0, 200.4, 200.7]):
            engine = AIDecisionEngine(alert_trigger_sec=0.8, recover_trigger_sec=10.0, min_alert_confirmations=2)
            self.assertIsNone(engine.update(True))   # t=200.0, count=1
            self.assertIsNone(engine.update(True))   # t=200.4, count=2, elapsed=0.4 < 0.8
            self.assertIsNone(engine.update(False))  # stepped back out at t=200.7 -> reset!
            self.assertEqual(engine.current_state, "NORMAL")

        # 3) Real intruder (>= 2 frames and >= 0.8s) triggers Fast-Attack ALERT, then holds Slow-Release 10s
        with patch(
            "edge_orchestrator.core.decision_engine.time.monotonic",
            side_effect=[300.0, 300.0, 300.45, 300.85, 301.5, 309.0, 311.6],
        ):
            engine = AIDecisionEngine(alert_trigger_sec=0.8, recover_trigger_sec=10.0, min_alert_confirmations=2)
            self.assertIsNone(engine.update(True))           # t=300.0, count=1
            self.assertIsNone(engine.update(True))           # t=300.45, count=2, elapsed=0.45
            self.assertEqual(engine.update(True), "ALERT")   # t=300.85, count=3, elapsed=0.85 -> ALERT!
            self.assertIsNone(engine.update(False))          # t=301.5, leaves frame
            self.assertIsNone(engine.update(False))          # t=309.0, elapsed=7.5s < 10.0s -> still ALERT
            self.assertEqual(engine.update(False), "NORMAL") # t=311.6, elapsed=10.1s >= 10.0s -> NORMAL!

    def test_event_driven_ai_observation_triggers_qos_and_logs_e2e_ms(self):
        from unittest.mock import patch
        from edge_orchestrator.core.decision_engine import AIDecisionEngine
        import edge_orchestrator.core.config as real_config

        extra_ns = {"config": real_config, "time": time}
        for name in ("_get_fresh_person_state", "_evaluate_live_ai_state"):
            node = next(n for n in DASHBOARD.body if isinstance(n, ast.FunctionDef) and n.name == name)
            exec(compile(ast.Module(body=[node], type_ignores=[]), "dashboard.py", "exec"), extra_ns)

        d = self.dashboard
        d.simulate_ai = False
        d._ai_available = True
        d._active_ai_cameras = {"CAM03"}
        d._ai_observations_by_camera = {}
        d._latest_person_detected = False
        d._latest_ai_observation_at = None
        d._latest_ai_event = None
        d._alert_e2e_start_mono = None
        d.ai_engine = AIDecisionEngine(alert_trigger_sec=0.8, recover_trigger_sec=10.0, min_alert_confirmations=2)
        d._get_fresh_person_state = extra_ns["_get_fresh_person_state"].__get__(d, DashboardHarness)
        d._evaluate_live_ai_state = extra_ns["_evaluate_live_ai_state"].__get__(d, DashboardHarness)

        def on_ai_state_change(state):
            d._ai_state = state
            if state == "NORMAL":
                d._alert_e2e_start_mono = None
            d._recalculate_target_policy()

        d._on_ai_state_change = on_ai_state_change

        with patch("time.monotonic", return_value=500.0):
            d._on_ai_observation(True, {"source_id": "CAM03", "frame_id": 1, "captured_at": 500.0})
        d.qos_sync.set_desired_policy.assert_not_called()

        with patch("time.monotonic", return_value=500.85):
            d._on_ai_observation(True, {"source_id": "CAM03", "frame_id": 2, "captured_at": 500.85})
        # Immediately triggered QoS ALERT without waiting for any QTimer tick!
        d.qos_sync.set_desired_policy.assert_called_once_with("ALERT")
        d.qos_sync.reconcile.assert_called_once()

        with patch("time.monotonic", return_value=501.42):
            d._on_qos_sync_result(QoSSyncEvent("R1", "ALERT", True, "Đã áp dụng và xác minh ALERT, CAMERA_CLASS=8000 kbps."))
        logged_msg = d.log_console.call_args.args[0]
        self.assertIn("Tổng phản ứng đầu-cuối: 1420 ms", logged_msg)


if __name__ == "__main__":
    unittest.main()
