import threading
import unittest
from unittest.mock import patch

from edge_orchestrator.network.qos_sync import PolicySyncResult, RouterQoSSynchronizer


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds=15):
        self.now += seconds


class FakeRouter:
    def __init__(self):
        self.policy = "NORMAL"
        self.online = True
        self.calls = []
        self.gate = None
        self.entered = threading.Event()

    def ensure_qos_policy(self, policy):
        self.calls.append((policy, threading.current_thread().name))
        self.entered.set()
        if self.gate is not None and not self.gate.wait(2):
            raise TimeoutError("Test gate was not released")
        if not self.online:
            raise OSError("No route to host")
        changed = self.policy != policy
        self.policy = policy
        return PolicySyncResult(policy, changed)


class RouterQoSSynchronizerTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.routers = {name: FakeRouter() for name in ("R1", "R2")}
        self.events = []
        self.sync = RouterQoSSynchronizer(self.routers, self.events.append, clock=self.clock)

    def tearDown(self):
        for router in self.routers.values():
            if router.gate is not None:
                router.gate.set()
        self.sync.close()

    def join(self, name=None):
        names = [name] if name else list(self.routers)
        for device in names:
            worker = self.sync._states[device].thread
            if worker is not None:
                worker.join(1)
                self.assertFalse(worker.is_alive(), device)

    def run_cycle(self):
        self.sync.reconcile()
        self.join()

    def test_both_routers_receive_alert_then_normal(self):
        for policy in ("ALERT", "NORMAL"):
            self.sync.set_desired_policy(policy)
            self.run_cycle()
            for name, router in self.routers.items():
                self.assertEqual(router.policy, policy)
                self.assertEqual(self.sync.snapshot()[name]["applied_policy"], policy)
                self.assertNotEqual(router.calls[-1][1], threading.current_thread().name)

    def test_r1_down_does_not_prevent_r2_and_retries_without_ai_transition(self):
        self.routers["R1"].online = False
        self.sync.set_desired_policy("ALERT")
        self.run_cycle()
        self.assertIsNone(self.sync.snapshot()["R1"]["applied_policy"])
        self.assertEqual(self.routers["R2"].policy, "ALERT")
        self.sync.reconcile()
        self.assertEqual(len(self.routers["R1"].calls), 1)
        self.routers["R1"].online = True
        self.clock.advance()
        self.run_cycle()
        self.assertEqual(self.routers["R1"].policy, "ALERT")
        self.assertEqual(self.sync.snapshot()["R1"]["applied_policy"], "ALERT")

    def test_r2_can_change_both_ways_while_r1_is_down(self):
        self.routers["R1"].online = False
        for policy in ("ALERT", "NORMAL", "ALERT"):
            self.sync.set_desired_policy(policy)
            self.run_cycle()
            self.assertEqual(self.routers["R2"].policy, policy)
        self.assertEqual(len(self.routers["R1"].calls), 1)

    def test_stalled_r1_does_not_delay_r2(self):
        r1 = self.routers["R1"]
        r1.gate = threading.Event()
        self.sync.set_desired_policy("ALERT")
        self.sync.reconcile()
        self.assertTrue(r1.entered.wait(1))
        self.join("R2")
        self.assertEqual(self.routers["R2"].policy, "ALERT")
        self.assertIsNone(self.sync.snapshot()["R1"]["applied_policy"])
        for _ in range(5):
            self.sync.reconcile()
        self.assertEqual(len(r1.calls), 1)
        r1.gate.set()
        self.join("R1")

    def test_new_request_during_ssh_is_reconciled_without_out_of_order_jobs(self):
        r1 = self.routers["R1"]
        r1.gate = threading.Event()
        self.sync.set_desired_policy("ALERT")
        self.sync.reconcile()
        self.assertTrue(r1.entered.wait(1))
        self.join("R2")
        self.sync.set_desired_policy("NORMAL")
        self.sync.reconcile()
        self.join("R2")
        self.assertEqual(self.routers["R2"].policy, "NORMAL")
        self.assertEqual(len(r1.calls), 1)
        r1.gate.set()
        self.join("R1")
        self.run_cycle()
        self.assertEqual(r1.policy, "NORMAL")
        self.assertEqual([p for p, _ in r1.calls], ["ALERT", "NORMAL"])

    def test_periodic_read_detects_restart_even_without_snmp_offline_event(self):
        self.sync.set_desired_policy("ALERT")
        self.run_cycle()
        self.routers["R1"].policy = "NORMAL"  # Router rebooted from startup-config.
        self.clock.advance()
        self.run_cycle()
        self.assertEqual(self.routers["R1"].policy, "ALERT")

    def test_initial_normal_is_verified_instead_of_assumed(self):
        self.routers["R1"].policy = "ALERT"
        self.run_cycle()
        self.assertEqual(self.routers["R1"].policy, "NORMAL")
        self.assertEqual(len(self.events), 2)

    def test_successful_heartbeats_do_not_repeat_logs_or_write_config(self):
        self.run_cycle()
        initial_events = len(self.events)
        self.sync.reconcile()
        self.assertEqual(len(self.routers["R1"].calls), 1)
        self.clock.advance()
        self.run_cycle()
        self.assertEqual(len(self.routers["R1"].calls), 2)
        self.assertEqual(len(self.events), initial_events)

    def test_identical_failures_are_backed_off_and_not_relogged(self):
        self.routers["R1"].online = False
        self.run_cycle()
        for _ in range(3):
            self.clock.advance()
            self.run_cycle()
        failures = [e for e in self.events if not e.success]
        self.assertEqual(len(failures), 1)
        self.assertEqual(len(self.routers["R1"].calls), 4)

    def test_recovery_uses_latest_policy_not_policy_from_failed_attempt(self):
        self.routers["R1"].online = False
        self.sync.set_desired_policy("ALERT")
        self.run_cycle()
        self.sync.set_desired_policy("NORMAL")
        self.run_cycle()
        self.assertEqual(len(self.routers["R1"].calls), 1)
        self.routers["R1"].online = True
        self.clock.advance()
        self.run_cycle()
        self.assertEqual(self.routers["R1"].calls[-1][0], "NORMAL")
        self.assertEqual(self.sync.snapshot()["R1"]["applied_policy"], "NORMAL")

    def test_close_suppresses_pending_results_and_future_jobs(self):
        r1 = self.routers["R1"]
        r1.gate = threading.Event()
        self.sync.reconcile()
        self.assertTrue(r1.entered.wait(1))
        self.join("R2")
        count = len(self.events)
        self.sync.close(timeout_sec=0)
        r1.gate.set()
        self.join("R1")
        self.sync.set_desired_policy("ALERT")
        self.sync.reconcile()
        self.assertEqual(len(self.events), count)
        self.assertEqual(len(r1.calls), 1)

    def test_invalid_policy_does_not_start_ssh(self):
        with self.assertRaises(ValueError):
            self.sync.set_desired_policy("INVALID")
        self.assertFalse(self.routers["R1"].calls)

    def test_thread_start_failure_is_reported_and_can_be_retried(self):
        with patch("threading.Thread.start", side_effect=RuntimeError("thread unavailable")):
            self.sync.reconcile()
        self.assertTrue(all(s["pending_policy"] is None for s in self.sync.snapshot().values()))
        self.assertEqual(len(self.events), 2)
        self.clock.advance()
        self.run_cycle()
        self.assertTrue(all(s["applied_policy"] == "NORMAL" for s in self.sync.snapshot().values()))


if __name__ == "__main__":
    unittest.main()
