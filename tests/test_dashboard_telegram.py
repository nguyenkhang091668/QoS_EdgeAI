"""Exercise actual state transition slots without cameras or network."""
import ast
from pathlib import Path
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock

from edge_orchestrator.core import config
from edge_orchestrator.network.telegram_alerts import CameraAlert

SOURCE = Path(__file__).resolve().parents[1] / "src/edge_orchestrator/gui/dashboard.py"
DASHBOARD = next(n for n in ast.parse(SOURCE.read_text()).body
                 if isinstance(n, ast.ClassDef) and n.name == "MainDashboard")


class Dashboard:
    pass


namespace = {"time": time, "config": config, "CameraAlert": CameraAlert}
for name in ("_on_ai_state_change", "_queue_telegram_alert"):
    node = next(n for n in DASHBOARD.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), namespace)
    setattr(Dashboard, name, namespace[name])


class DashboardTelegramTests(unittest.TestCase):
    def dashboard(self):
        d = Dashboard()
        d._ai_state = "NORMAL"
        d._detect_count = 0
        d.simulate_ai = False
        d.telegram_alerts = SimpleNamespace(enabled=True, submit=Mock())
        d._recalculate_target_policy = Mock()
        d.log_console = Mock()
        d.lbl_detect_count = Mock()
        d.lbl_status = Mock()
        d.lbl_mode_badge = Mock()
        d._ai_observations_by_camera = {
            "CAM03": {"source_id": "CAM03", "person_detected": True,
                      "observed_at": time.monotonic(), "event": {
                          "persons": [{}, {}], "alert_photo": b"cam03-photo"}}
        }
        return d

    def test_one_photo_per_normal_to_alert_transition(self):
        d = self.dashboard()
        for state in ("ALERT", "ALERT", "NORMAL", "NORMAL", "ALERT"):
            d._on_ai_state_change(state)
        self.assertEqual(d.telegram_alerts.submit.call_count, 2)
        alert = d.telegram_alerts.submit.call_args.args[0]
        self.assertEqual(alert.camera, "CAM03")
        self.assertEqual(alert.location, "Kho tài liệu bảo mật tầng 8")
        self.assertEqual(alert.people, 2)
        self.assertEqual(alert.photo, b"cam03-photo")
        self.assertEqual(alert.qos_mbps, 8)

    def test_simulation_never_queues_telegram(self):
        d = self.dashboard()
        d.simulate_ai = True
        d._on_ai_state_change("ALERT")
        d.telegram_alerts.submit.assert_not_called()

    def test_expired_and_inactive_camera_cannot_supply_alert_photo(self):
        d = self.dashboard()
        d._active_ai_cameras = {"CAM03"}
        d._ai_observations_by_camera["CAM03"]["observed_at"] -= 100
        d._ai_observations_by_camera["CAM04"] = {
            "source_id": "CAM04", "person_detected": True,
            "observed_at": time.monotonic(), "event": {"persons": [{}], "alert_photo": b"wrong-photo"}}
        d._on_ai_state_change("ALERT")
        d.telegram_alerts.submit.assert_not_called()
        d.log_console.assert_called_once()
