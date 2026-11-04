"""Regression tests for the 3 critical Pi 4 / ACL / Khung 3 high-load fixes."""

import ast
from collections import deque
from html import escape
from pathlib import Path
import queue
import time
import unittest
from unittest.mock import Mock

from PyQt5.QtGui import QTextCursor, QTextDocument

from edge_orchestrator.core.event import EventType
from edge_orchestrator.gui.audit_log import AuditLogStore


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_SRC = (ROOT / "src/edge_orchestrator/gui/dashboard.py").read_text()
LAYER1_SRC = (ROOT / "src/edge_orchestrator/Edge/layer1_motion.py").read_text()
DASHBOARD_AST = next(
    node
    for node in ast.parse(DASHBOARD_SRC).body
    if isinstance(node, ast.ClassDef) and node.name == "MainDashboard"
)


class FakeScrollBar:
    def __init__(self):
        self._max = 100
        self._val = 100

    def maximum(self):
        return self._max

    def value(self):
        return self._val

    def setValue(self, val):
        self._val = val


class FakeConsole:
    """Headless QTextDocument-backed stand-in for QTextEdit."""

    def __init__(self):
        self._doc = QTextDocument()
        self._doc.setMaximumBlockCount(1000)
        self._scrollbar = FakeScrollBar()
        self.set_html_calls = 0
        self.append_calls = 0

    def document(self):
        return self._doc

    def verticalScrollBar(self):
        return self._scrollbar

    def clear(self):
        self._doc.clear()

    def append(self, html: str):
        self.append_calls += 1
        cur = QTextCursor(self._doc)
        cur.movePosition(QTextCursor.End)
        if not self._doc.isEmpty():
            cur.insertBlock()
        cur.insertHtml(html)

    def setHtml(self, html: str):
        self.set_html_calls += 1
        self._doc.setHtml(html)


class ConsoleHarness:
    pass


SYSLOG_QUEUE = queue.Queue()
HARNESS_NS = {
    "deque": deque,
    "escape": escape,
    "time": time,
    "queue": queue,
    "QTextCursor": QTextCursor,
    "EventType": EventType,
    "syslog_event_queue": SYSLOG_QUEUE,
}

for attr_node in DASHBOARD_AST.body:
    if isinstance(attr_node, ast.Assign):
        for target in attr_node.targets:
            if isinstance(target, ast.Name) and target.id in ("SEVERITY_COLORS", "PREFIX_CATEGORY_MAP"):
                exec(compile(ast.Module(body=[attr_node], type_ignores=[]), "dashboard.py", "exec"), HARNESS_NS)
                setattr(ConsoleHarness, target.id, HARNESS_NS[target.id])
    elif isinstance(attr_node, ast.FunctionDef) and attr_node.name in (
        "_normalize_log_input",
        "log_console",
        "_format_entry_html",
        "_entry_visible_in_current_filter",
        "_update_single_log_block",
        "_flush_deferred_coalesced_entries",
        "_render_or_update_log_entry",
        "_rebuild_console_view",
        "_run_acl_remediation",
        "_tick_syslog",
    ):
        exec(compile(ast.Module(body=[attr_node], type_ignores=[]), "dashboard.py", "exec"), HARNESS_NS)
        setattr(ConsoleHarness, attr_node.name, HARNESS_NS[attr_node.name])


class CriticalFixesTests(unittest.TestCase):
    def setUp(self):
        while not SYSLOG_QUEUE.empty():
            SYSLOG_QUEUE.get_nowait()
        self.h = ConsoleHarness()
        self.h.audit_log_store = AuditLogStore(limit=1000, coalesce_window_sec=65.0)
        self.h._active_log_category = "Tất cả"
        self.h._rendered_entries = deque(maxlen=1000)
        self.h._defer_coalesce_repaint = False
        self.h._dirty_coalesced_entries = []
        self.h._acl_cooldown_sec = 60.0
        self.h._acl_last_remediation_by_ip = {}
        self.h._device_last_syslog = {}
        self.h.simulate_netmiko = False
        self.h.network_controllers = {"R1": Mock()}
        self.h.txt_console = FakeConsole()

    def test_1_layer1_motion_has_no_stray_networkx_import(self):
        tree = ast.parse(LAYER1_SRC)
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module.split(".")[0])
        self.assertNotIn("networkx", imported_modules)

    def test_2_audit_log_coalesces_even_when_newer_unrelated_entry_is_older_in_last_seen(self):
        store = AuditLogStore(limit=1000, coalesce_window_sec=65.0)
        # Event A at t=0, Event B at t=10, Event A repeats at t=30 (last_seen=30)
        store.append("CDP mismatch", category="Cisco", dedup_key="CDP:1", now_mono=0.0)
        store.append("OSPF neighbor up", category="Cisco", dedup_key="OSPF:1", now_mono=10.0)
        store.append("CDP mismatch", category="Cisco", dedup_key="CDP:1", now_mono=30.0)
        # At t=80: Event B has age 70s (>65s), while Event A has age 50s (<=65s)
        entry_a = store.append("CDP mismatch", category="Cisco", dedup_key="CDP:1", now_mono=80.0)
        self.assertTrue(entry_a.coalesced)
        self.assertEqual(entry_a.count, 3)
        self.assertEqual(len(store), 2)

    def test_3_acl_cooldown_dynamic_countdown_coalesces_into_single_row(self):
        for _ in range(25):
            self.h._run_acl_remediation("192.168.10.55", "0242.77a5.3000")

        # SSH isolate_attacker only triggered once
        self.h.network_controllers["R1"].isolate_attacker.assert_called_once_with(
            "192.168.10.55", "0242.77a5.3000"
        )
        entries = self.h.audit_log_store.select("Security/ACL")
        # Exactly 2 rows total: 1 action row + 1 coalesced cooldown row (×24)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[1].count, 24)
        self.assertIn("(lặp lại ×24)", entries[1].display_message)

    def test_4_syslog_dai_flood_updates_single_block_in_place_without_setHtml_rebuild(self):
        # Seed 200 existing entries in console
        for i in range(200):
            self.h.log_console(f"[SYSTEM] Khởi tạo thành phần #{i}")
        self.assertEqual(self.h.txt_console.set_html_calls, 0)
        self.assertEqual(self.h.txt_console.document().blockCount(), 200)

        # Simulate a burst of 40 identical DAI ARP spoofing syslog events in one _tick_syslog
        for _ in range(40):
            SYSLOG_QUEUE.put_nowait({
                "type": EventType.DAI_ARP_SPOOFING_DENY,
                "device_name": "ASW",
                "raw": "%SW_DAI-4-DHCP_SNOOPING_DENY",
                "details": "DAI deny",
                "data": {
                    "src_ip": "192.168.10.55",
                    "src_mac": "0242.77a5.3000",
                    "interface": "Gi0/2",
                    "vlan": "10",
                },
            })

        self.h._tick_syslog()

        # Zero O(N) full setHtml rebuilds occurred!
        self.assertEqual(self.h.txt_console.set_html_calls, 0)
        # Only 3 new blocks added (DAI alert, ACL push action, ACL cooldown) -> 203 blocks total
        doc = self.h.txt_console.document()
        self.assertEqual(doc.blockCount(), 203)
        self.assertIn("(lặp lại ×40)", doc.findBlockByNumber(200).text())
        self.assertIn("(lặp lại ×39)", doc.findBlockByNumber(202).text())


if __name__ == "__main__":
    unittest.main()
