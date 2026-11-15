import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from edge_orchestrator.network.monitor_api import dashboard_snapshot, MonitorState


def fixture():
    config=SimpleNamespace(QOS_NORMAL_BW_MBPS=1,QOS_ALERT_BW_MBPS=8,AI_OBSERVATION_TTL_SEC=3.5)
    states={name:dict(applied_policy='ALERT',pending_policy=None,last_error=None,next_attempt_at=114.)
            for name in ('R1','R2')}
    sync=SimpleNamespace(snapshot=lambda:states,verify_sec=15)
    d=SimpleNamespace(_ai_available=True,simulate_ai=False,simulate_netmiko=False,
        _ai_state='ALERT',_desired_policy='ALERT',qos_sync=sync,
        _ai_observations_by_camera={'CAM03':dict(observed_at=99.,person_detected=True,frame_id=15)},
        bandwidth_store=SimpleNamespace(snapshot=lambda **kw:{}),
        ai_camera_workers={'CAM03':SimpleNamespace(get_diagnostics=lambda:dict(preview_fps=16,
            rtp=dict(status='RUNNING',written=400,error=None,encoder_stderr='PRIVATE_FIXTURE')))})
    return d,config,states


class MonitorSnapshotTests(unittest.TestCase):
    def test_fresh_snapshot_keeps_desired_applied_and_observation_distinct(self):
        d,c,states=fixture();states['R1']['pending_policy']='NORMAL';d._desired_policy='NORMAL'
        data=dashboard_snapshot(d,c,mono=100.,wall=1000.)
        self.assertEqual(data['ai']['state'],'ALERT')
        self.assertEqual(data['ai']['observation_age_ms'],1000)
        self.assertEqual(data['qos']['desired_policy'],'NORMAL')
        r=data['qos']['routers']['R1']
        self.assertEqual((r['applied_policy'],r['applied_kbps'],r['verification']),('ALERT',8000,'PENDING'))
        self.assertEqual(r['verified_age_ms'],1000)

    def test_stale_ai_never_reports_normal_or_no_person(self):
        d,c,_=fixture();d._ai_state='NORMAL'
        data=dashboard_snapshot(d,c,mono=110.,wall=1000.)
        self.assertEqual(data['ai']['state'],'UNKNOWN')
        self.assertIsNone(data['ai']['person_in_roi'])
        self.assertEqual(data['ai']['last_decision'],'NORMAL')

    def test_old_readback_and_simulation_are_not_verified(self):
        d,c,_=fixture()
        data=dashboard_snapshot(d,c,mono=150.,wall=1000.)
        self.assertEqual(data['qos']['routers']['R2']['verification'],'STALE')
        d.simulate_netmiko=True
        self.assertEqual(dashboard_snapshot(d,c,mono=100.,wall=1000.)['qos']['routers']['R2']['verification'],'SIMULATED')

    def test_export_does_not_leak_raw_exceptions_encoder_logs_or_credentials(self):
        d,c,states=fixture();states['R1']['last_error']='PRIVATE_FIXTURE'
        d.TELEGRAM_BOT_TOKEN='PRIVATE_FIXTURE'
        encoded=json.dumps(dashboard_snapshot(d,c,mono=100.,wall=1000.))
        self.assertNotIn('PRIVATE_FIXTURE',encoded)
        data=json.loads(encoded)
        self.assertEqual(data['qos']['routers']['R1']['verification'],'ERROR')

    def test_publisher_age_increases_when_gui_stops_updating(self):
        now=[10.];state=MonitorState(clock=lambda:now[0])
        self.assertIsNone(state.snapshot())
        state.publish({'value':1});now[0]+=5
        self.assertEqual(state.snapshot()['status_age_ms'],5000)
        state.snapshot()['value']=2
        self.assertEqual(state.snapshot()['value'],1)
        with self.assertRaises(ValueError):state.publish({'value':float('nan')})


if __name__=='__main__':unittest.main()
