from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from edge_orchestrator import monitor_bootstrap
from edge_orchestrator.network.monitor_api import MonitorState
from tests.test_monitor_api import fixture


class BootstrapTests(unittest.TestCase):
    def test_real_bootstrap_publishes_from_gui_timer_and_closes_on_app_quit(self):
        events=[];d,_,_=fixture()

        class Signal:
            def connect(self, callback):self.callback=callback
        quitting=Signal()

        class Timer:
            def __init__(self,parent):self.timeout=Signal();self.interval=None
            def start(self,interval):self.interval=interval

        class Server:
            def __init__(self,state):self.state=state
            def start(self):events.append('server-start')
            def close(self):events.append('server-close')

        class Dashboard:
            def __init__(self):
                events.append('original-dashboard')
                self.__dict__.update(d.__dict__)

        core=ModuleType('PyQt5.QtCore');core.QTimer=Timer
        widgets=ModuleType('PyQt5.QtWidgets');widgets.QApplication=SimpleNamespace(instance=lambda:SimpleNamespace(aboutToQuit=quitting))
        dashboard=ModuleType('edge_orchestrator.gui.dashboard');dashboard.MainDashboard=Dashboard
        modules={'PyQt5':ModuleType('PyQt5'),'PyQt5.QtCore':core,'PyQt5.QtWidgets':widgets,
                 'edge_orchestrator.gui.dashboard':dashboard}

        def run_module(name,run_name):
            self.assertEqual((name,run_name),('edge_orchestrator.main','__main__'))
            window=Dashboard()
            state,server,timer=window._mac_monitor
            self.assertEqual(timer.interval,1000)
            self.assertEqual(state.snapshot()['source'],'pi-edge-orchestrator')
            self.assertEqual(state.snapshot()['qos']['routers']['R1']['applied_policy'],'ALERT')
            timer.timeout.callback()
            quitting.callback()

        with patch.dict('sys.modules',modules),patch('edge_orchestrator.network.monitor_api.MonitorServer',Server),patch.object(monitor_bootstrap.runpy,'run_module',side_effect=run_module):
            monitor_bootstrap.main()
        self.assertEqual(events,['server-start','original-dashboard','server-close'])


if __name__=='__main__':unittest.main()
