import threading
import time

from concurrent.futures import ThreadPoolExecutor

from PyQt5.QtCore import QThread, pyqtSignal

from edge_orchestrator.network.bandwidth_types import (
    BandwidthSample,
    CollectorStatus,
)


class CiscoBandwidthWorker(QThread):
    """Poll hosts independently; publish each observation without a batch barrier.

    At most one job per host is queued/running, and interfaces of that host are
    read sequentially. The executor bounds total concurrency. An unreachable
    router therefore cannot occupy one worker per interface or hold fresh
    observations from other devices until its timeout finishes.
    """

    samples_signal = pyqtSignal(list)
    worker_error_signal = pyqtSignal(str)

    def __init__(self, collector, interval_sec=2.0, max_workers=5, parent=None):
        super().__init__(parent)
        if interval_sec <= 0:
            raise ValueError("interval_sec must be positive")
        if max_workers <= 0:
            raise ValueError("max_workers must be positive")

        self.collector = collector
        self.interval_sec = float(interval_sec)
        self._host_sources = {}
        for source_id in collector.source_ids:
            host = collector.devices[source_id]["host"]
            self._host_sources.setdefault(host, []).append(source_id)
        if not self._host_sources:
            raise ValueError("collector must have at least one host")
        self.max_workers = min(int(max_workers), len(self._host_sources))
        self._stop_event = threading.Event()

    def run(self):
        next_due = {host: time.monotonic() for host in self._host_sources}
        pending = {}
        try:
            with ThreadPoolExecutor(
                max_workers=self.max_workers, thread_name_prefix="snmp-host",
            ) as executor:
                try:
                    while not self._stop_event.is_set():
                        for host, (future, started_at) in list(pending.items()):
                            if not future.done():
                                continue
                            del pending[host]
                            try:
                                future.result()
                            except Exception as error:
                                self.worker_error_signal.emit(f"Host {host}: {error}")
                            next_due[host] = max(
                                started_at + self.interval_sec, time.monotonic(),
                            )

                        now = time.monotonic()
                        for host, source_ids in self._host_sources.items():
                            if self._stop_event.is_set():
                                break
                            if host in pending or now < next_due[host]:
                                continue
                            future = executor.submit(self._poll_host, tuple(source_ids))
                            pending[host] = (future, now)

                        self._stop_event.wait(0.05)
                finally:
                    self._stop_event.set()
                    for future, _ in pending.values():
                        future.cancel()
            # Executor shutdown waits for active reads before closing SNMP loops.
        except Exception as error:
            self.worker_error_signal.emit(str(error))
        finally:
            close_collector = getattr(self.collector, "close", None)
            if callable(close_collector):
                try:
                    close_collector()
                except Exception as error:
                    self.worker_error_signal.emit(f"SNMP cleanup failed: {error}")

    def stop(self):
        self._stop_event.set()
        self.requestInterruption()

    def _poll_host(self, source_ids):
        samples = []
        for source_id in source_ids:
            if self._stop_event.is_set():
                break
            sample = self._poll_source(source_id)
            if self._stop_event.is_set():
                break
            samples.append(sample)
            self.samples_signal.emit([sample])
        return samples

    def _poll_source(self, source_id):
        try:
            # Keep the collector's observation timestamp and rate baseline intact.
            return self.collector.poll_device(source_id)
        except Exception as error:
            device = self.collector.devices[source_id]
            return BandwidthSample.unavailable(
                source_id=source_id,
                source_type="snmp",
                status=CollectorStatus.COLLECTOR_ERROR,
                sampled_at=time.time(),
                details={
                    "host": device["host"],
                    "interface": device["interface"],
                    "direction": device["direction"],
                    "label": device.get("label", ""),
                },
                error=str(error),
            )
