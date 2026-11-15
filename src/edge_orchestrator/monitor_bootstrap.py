"""Run the existing Pi application with an optional read-only monitor publisher."""
import runpy


def main():
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication
    from edge_orchestrator.core import config
    from edge_orchestrator.gui.dashboard import MainDashboard
    from edge_orchestrator.network.monitor_api import MonitorState, MonitorServer, dashboard_snapshot

    original = MainDashboard.__init__

    def monitored_init(self, *args, **kwargs):
        state = MonitorState()
        server = MonitorServer(state)
        # Reserve the API port before the original application opens its camera.
        server.start()
        try:
            original(self, *args, **kwargs)
            timer = QTimer(self)
            previous_error = [None]

            def publish():
                try:
                    state.publish(dashboard_snapshot(self, config))
                    previous_error[0] = None
                except Exception as error:
                    # The last snapshot ages naturally; never label it fresh on failure.
                    name = type(error).__name__
                    if name != previous_error[0]:
                        print('[MONITOR] Snapshot unavailable: '+name, flush=True)
                        previous_error[0] = name

            timer.timeout.connect(publish)
            timer.start(1000)
            publish()
            QApplication.instance().aboutToQuit.connect(server.close)
            self._mac_monitor = (state, server, timer)
            print('[MONITOR] Read-only status API listening on 127.0.0.1:8765', flush=True)
        except BaseException:
            server.close()
            raise

    MainDashboard.__init__ = monitored_init
    runpy.run_module('edge_orchestrator.main', run_name='__main__')


if __name__ == '__main__':
    main()
