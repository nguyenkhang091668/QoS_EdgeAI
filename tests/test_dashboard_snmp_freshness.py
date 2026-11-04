"""Run real dashboard slots against a real store without cameras or a QWidget."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import time

import numpy as np

from edge_orchestrator.network.bandwidth_store import BandwidthStore
from edge_orchestrator.network.bandwidth_types import BandwidthSample, CollectorStatus


SOURCE = (Path(__file__).resolve().parents[1] / "src/edge_orchestrator/gui/dashboard.py").read_text()
CLASS = next(n for n in ast.parse(SOURCE).body if isinstance(n, ast.ClassDef) and n.name == "MainDashboard")
CONFIG = SimpleNamespace(SNMP_DEVICES={"ASW_CAM": {}}, RTP_STREAMS={})
NAMESPACE = dict(time=time, np=np, config=CONFIG, CollectorStatus=CollectorStatus,
                 BANDWIDTH_SOURCE_IDS=("ASW_CAM",), QColor=lambda value: value)


class DashboardHarness:
    pass


for name in ("_on_snmp_bandwidth_samples", "_audit_snmp_snapshot", "_tick_bandwidth",
             "_update_device_status_table", "closeEvent"):
    node = next(n for n in CLASS.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), "dashboard.py", "exec"), NAMESPACE)
    setattr(DashboardHarness, name, NAMESPACE[name])


class Cell:
    def __init__(self, text):
        self.value = text

    def text(self):
        return self.value

    def setText(self, value):
        self.value = value

    def setForeground(self, color):
        self.color = color


class DashboardSNMPFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.clock = 1000.0
        self.dashboard = d = DashboardHarness()
        d.bandwidth_store = BandwidthStore(("ASW_CAM",), stale_after_sec=6,
                                           clock=lambda: self.clock)
        d._bandwidth_status_cache = {}
        d.log_console = Mock()
        d._status_cells = {"ASW_CAM": [Cell("ASW_CAM"), Cell("Gi0/2"),
                                      Cell("WARMING_UP"), Cell("—")]}
        d.bandwidth_lines = {"ASW_CAM": Mock()}
        d._bandwidth_legend_signature = None
        d.ax = Mock()
        d.ax.get_ylim.return_value = (0.0, 10.0)
        d.canvas = Mock()
        d.canvas.width.return_value = 500
        d.canvas.height.return_value = 280
        d._audit_snmp_snapshot = Mock(wraps=d._audit_snmp_snapshot)
        d._update_device_status_table = Mock(wraps=d._update_device_status_table)

    def sample(self, status=CollectorStatus.ONLINE, **kwargs):
        return BandwidthSample(
            source_id="ASW_CAM", source_type="snmp", sampled_at=self.clock,
            status=status, value_mbps=1.25 if status == CollectorStatus.ONLINE else None,
            **kwargs,
        )

    def tick(self):
        with patch.object(time, "time", return_value=self.clock):
            self.dashboard._tick_bandwidth()
        d = self.dashboard
        self.assertIs(d._audit_snmp_snapshot.call_args.args[0],
                      d._update_device_status_table.call_args.args[0])

    def test_online_stale_online_is_shared_by_audit_table_and_legend(self):
        d = self.dashboard
        original = self.sample()
        d._on_snmp_bandwidth_samples([original])
        d.log_console.assert_not_called()
        self.tick()
        self.assertEqual(d._status_cells["ASW_CAM"][2].text(), "ONLINE")
        self.clock += 7
        self.tick()
        self.assertEqual(d._status_cells["ASW_CAM"][2].text(), "STALE")
        self.assertIn("ONLINE -> STALE", d.log_console.call_args.args[0])
        self.assertIn("7.0s", d.log_console.call_args.args[0])
        self.assertEqual(d._bandwidth_legend_signature, (("ASW_CAM", "STALE"),))
        self.assertIs(d.bandwidth_store.latest("ASW_CAM"), original)
        count = d.log_console.call_count
        self.clock += 1
        self.tick()
        self.assertEqual(d.log_console.call_count, count)
        d._on_snmp_bandwidth_samples([self.sample()])
        self.tick()
        self.assertEqual(d._status_cells["ASW_CAM"][2].text(), "ONLINE")
        self.assertIn("STALE -> ONLINE", d.log_console.call_args.args[0])
        self.assertFalse(d.log_console.call_args.kwargs["alert"])

    def test_old_duplicate_or_error_sample_cannot_rewrite_current_audit(self):
        d = self.dashboard
        d._on_snmp_bandwidth_samples([self.sample()])
        self.tick()
        count = d.log_console.call_count
        self.clock -= 1
        d._on_snmp_bandwidth_samples([self.sample(CollectorStatus.COLLECTOR_ERROR,
                                                error="older error")])
        self.clock += 2
        self.tick()
        self.assertEqual(d.log_console.call_count, count)
        self.assertEqual(d._status_cells["ASW_CAM"][2].text(), "ONLINE")

    def test_true_timeout_is_logged_then_becomes_stale_with_last_error(self):
        d = self.dashboard
        d._on_snmp_bandwidth_samples([self.sample(CollectorStatus.OFFLINE,
                                                error="SNMP timeout from ASW")])
        self.tick()
        self.assertEqual(d._status_cells["ASW_CAM"][2].text(), "OFFLINE")
        self.assertIn("SNMP timeout", d.log_console.call_args.args[0])
        self.clock += 7
        self.tick()
        self.assertEqual(d._status_cells["ASW_CAM"][2].text(), "STALE")
        self.assertIn("trạng thái mẫu=OFFLINE", d.log_console.call_args.args[0])
        self.assertIn("SNMP timeout", d.log_console.call_args.args[0])

    def test_interface_down_has_admin_oper_evidence_and_never_becomes_online(self):
        d = self.dashboard
        d._on_snmp_bandwidth_samples([self.sample(CollectorStatus.INTERFACE_DOWN,
                                                details={"admin_status": 2, "oper_status": 2})])
        self.tick()
        self.assertEqual(d._status_cells["ASW_CAM"][2].text(), "INTERFACE_DOWN")
        self.assertIn("admin=2, oper=2", d.log_console.call_args.args[0])

    def test_missing_source_and_non_snmp_source_do_not_flood_audit(self):
        d = self.dashboard
        d._audit_snmp_snapshot({"CAM01": {"status": CollectorStatus.STREAMING}}, self.clock)
        d.log_console.assert_not_called()
        self.tick()
        self.assertEqual(d._status_cells["ASW_CAM"][2].text(), "WARMING_UP")
        self.clock += 7
        self.tick()
        self.assertIn("chưa có mẫu SNMP", d.log_console.call_args.args[0])

    def test_plot_failure_cannot_prevent_status_and_audit_update(self):
        d = self.dashboard
        d._on_snmp_bandwidth_samples([self.sample()])
        d.ax.get_ylim.side_effect = RuntimeError("plot failure")
        with self.assertRaisesRegex(RuntimeError, "plot failure"):
            self.tick()
        self.assertEqual(d._status_cells["ASW_CAM"][2].text(), "ONLINE")
        self.assertIn("ONLINE", d.log_console.call_args.args[0])

    def test_store_error_is_logged_and_does_not_discard_following_sample(self):
        d = self.dashboard
        unknown = BandwidthSample(source_id="UNKNOWN", source_type="snmp",
                                  sampled_at=self.clock, status=CollectorStatus.ONLINE)
        d._on_snmp_bandwidth_samples([unknown, self.sample()])
        self.assertIn("Lỗi cập nhật mẫu", d.log_console.call_args.args[0])
        self.assertEqual(d.bandwidth_store.latest("ASW_CAM").status, CollectorStatus.ONLINE)

    def test_close_does_not_destroy_snmp_worker_with_in_flight_read(self):
        d = self.dashboard
        d.telegram_alerts = Mock()
        d.qos_sync = Mock()
        d._stop_ai_workers = Mock()
        d.snmp_bandwidth_worker = Mock()
        d.snmp_bandwidth_worker.wait.return_value = False
        d.pi_camera_bandwidth_worker = None
        event = Mock()
        d.closeEvent(event)
        event.ignore.assert_called_once()
        event.accept.assert_not_called()
        d.snmp_bandwidth_worker.terminate.assert_not_called()
        d.snmp_bandwidth_worker.wait.return_value = True
        d.closeEvent(event)
        event.accept.assert_called_once()


if __name__ == "__main__":
    unittest.main()
