import threading
import time

from concurrent.futures import (
    ThreadPoolExecutor,
)

from PyQt5.QtCore import (
    QThread,
    pyqtSignal,
)

from edge_orchestrator.network.bandwidth_types import (
    BandwidthSample,
    CollectorStatus,
)


class CiscoBandwidthWorker(QThread):
    """Polling các thiết bị Cisco mà không chặn GUI."""

    samples_signal = pyqtSignal(list)
    worker_error_signal = pyqtSignal(str)

    def __init__(
        self,
        collector,
        interval_sec=2.0,
        max_workers=5,
        parent=None,
    ):
        super().__init__(parent)

        if interval_sec <= 0:
            raise ValueError(
                "interval_sec must be positive"
            )

        if max_workers <= 0:
            raise ValueError(
                "max_workers must be positive"
            )

        self.collector = collector
        self.interval_sec = float(
            interval_sec
        )
        self.max_workers = min(
            int(max_workers),
            len(collector.source_ids),
        )

        self._stop_event = threading.Event()

    def run(self):
        next_deadline = time.monotonic()

        try:
            with ThreadPoolExecutor(
                max_workers=self.max_workers,
                thread_name_prefix="snmp-poll",
            ) as executor:
                while not self._stop_event.is_set():
                    samples = self._poll_cycle(
                        executor
                    )

                    if samples:
                        self.samples_signal.emit(
                            samples
                        )

                    next_deadline += (
                        self.interval_sec
                    )

                    delay = (
                        next_deadline
                        - time.monotonic()
                    )

                    if delay < 0:
                        # Chu kỳ trước đã chạy quá lâu.
                        next_deadline = (
                            time.monotonic()
                        )
                        delay = 0

                    self._stop_event.wait(delay)

        except Exception as error:
            self.worker_error_signal.emit(
                str(error)
            )

        finally:
            close_collector = getattr(
                self.collector,
                "close",
                None,
            )

            if callable(close_collector):
                try:
                    close_collector()

                except Exception as error:
                    self.worker_error_signal.emit(
                        "SNMP cleanup failed: "
                        f"{error}"
                    )

    def stop(self):
        self._stop_event.set()
        self.requestInterruption()

    def _poll_cycle(self, executor):
        source_ids = (
            self.collector.source_ids
        )

        futures = {
            source_id: executor.submit(
                self.collector.poll_device,
                source_id,
            )
            for source_id in source_ids
        }

        samples = []

        for source_id in source_ids:
            future = futures[source_id]

            try:
                sample = future.result()

            except Exception as error:
                config = (
                    self.collector.devices[
                        source_id
                    ]
                )

                sample = (
                    BandwidthSample.unavailable(
                        source_id=source_id,
                        source_type="snmp",
                        status=(
                            CollectorStatus
                            .COLLECTOR_ERROR
                        ),
                        sampled_at=time.time(),
                        details={
                            "host":
                                config["host"],
                            "interface":
                                config["interface"],
                            "direction":
                                config["direction"],
                            "label":
                                config.get(
                                    "label",
                                    "",
                                ),
                        },
                        error=str(error),
                    )
                )

            samples.append(sample)

        return samples
