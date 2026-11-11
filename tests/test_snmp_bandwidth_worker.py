import threading
import time
import unittest

from concurrent.futures import ThreadPoolExecutor
from PyQt5.QtCore import QCoreApplication, QObject, pyqtSlot

from edge_orchestrator.network.bandwidth_store import BandwidthStore
from edge_orchestrator.network.bandwidth_types import (
    BandwidthSample,
    CollectorStatus,
)
from edge_orchestrator.network.snmp_bandwidth_worker import (
    CiscoBandwidthWorker,
)


class FakeCollector:
    def __init__(self):
        self.source_ids = (
            "R1",
            "R2",
            "DSW1",
            "DSW2",
            "ASW",
        )

        self.devices = {
            source_id: {
                "host": f"host-{source_id}",
                "interface": "Gi0/2",
                "direction": "rx",
                "label": source_id,
            }
            for source_id in self.source_ids
        }

        self.failures = set()

    def poll_device(self, source_id):
        if source_id in self.failures:
            raise RuntimeError(
                f"SNMP failed for {source_id}"
            )

        return BandwidthSample(
            source_id=source_id,
            source_type="snmp",
            status=CollectorStatus.ONLINE,
            sampled_at=1000.0,
            value_mbps=1.0,
            rx_mbps=1.0,
            tx_mbps=0.5,
            details={},
        )


class ControlledCollector:
    """Collector test double cho chế độ per-source và kiểm tra cô lập host chết."""

    def __init__(self, blocked_host=None, blocked_source=None, enable_batch=False):
        self.devices = {
            "ASW_CAM": {"host": "asw", "interface": "Gi0/2", "direction": "rx"},
            "ASW_DSW1": {"host": "asw", "interface": "Gi0/0", "direction": "tx"},
            "DSW1_PO1": {"host": "dsw1", "interface": "Po1", "direction": "max"},
            "R1_WAN": {"host": "r1", "interface": "Gi0/0", "direction": "tx"},
            "R1_DSW1": {"host": "r1", "interface": "Gi0/2.99", "direction": "rx"},
            "R2_WAN": {"host": "r2", "interface": "Gi0/0", "direction": "tx"},
        }
        self.source_ids = tuple(self.devices)
        self.blocked_host = blocked_host
        self.blocked_source = blocked_source
        self.enable_batch = enable_batch
        if enable_batch:
            grouped = {}
            for sid, cfg in self.devices.items():
                grouped.setdefault(cfg["host"], []).append(sid)
            self.host_groups = {h: tuple(sids) for h, sids in grouped.items()}
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

    def poll_host_group(self, host):
        if not self.enable_batch:
            raise NotImplementedError
        group_ids = self.host_groups[host]
        with self.lock:
            self.active_hosts[host] = self.active_hosts.get(host, 0) + 1
            self.max_per_host[host] = max(
                self.max_per_host.get(host, 0), self.active_hosts[host]
            )
            for sid in group_ids:
                self.active[sid] = self.active.get(sid, 0) + 1
                self.max_per_source[sid] = max(
                    self.max_per_source.get(sid, 0), self.active[sid]
                )
            self.max_total = max(self.max_total, sum(self.active_hosts.values()))
        try:
            if host == self.blocked_host:
                self.entered.set()
                if not self.release.wait(15):
                    raise TimeoutError("test gate was not released")
            now_ts = time.time()
            out = []
            for sid in group_ids:
                with self.lock:
                    fail = sid in self.failures
                if fail:
                    raise OSError(f"controlled SNMP batch failure on {sid}")
                sample = BandwidthSample(
                    source_id=sid,
                    source_type="snmp",
                    status=CollectorStatus.ONLINE,
                    sampled_at=now_ts,
                    value_mbps=1.25,
                    rx_mbps=1.25,
                    tx_mbps=0.5,
                )
                with self.lock:
                    self.produced.append(sample)
                out.append(sample)
            return out
        finally:
            with self.lock:
                self.active_hosts[host] -= 1
                for sid in group_ids:
                    self.active[sid] -= 1

    def poll_device(self, source_id):
        host = self.devices[source_id]["host"]
        with self.lock:
            self.active[source_id] = self.active.get(source_id, 0) + 1
            self.active_hosts[host] = self.active_hosts.get(host, 0) + 1
            self.max_per_source[source_id] = max(
                self.max_per_source.get(source_id, 0), self.active[source_id]
            )
            self.max_per_host[host] = max(
                self.max_per_host.get(host, 0), self.active_hosts[host]
            )
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
                source_id=source_id,
                source_type="snmp",
                status=CollectorStatus.ONLINE,
                sampled_at=time.time(),
                value_mbps=1.25,
                rx_mbps=1.25,
                tx_mbps=0.5,
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
            self.closed_while_active = any(self.active.values()) or any(
                self.active_hosts.values()
            )
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


class CiscoBandwidthWorkerTests(unittest.TestCase):
    def setUp(self):
        self.collector = FakeCollector()

        self.worker = CiscoBandwidthWorker(
            collector=self.collector,
            interval_sec=2.0,
            max_workers=5,
        )

    def test_poll_cycle_preserves_source_order(self):
        with ThreadPoolExecutor(
            max_workers=5
        ) as executor:
            samples = self.worker._poll_cycle(
                executor
            )

        self.assertEqual(
            [sample.source_id for sample in samples],
            list(self.collector.source_ids),
        )

        self.assertTrue(
            all(
                sample.status
                == CollectorStatus.ONLINE
                for sample in samples
            )
        )

    def test_one_failure_does_not_drop_other_devices(self):
        self.collector.failures.add("R2")

        with ThreadPoolExecutor(
            max_workers=5
        ) as executor:
            samples = self.worker._poll_cycle(
                executor
            )

        self.assertEqual(len(samples), 5)

        samples_by_id = {
            sample.source_id: sample
            for sample in samples
        }

        self.assertEqual(
            samples_by_id["R2"].status,
            CollectorStatus.COLLECTOR_ERROR,
        )

        self.assertIsNone(
            samples_by_id["R2"].value_mbps
        )

        self.assertIn(
            "SNMP failed for R2",
            samples_by_id["R2"].error,
        )

        self.assertEqual(
            samples_by_id["R1"].status,
            CollectorStatus.ONLINE,
        )

        self.assertEqual(
            samples_by_id["DSW1"].status,
            CollectorStatus.ONLINE,
        )

    def test_max_workers_is_limited_by_sources(self):
        worker = CiscoBandwidthWorker(
            collector=self.collector,
            interval_sec=2.0,
            max_workers=20,
        )

        self.assertEqual(
            worker.max_workers,
            5,
        )

    def test_invalid_interval_is_rejected(self):
        with self.assertRaises(ValueError):
            CiscoBandwidthWorker(
                collector=self.collector,
                interval_sec=0,
                max_workers=5,
            )

    def test_invalid_max_workers_is_rejected(self):
        with self.assertRaises(ValueError):
            CiscoBandwidthWorker(
                collector=self.collector,
                interval_sec=2,
                max_workers=0,
            )


class SNMPFailoverFreshnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def start_worker(self, collector, max_workers=4):
        receiver = Receiver(collector.source_ids)
        worker = CiscoBandwidthWorker(
            collector, interval_sec=0.05, max_workers=max_workers
        )
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

    def test_r1_stopped_in_host_batch_mode_never_stales_healthy_hosts(self):
        collector = ControlledCollector(blocked_host="r1", enable_batch=True)
        worker, receiver = self.start_worker(collector, max_workers=4)
        self.assertTrue(collector.entered.wait(1))
        healthy = ("ASW_CAM", "ASW_DSW1", "DSW1_PO1", "R2_WAN")
        self.wait_for(lambda: all(receiver.count(key) >= 3 for key in healthy))
        deadline = time.monotonic() + 6.2
        while time.monotonic() < deadline:
            self.app.processEvents()
            for key in healthy:
                self.assertEqual(
                    receiver.store.effective_status(key),
                    CollectorStatus.ONLINE,
                )
            time.sleep(0.01)
        self.assertEqual(receiver.count("R1_WAN"), 0)
        self.assertEqual(receiver.count("R1_DSW1"), 0)
        self.assertTrue(worker.isRunning())
        self.assertLessEqual(collector.max_total, 4)
        self.assertTrue(all(count == 1 for count in collector.max_per_host.values()))

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
                self.assertEqual(
                    receiver.store.effective_status(key),
                    CollectorStatus.ONLINE,
                )
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
        self.wait_for(
            lambda: receiver.count("ASW_CAM") >= 3 and receiver.count("R1_WAN") >= 3
        )
        self.assertEqual(receiver.count("R2_WAN"), 0)
        collector.release.set()
        self.wait_for(lambda: receiver.count("R2_WAN") >= 2)
        self.assertEqual(
            receiver.store.effective_status("R2_WAN"), CollectorStatus.ONLINE
        )

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
        self.wait_for(
            lambda: receiver.count("ASW_CAM") >= 2 and receiver.count("ASW_DSW1") >= 2
        )
        self.assertEqual(
            receiver.store.latest("ASW_CAM").status,
            CollectorStatus.COLLECTOR_ERROR,
        )
        self.assertEqual(
            receiver.store.latest("ASW_DSW1").status,
            CollectorStatus.ONLINE,
        )
        with collector.lock:
            collector.failures.clear()
        self.wait_for(
            lambda: receiver.store.latest("ASW_CAM").status == CollectorStatus.ONLINE
        )

    def test_concurrency_is_bounded_with_more_hosts_than_workers(self):
        collector = ControlledCollector(blocked_host="r1")
        _, receiver = self.start_worker(collector, max_workers=2)
        self.wait_for(
            lambda: receiver.count("ASW_CAM") >= 3 and receiver.count("R2_WAN") >= 3
        )
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