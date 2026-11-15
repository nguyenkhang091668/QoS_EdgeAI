"""Cached SNMP -> public device status, including failure and freshness boundaries."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from edge_orchestrator.network.bandwidth_store import BandwidthStore
from edge_orchestrator.network.bandwidth_types import BandwidthSample, CollectorStatus
from edge_orchestrator.network.monitor_api import dashboard_snapshot
from tests.test_monitor_api import fixture


NAMES = ('R1', 'R2', 'DSW1', 'DSW2', 'ASW')


class DeviceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.dashboard, self.config, _ = fixture()
        self.config.NETWORK_DEVICES = [dict(name=n, ip='192.0.2.'+str(i+1)) for i, n in enumerate(NAMES)]
        self.config.SNMP_DEVICES = {n+'_'+str(i): dict(host='192.0.2.1', interface='GigabitEthernet0/'+str(i),
                                                  direction='rx', label=n+' uplink '+str(i))
                                    for n in NAMES for i in range(3)}
        self.store = BandwidthStore(self.config.SNMP_DEVICES, stale_after_sec=6, clock=lambda:1000.)
        self.dashboard.bandwidth_store = self.store
        for source_id in self.config.SNMP_DEVICES:
            self.put(source_id)

    def put(self, source_id, status=CollectorStatus.ONLINE, at=999., value=.5):
        self.store.update(BandwidthSample(source_id, 'snmp', status, at,
            value_mbps=value, rx_mbps=value, tx_mbps=.1,
            details={'secret': 'PRIVATE_FIXTURE'}, error='PRIVATE_FIXTURE'))

    def snapshot(self, wall=1000.):
        return dashboard_snapshot(self.dashboard, self.config, mono=100., wall=wall)['devices']

    def test_exports_all_five_devices_and_fifteen_interfaces_from_one_cache_read(self):
        self.store.snapshot = Mock(wraps=self.store.snapshot)
        devices = self.snapshot()
        self.assertEqual(set(devices), set(NAMES))
        self.assertEqual(sum(len(d['interfaces']) for d in devices.values()), 15)
        self.assertTrue(all(d['status'] == 'ONLINE' for d in devices.values()))
        self.assertEqual(devices['DSW1']['interfaces'][0]['rx_mbps'], .5)
        self.assertEqual(devices['DSW1']['age_ms'], 1000)
        self.store.snapshot.assert_called_once_with(now=1000.)
        self.assertNotIn('PRIVATE_FIXTURE', str(devices))

    def test_down_interface_does_not_mean_device_is_offline(self):
        self.put('ASW_0', CollectorStatus.INTERFACE_DOWN, at=1000., value=None)
        device = self.snapshot()['ASW']
        self.assertEqual(device['status'], 'ONLINE')
        self.assertEqual(device['interfaces'][0]['status'], 'INTERFACE_DOWN')
        self.assertIsNone(device['interfaces'][0]['mbps'])

    def test_only_down_interfaces_still_prove_snmp_response(self):
        for i in range(3): self.put('DSW2_'+str(i), CollectorStatus.INTERFACE_DOWN, at=1000., value=None)
        self.assertEqual(self.snapshot()['DSW2']['status'], 'ONLINE')

    def test_expired_samples_are_stale_and_hide_rates_even_if_store_is_stale(self):
        device = self.snapshot(wall=1007.)['DSW1']
        self.assertEqual(device['status'], 'STALE')
        self.assertTrue(all(p['status'] == 'STALE' and p['mbps'] is None for p in device['interfaces']))
        self.assertEqual(device['ttl_ms'], 6000)

    def test_online_age_tracks_last_success_not_a_newer_failure(self):
        self.put('ASW_0', CollectorStatus.COLLECTOR_ERROR, at=1000., value=None)
        self.assertEqual(self.snapshot()['ASW']['age_ms'], 1000)

    def test_offline_and_collector_error_are_distinct(self):
        for i in range(3):
            self.put('DSW1_'+str(i), CollectorStatus.OFFLINE, at=1000., value=None)
            self.put('DSW2_'+str(i), CollectorStatus.COLLECTOR_ERROR, at=1000., value=None)
        self.assertEqual(self.snapshot()['DSW1']['status'], 'OFFLINE')
        self.assertEqual(self.snapshot()['DSW2']['status'], 'COLLECTOR_ERROR')

    def test_missing_samples_do_not_claim_online(self):
        self.dashboard.bandwidth_store = BandwidthStore(self.config.SNMP_DEVICES, clock=lambda:1000.)
        self.assertEqual(self.snapshot()['ASW']['status'], 'WARMING_UP')
        self.assertEqual(self.snapshot(wall=1007.)['ASW']['status'], 'STALE')

    def test_simulation_cannot_claim_real_device_health(self):
        self.dashboard.simulate_netmiko = True
        device = self.snapshot()['ASW']
        self.assertEqual(device['status'], 'SIMULATED')
        self.assertTrue(all(p['mbps'] is None for p in device['interfaces']))


if __name__ == '__main__': unittest.main()
