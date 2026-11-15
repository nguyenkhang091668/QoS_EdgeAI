"""Profile selection reaches real controller writes, verification and sync events."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

from edge_orchestrator.core import config
from edge_orchestrator.core.qos_profile import load_qos_profile, QoSProfile
from edge_orchestrator.network import network_controller as controller_module
from edge_orchestrator.network.qos_sync import RouterQoSSynchronizer


def policy_text(kbps):
    return ('policy-map TDTU_DYNAMIC_QOS\n class CAMERA_CLASS\n'
            f'  bandwidth {kbps}\n  set ip dscp af41\n')


class QoSProfileTests(unittest.TestCase):
    def test_explicit_selection_defaults_and_invalid_input(self):
        self.assertEqual(load_qos_profile({}).bandwidth_kbps('ALERT'),8000)
        self.assertEqual(load_qos_profile({'EDGE_QOS_PROFILE':'lab-1m'}).normal_kbps,200)
        for value in ('', 'lab-1m; echo injected', '8000'):
            with self.assertRaises(ValueError):
                load_qos_profile({'EDGE_QOS_PROFILE':value})
        with self.assertRaises(ValueError):
            load_qos_profile({}).bandwidth_kbps('UNKNOWN')

    def test_invalid_bandwidth_cannot_create_a_profile(self):
        for values in ((True,700,1000000),(0,700,1000000),(800,700,1000000),
                       (200,8000,1000000)):
            with self.assertRaises(ValueError):
                QoSProfile('fixture',*values)

    def test_fresh_process_configuration_and_notification_keep_fractional_mbps(self):
        env=dict(os.environ, EDGE_QOS_PROFILE='lab-1m',
                 PYTHONPATH=str(Path(__file__).parents[1]/'src'),
                 PYTHONDONTWRITEBYTECODE='1')
        script='''import json
from edge_orchestrator.core import config
from edge_orchestrator.network.telegram_alerts import CameraAlert
a=CameraAlert('CAM03','fixture',1,0,b'',config.QOS_ALERT_BW_MBPS,'R2')
print(json.dumps([config.QOS_NORMAL_BW_KBPS,config.QOS_ALERT_BW_KBPS,
config.QOS_NORMAL_BW_MBPS,config.QOS_ALERT_BW_MBPS,a.caption()]))
'''
        result=subprocess.run([sys.executable,'-c',script],env=env,capture_output=True,text=True,check=True)
        values=json.loads(result.stdout)
        self.assertEqual(values[:4],[200,700,0.2,0.7])
        self.assertIn('0.7 Mbps (requested)',values[4])

    def controller(self, reads):
        patch.object(controller_module,'ConnectHandler',MagicMock()).start()
        patch.object(controller_module,'paramiko',MagicMock()).start()
        self.addCleanup(patch.stopall)
        controller=controller_module.NetworkController('R2',username='fixture',password='fixture')
        connection=MagicMock()
        connection.send_command.side_effect=reads
        connection.send_config_set.return_value='accepted'
        patch.object(controller,'_session').start().return_value.__enter__.return_value=connection
        return controller,connection

    def test_actual_lab_writes_readback_and_sync_log_use_700_then_200(self):
        with patch.object(config,'QOS_PROFILE',load_qos_profile({'EDGE_QOS_PROFILE':'lab-1m'})):
            controller,connection=self.controller([policy_text(1000),policy_text(700),
                                                   policy_text(700),policy_text(200)])
            events=[]
            sync=RouterQoSSynchronizer({'R2':controller},events.append)
            self.addCleanup(sync.close)
            for state,kbps in (('ALERT',700),('NORMAL',200)):
                sync.set_desired_policy(state)
                sync.reconcile()
                sync._states['R2'].thread.join(2)
                self.assertEqual(sync.snapshot()['R2']['applied_policy'],state)
                self.assertIn(f'  bandwidth {kbps}',connection.send_config_set.call_args.args[0])
                self.assertTrue(events[-1].success)
                self.assertIn(f'CAMERA_CLASS={kbps} kbps',events[-1].message)

    def test_old_8000_readback_is_rejected_instead_of_reporting_lab_success(self):
        with patch.object(config,'QOS_PROFILE',load_qos_profile({'EDGE_QOS_PROFILE':'lab-1m'})):
            controller,_=self.controller([policy_text(200),policy_text(8000)])
            with self.assertRaisesRegex(RuntimeError,'700 kbps'):
                controller.ensure_qos_policy('ALERT')


if __name__ == '__main__':
    unittest.main()
