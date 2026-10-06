import math
import unittest

from edge_orchestrator.network.bandwidth_store import (
    BandwidthStore,
)
from edge_orchestrator.network.bandwidth_types import (
    BandwidthSample,
    CollectorStatus,
)


class FakeClock:
    def __init__(self, initial=100.0):
        self.value = float(initial)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += float(seconds)


class BandwidthStoreTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()

        self.store = BandwidthStore(
            source_ids=["R1", "CAM01"],
            history_sec=60,
            stale_after_sec=6,
            clock=self.clock,
        )

    def sample(
        self,
        source_id,
        value,
        status=CollectorStatus.ONLINE,
    ):
        return BandwidthSample(
            source_id=source_id,
            source_type="snmp",
            status=status,
            sampled_at=self.clock(),
            value_mbps=value,
        )

    def test_zero_is_preserved(self):
        self.store.update(
            self.sample("R1", 0.0)
        )

        history = self.store.history("R1")

        self.assertEqual(history[-1][1], 0.0)

    def test_missing_value_becomes_nan(self):
        sample = BandwidthSample.unavailable(
            source_id="R1",
            source_type="snmp",
            status=CollectorStatus.OFFLINE,
        )

        # Dùng timestamp của fake clock để test ổn định.
        sample = BandwidthSample(
            source_id=sample.source_id,
            source_type=sample.source_type,
            status=sample.status,
            sampled_at=self.clock(),
            value_mbps=None,
        )

        self.store.update(sample)

        value = self.store.history("R1")[-1][1]

        self.assertTrue(math.isnan(value))

    def test_duplicate_timestamp_is_ignored(self):
        first = self.sample("R1", 1.0)
        duplicate = self.sample("R1", 2.0)

        self.assertTrue(self.store.update(first))
        self.assertFalse(self.store.update(duplicate))

        self.assertEqual(
            len(self.store.history("R1")),
            1,
        )

    def test_source_becomes_stale(self):
        self.store.update(
            self.sample("R1", 1.0)
        )

        self.clock.advance(7)

        self.assertEqual(
            self.store.effective_status("R1"),
            CollectorStatus.STALE,
        )

    def test_empty_source_starts_warming_up(self):
        self.assertEqual(
            self.store.effective_status("CAM01"),
            CollectorStatus.WARMING_UP,
        )

    def test_empty_source_becomes_stale(self):
        self.clock.advance(7)

        self.assertEqual(
            self.store.effective_status("CAM01"),
            CollectorStatus.STALE,
        )

    def test_old_history_is_removed(self):
        self.store.update(
            self.sample("R1", 1.0)
        )

        self.clock.advance(61)

        self.store.update(
            self.sample("R1", 2.0)
        )

        history = self.store.history("R1")

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0][1], 2.0)

    def test_unknown_source_is_rejected(self):
        with self.assertRaises(KeyError):
            self.store.update(
                self.sample("UNKNOWN", 1.0)
            )


if __name__ == "__main__":
    unittest.main()