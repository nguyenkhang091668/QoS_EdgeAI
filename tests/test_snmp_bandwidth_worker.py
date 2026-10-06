import unittest

from concurrent.futures import ThreadPoolExecutor

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
if __name__ == "__main__":
    unittest.main()