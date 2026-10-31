"""Exercise the running Qt worker, not just a completed polling batch."""
import threading
import time
import unittest

from PyQt5.QtCore import QCoreApplication, QObject, pyqtSlot

from edge_orchestrator.network.bandwidth_store import BandwidthStore
from edge_orchestrator.network.bandwidth_types import BandwidthSample, CollectorStatus
from edge_orchestrator.network.snmp_bandwidth_worker import CiscoBandwidthWorker


class ControlledCollector:
    def __init__(self, blocked_host=None, blocked_source=None):
        self.devices = {
            "ASW_CAM": dict(host="asw", interface="Gi0/2", direction="rx"),
            "ASW_DSW1": dict(host="asw", interface="Gi0/0", direction="tx"),
            "DSW1_PO1": dict(host="dsw1", interface="Po1", direction="max"),
            "R1_WAN": dict(host="r1", interface="Gi0/0", direction="tx"),
            "R1_DSW1": dict(host="r1", interface="Gi0/2.99", direction="rx"),
            "R2_WAN": dict(host="r2", interface="Gi0/0", direction="tx"),
        }
        self.source_ids = tuple(self.devices)
        self.blocked_host = blocked_host
        self.blocked_source = blocked_source
        self.entered = threading.Event()
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.failures = set()
        self.active = {}
        self.active_hosts = {}
        self.max_per_source = {}
        self.max_per_host = {}
        self.max_total = 0
        self.produced = []
        self.closed = False
        self.closed_while_active = False

    def poll_device(self, source_id):
        host = self.devices[source_id]["host"]
        with self.lock:
            self.active[source_id] = self.active.get(source_id, 0) + 1
            self.active_hosts[host] = self.active_hosts.get(host, 0) + 1
            self.max_per_source[source_id] = max(
                self.max_per_source.get(source_id, 0), self.active[source_id])
            self.max_per_host[host] = max(
                self.max_per_host.get(host, 0), self.active_hosts[host])
            self.max_total = max(self.max_total, sum(self.active.values()))
        try:
            if host == self.blocked_host or source_id == self.blocked_source:
                self.entered.set()
                if not self.release.wait(15):
                    raise TimeoutError("test gate was not released")
            with self.lock:
                fail = source_id in self.failures
            if fail:
                raise OSError("controlled SNMP read failure")
            sample = BandwidthSample(
                source_id=source_id, source_type="snmp", status=CollectorStatus.ONLINE,
                sampled_at=time.time(), value_mbps=1.25, rx_mbps=1.25, tx_mbps=0.5,
            )
            with self.lock:
                self.produced.append(sample)
            return sample
        finally:
            with self.lock:
                self.active[source_id] -= 1
                self.active_hosts[host] -= 1

    def close(self):
        with self.lock:
            self.closed_while_active = any(self.active.values())
            self.closed = True


class Receiver(QObject):
    def __init__(self, source_ids):
        super().__init__()
        self.store = BandwidthStore(source_ids, stale_after_sec=6)
        self.samples = []

    @pyqtSlot(list)
    def receive(self, samples):
        self.samples.extend(samples)
        self.store.update_many(samples)

    def count(self, source_id):
        return sum(sample.source_id == source_id for sample in self.samples)


class SNMPFailoverFreshnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def start_worker(self, collector, max_workers=4):
        receiver = Receiver(collector.source_ids)
        worker = CiscoBandwidthWorker(collector, interval_sec=0.05, max_workers=max_workers)
        worker.samples_signal.connect(receiver.receive)
        worker.start()
        self.addCleanup(self.stop_worker, worker, collector)
        return worker, receiver

    def stop_worker(self, worker, collector):
        worker.stop()
        collector.release.set()
        self.assertTrue(worker.wait(3000), "SNMP worker did not stop")
        self.app.processEvents()
        self.assertTrue(collector.closed)
        self.assertFalse(collector.closed_while_active)

    def wait_for(self, condition, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return
            time.sleep(0.005)
        self.app.processEvents()
        self.assertTrue(condition(), "condition did not arrive before deadline")

    def test_r1_pending_beyond_real_stale_threshold_does_not_stale_healthy_hosts(self):
        collector = ControlledCollector(blocked_host="r1")
        worker, receiver = self.start_worker(collector)
        self.assertTrue(collector.entered.wait(1))
        healthy = ("ASW_CAM", "ASW_DSW1", "DSW1_PO1", "R2_WAN")
        self.wait_for(lambda: all(receiver.count(key) >= 3 for key in healthy))
        deadline = time.monotonic() + 6.2
        while time.monotonic() < deadline:
            self.app.processEvents()
            for key in healthy:
                self.assertEqual(receiver.store.effective_status(key), CollectorStatus.ONLINE)
            time.sleep(0.01)
        self.assertEqual(receiver.count("R1_WAN"), 0)
        self.assertEqual(receiver.count("R1_DSW1"), 0)
        self.assertTrue(worker.isRunning())
        self.assertLessEqual(collector.max_total, 4)
        self.assertTrue(all(count == 1 for count in collector.max_per_source.values()))
        self.assertTrue(all(count == 1 for count in collector.max_per_host.values()))
        with collector.lock:
            produced_ids = {id(sample) for sample in collector.produced}
        self.assertTrue(all(id(sample) in produced_ids for sample in receiver.samples))

    def test_r2_pending_does_not_block_r1_or_asw_and_recovers_without_restart(self):
        collector = ControlledCollector(blocked_host="r2")
        _, receiver = self.start_worker(collector)
        self.assertTrue(collector.entered.wait(1))
        self.wait_for(lambda: receiver.count("ASW_CAM") >= 3 and receiver.count("R1_WAN") >= 3)
        self.assertEqual(receiver.count("R2_WAN"), 0)
        collector.release.set()
        self.wait_for(lambda: receiver.count("R2_WAN") >= 2)
        self.assertEqual(receiver.store.effective_status("R2_WAN"), CollectorStatus.ONLINE)

    def test_ready_sample_is_emitted_before_later_interface_finishes(self):
        collector = ControlledCollector(blocked_source="ASW_DSW1")
        _, receiver = self.start_worker(collector)
        self.assertTrue(collector.entered.wait(1))
        self.wait_for(lambda: receiver.count("ASW_CAM") >= 1)
        self.assertEqual(receiver.count("ASW_DSW1"), 0)
        self.wait_for(lambda: receiver.count("R2_WAN") >= 3)

    def test_failure_does_not_drop_other_interfaces_and_can_recover(self):
        collector = ControlledCollector()
        collector.failures.add("ASW_CAM")
        _, receiver = self.start_worker(collector)
        self.wait_for(lambda: receiver.count("ASW_CAM") >= 2 and receiver.count("ASW_DSW1") >= 2)
        self.assertEqual(receiver.store.latest("ASW_CAM").status, CollectorStatus.COLLECTOR_ERROR)
        self.assertEqual(receiver.store.latest("ASW_DSW1").status, CollectorStatus.ONLINE)
        with collector.lock:
            collector.failures.clear()
        self.wait_for(lambda: receiver.store.latest("ASW_CAM").status == CollectorStatus.ONLINE)

    def test_concurrency_is_bounded_with_more_hosts_than_workers(self):
        collector = ControlledCollector(blocked_host="r1")
        _, receiver = self.start_worker(collector, max_workers=2)
        self.wait_for(lambda: receiver.count("ASW_CAM") >= 3 and receiver.count("R2_WAN") >= 3)
        self.assertLessEqual(collector.max_total, 2)
        self.assertTrue(all(count == 1 for count in collector.max_per_source.values()))

    def test_stop_suppresses_late_samples_and_waits_before_cleanup(self):
        collector = ControlledCollector(blocked_host="r1")
        worker, receiver = self.start_worker(collector)
        self.assertTrue(collector.entered.wait(1))
        self.wait_for(lambda: receiver.count("ASW_CAM") >= 2)
        worker.stop()
        self.assertFalse(collector.closed, "collector closed during an in-flight read")
        collector.release.set()
        self.assertTrue(worker.wait(3000))
        self.app.processEvents()
        self.assertEqual(receiver.count("R1_WAN"), 0)
        self.assertEqual(receiver.count("R1_DSW1"), 0)
        self.assertFalse(collector.closed_while_active)


if __name__ == "__main__":
    unittest.main()
