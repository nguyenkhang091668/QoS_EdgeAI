import unittest

from edge_orchestrator.network.bandwidth_types import (
    CollectorStatus,
)
from edge_orchestrator.network.snmp_bandwidth import (
    CiscoBandwidthCollector,
    InterfaceCounters,
    SnmpTimeoutError,
)


class FakeClock:
    def __init__(self, initial=100.0):
        self.value = float(initial)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += float(seconds)


class FakeSnmpClient:
    def __init__(self):
        self.if_index = 10

        self.reading = InterfaceCounters(
            admin_status=1,
            oper_status=1,
            in_octets=1000,
            out_octets=2000,
        )

        self.timeout = False

    def resolve_if_index(
        self,
        host,
        interface_name,
    ):
        if self.timeout:
            raise SnmpTimeoutError(
                f"Timeout resolving {host}"
            )

        return self.if_index

    def read_interface(
        self,
        host,
        if_index,
    ):
        if self.timeout:
            raise SnmpTimeoutError(
                f"Timeout reading {host}"
            )

        return self.reading


class CiscoBandwidthCollectorTests(
    unittest.TestCase
):
    def setUp(self):
        self.monotonic_clock = FakeClock(100)
        self.wall_clock = FakeClock(1000)
        self.client = FakeSnmpClient()

        self.devices = {
            "R1": {
                "host": "10.255.255.1",
                "interface":
                    "GigabitEthernet0/2.10",
                "direction": "rx",
                "label": "R1 VLAN10 RX",
            },
        }

        self.collector = (
            CiscoBandwidthCollector(
                devices=self.devices,
                snmp_client=self.client,
                offline_failures=3,
                monotonic_clock=self.monotonic_clock,
                wall_clock=self.wall_clock,
            )
        )

    def poll(self):
        return self.collector.poll_device("R1")

    def advance(self, seconds=2):
        self.monotonic_clock.advance(seconds)
        self.wall_clock.advance(seconds)

    def test_first_poll_is_warming_up(self):
        sample = self.poll()

        self.assertEqual(
            sample.status,
            CollectorStatus.WARMING_UP,
        )
        self.assertIsNone(sample.value_mbps)

    def test_second_poll_calculates_rx(self):
        self.poll()
        self.advance(2)

        self.client.reading = InterfaceCounters(
            admin_status=1,
            oper_status=1,
            in_octets=2000,
            out_octets=3000,
        )

        sample = self.poll()

        # 1000 bytes × 8 / 2 seconds / 1e6
        self.assertAlmostEqual(
            sample.rx_mbps,
            0.004,
        )
        self.assertAlmostEqual(
            sample.value_mbps,
            0.004,
        )
        self.assertEqual(
            sample.status,
            CollectorStatus.ONLINE,
        )

    def test_zero_delta_is_online_zero(self):
        self.poll()
        self.advance(2)

        sample = self.poll()

        self.assertEqual(
            sample.status,
            CollectorStatus.ONLINE,
        )
        self.assertEqual(
            sample.value_mbps,
            0.0,
        )

    def test_counter_reset_returns_warming_up(
        self,
    ):
        self.poll()
        self.advance(2)

        self.client.reading = InterfaceCounters(
            admin_status=1,
            oper_status=1,
            in_octets=100,
            out_octets=200,
        )

        sample = self.poll()

        self.assertEqual(
            sample.status,
            CollectorStatus.WARMING_UP,
        )
        self.assertIsNone(sample.value_mbps)

    def test_interface_down(self):
        self.client.reading = InterfaceCounters(
            admin_status=1,
            oper_status=2,
            in_octets=1000,
            out_octets=2000,
        )

        sample = self.poll()

        self.assertEqual(
            sample.status,
            CollectorStatus.INTERFACE_DOWN,
        )
        self.assertIsNone(sample.value_mbps)

    def test_three_timeouts_returns_offline(self):
        self.client.timeout = True

        first = self.poll()
        second = self.poll()
        third = self.poll()

        self.assertEqual(
            first.status,
            CollectorStatus.COLLECTOR_ERROR,
        )
        self.assertEqual(
            second.status,
            CollectorStatus.COLLECTOR_ERROR,
        )
        self.assertEqual(
            third.status,
            CollectorStatus.OFFLINE,
        )

    def test_recovery_requires_new_baseline(self):
        self.poll()
        self.client.timeout = True

        self.poll()
        self.poll()
        offline = self.poll()

        self.assertEqual(
            offline.status,
            CollectorStatus.OFFLINE,
        )

        self.client.timeout = False
        self.advance(2)

        recovered = self.poll()

        self.assertEqual(
            recovered.status,
            CollectorStatus.WARMING_UP,
        )

    def test_unknown_source_is_rejected(self):
        with self.assertRaises(KeyError):
            self.collector.poll_device(
                "UNKNOWN"
            )
    def test_tx_direction_selects_tx_value(self):
        self.collector.devices["R1"]["direction"] = "tx"

        self.poll()
        self.advance(2)

        self.client.reading = InterfaceCounters(
            admin_status=1,
            oper_status=1,
            in_octets=2000,
            out_octets=4000,
        )

        sample = self.poll()

        # RX delta: 1000 bytes -> 0.004 Mbps
        self.assertAlmostEqual(
            sample.rx_mbps,
            0.004,
        )

        # TX delta: 2000 bytes -> 0.008 Mbps
        self.assertAlmostEqual(
            sample.tx_mbps,
            0.008,
        )

        # direction=tx nên giá trị hiển thị lấy TX
        self.assertAlmostEqual(
            sample.value_mbps,
            0.008,
        )

        self.assertEqual(
            sample.status,
            CollectorStatus.ONLINE,
        )
    def test_devices_have_independent_baselines(self):
        devices = {
            "R1": {
                "host": "10.255.255.1",
                "interface":
                    "GigabitEthernet0/2.10",
                "direction": "rx",
                "label": "R1 VLAN10 RX",
            },
            "R2": {
                "host": "10.255.255.2",
                "interface":
                    "GigabitEthernet0/2.10",
                "direction": "rx",
                "label": "R2 VLAN10 RX",
            },
        }

        collector = CiscoBandwidthCollector(
            devices=devices,
            snmp_client=self.client,
            offline_failures=3,
            monotonic_clock=self.monotonic_clock,
            wall_clock=self.wall_clock,
        )

        first_r1 = collector.poll_device("R1")

        self.assertEqual(
            first_r1.status,
            CollectorStatus.WARMING_UP,
        )

        self.advance(2)

        self.client.reading = InterfaceCounters(
            admin_status=1,
            oper_status=1,
            in_octets=2000,
            out_octets=3000,
        )

        second_r1 = collector.poll_device("R1")
        first_r2 = collector.poll_device("R2")

        self.assertEqual(
            second_r1.status,
            CollectorStatus.ONLINE,
        )

        # R2 vẫn phải tạo baseline riêng,
        # không được dùng baseline của R1.
        self.assertEqual(
            first_r2.status,
            CollectorStatus.WARMING_UP,
        )

        self.assertIsNone(
            first_r2.value_mbps,
        )

if __name__ == "__main__":
    unittest.main()
