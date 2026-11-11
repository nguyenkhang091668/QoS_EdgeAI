import threading
import time

from concurrent.futures import (
    FIRST_COMPLETED,
    ThreadPoolExecutor,
    wait,
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
    """Polling các thiết bị Cisco độc lập từng host mà không chặn nguồn khỏe hoặc GUI."""

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
            max(1, len(collector.source_ids)),
        )

        self._stop_event = threading.Event()

    def _use_host_batch_mode(self) -> bool:
        host_groups = getattr(self.collector, "host_groups", None)
        poll_host_group = getattr(self.collector, "poll_host_group", None)
        if not host_groups or not callable(poll_host_group):
            return False
        supports_batch = getattr(self.collector, "_supports_batch_client", None)
        if callable(supports_batch):
            return bool(supports_batch())
        return True

    def _build_error_sample(self, source_id, error, sampled_at=None):
        config = self.collector.devices[source_id]
        return BandwidthSample.unavailable(
            source_id=source_id,
            source_type="snmp",
            status=CollectorStatus.COLLECTOR_ERROR,
            sampled_at=time.time() if sampled_at is None else sampled_at,
            details={
                "host": config["host"],
                "interface": config["interface"],
                "direction": config["direction"],
                "label": config.get("label", ""),
            },
            error=str(error),
        )

    def run(self):
        try:
            with ThreadPoolExecutor(
                max_workers=self.max_workers,
                thread_name_prefix="snmp-poll",
            ) as executor:
                if self._use_host_batch_mode():
                    self._run_host_batch_scheduler(executor)
                else:
                    self._run_source_scheduler(executor)

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

    def _run_host_batch_scheduler(self, executor):
        """Lập lịch độc lập từng host với batch SNMP theo host:
        - Tối đa 1 truy vấn đang chạy / host và tổng cộng <= max_workers.
        - Host nào xong phát mẫu ngay, không chờ host đang timeout (như R1 khi STOP).
        """
        host_groups = self.collector.host_groups
        poll_host_group = self.collector.poll_host_group
        hosts = tuple(host_groups.keys())
        if not hosts:
            return

        now = time.monotonic()
        next_due_at = {host: now for host in hosts}
        host_last_seq = {host: idx for idx, host in enumerate(hosts)}
        seq_counter = len(hosts)

        in_flight_hosts = set()
        active_futures = {}  # future -> (host, group_ids)

        while not self._stop_event.is_set():
            now = time.monotonic()

            if len(active_futures) < self.max_workers:
                due_hosts = [
                    host
                    for host in hosts
                    if host not in in_flight_hosts and now >= next_due_at[host]
                ]
                due_hosts.sort(key=lambda h: (host_last_seq[h], next_due_at[h]))

                for host in due_hosts:
                    if len(active_futures) >= self.max_workers or self._stop_event.is_set():
                        break
                    group_ids = host_groups[host]
                    in_flight_hosts.add(host)
                    seq_counter += 1
                    host_last_seq[host] = seq_counter
                    next_due_at[host] = now + self.interval_sec
                    future = executor.submit(poll_host_group, host)
                    active_futures[future] = (host, group_ids)

            if self._stop_event.is_set():
                break

            now = time.monotonic()
            idle_hosts = [h for h in hosts if h not in in_flight_hosts]
            if idle_hosts and len(active_futures) < self.max_workers:
                earliest_due = min(next_due_at[h] for h in idle_hosts)
                wait_timeout = max(0.0, earliest_due - now)
            else:
                wait_timeout = self.interval_sec

            if not active_futures:
                self._stop_event.wait(min(wait_timeout, self.interval_sec))
                continue

            done, _ = wait(
                tuple(active_futures.keys()),
                timeout=min(wait_timeout, 0.05),
                return_when=FIRST_COMPLETED,
            )

            if not done:
                continue

            ready_samples = []
            for future in done:
                host, group_ids = active_futures.pop(future)
                in_flight_hosts.discard(host)
                try:
                    group_samples = future.result()
                except Exception as error:
                    sampled_at = time.time()
                    group_samples = [
                        self._build_error_sample(sid, error, sampled_at=sampled_at)
                        for sid in group_ids
                    ]

                if not self._stop_event.is_set() and group_samples:
                    ready_samples.extend(group_samples)

            if not self._stop_event.is_set() and ready_samples:
                self.samples_signal.emit(ready_samples)

    def _run_source_scheduler(self, executor):
        """Lập lịch độc lập từng nguồn nhưng khóa tối đa 1 truy vấn/host tại mọi thời điểm."""
        source_ids = tuple(self.collector.source_ids)
        if not source_ids:
            return

        source_host = {
            sid: self.collector.devices[sid]["host"]
            for sid in source_ids
        }
        hosts = tuple(dict.fromkeys(source_host.values()))

        now = time.monotonic()
        next_due_at = {sid: now for sid in source_ids}
        host_last_seq = {host: idx for idx, host in enumerate(hosts)}
        source_last_seq = {sid: idx for idx, sid in enumerate(source_ids)}
        seq_counter = len(source_ids)

        in_flight_hosts = set()
        in_flight_sources = set()
        active_futures = {}  # future -> (sid, host)

        while not self._stop_event.is_set():
            now = time.monotonic()

            if len(active_futures) < self.max_workers:
                due_sources = [
                    sid
                    for sid in source_ids
                    if sid not in in_flight_sources
                    and source_host[sid] not in in_flight_hosts
                    and now >= next_due_at[sid]
                ]
                due_sources.sort(
                    key=lambda sid: (
                        host_last_seq[source_host[sid]],
                        source_last_seq[sid],
                        next_due_at[sid],
                    )
                )

                for sid in due_sources:
                    if len(active_futures) >= self.max_workers or self._stop_event.is_set():
                        break
                    host = source_host[sid]
                    if host in in_flight_hosts or sid in in_flight_sources:
                        continue
                    in_flight_hosts.add(host)
                    in_flight_sources.add(sid)
                    seq_counter += 1
                    host_last_seq[host] = seq_counter
                    source_last_seq[sid] = seq_counter
                    next_due_at[sid] = now + self.interval_sec
                    future = executor.submit(self.collector.poll_device, sid)
                    active_futures[future] = (sid, host)

            if self._stop_event.is_set():
                break

            now = time.monotonic()
            eligible_idle = [
                sid
                for sid in source_ids
                if sid not in in_flight_sources
                and source_host[sid] not in in_flight_hosts
            ]
            if eligible_idle and len(active_futures) < self.max_workers:
                earliest_due = min(next_due_at[sid] for sid in eligible_idle)
                wait_timeout = max(0.0, earliest_due - now)
            else:
                wait_timeout = self.interval_sec

            if not active_futures:
                self._stop_event.wait(min(wait_timeout, self.interval_sec))
                continue

            done, _ = wait(
                tuple(active_futures.keys()),
                timeout=min(wait_timeout, 0.02),
                return_when=FIRST_COMPLETED,
            )

            if not done:
                continue

            ready_samples = []
            for future in done:
                sid, host = active_futures.pop(future)
                in_flight_sources.discard(sid)
                in_flight_hosts.discard(host)
                try:
                    sample = future.result()
                except Exception as error:
                    sample = self._build_error_sample(sid, error)

                if not self._stop_event.is_set() and sample is not None:
                    ready_samples.append(sample)

            if not self._stop_event.is_set() and ready_samples:
                self.samples_signal.emit(ready_samples)

    def stop(self):
        self._stop_event.set()
        self.requestInterruption()

    def _poll_cycle(self, executor):
        source_ids = self.collector.source_ids
        host_groups = getattr(self.collector, "host_groups", None)
        poll_host_group = getattr(self.collector, "poll_host_group", None)

        if host_groups and callable(poll_host_group):
            host_futures = {
                host: executor.submit(poll_host_group, host)
                for host in host_groups
            }
            samples_by_id = {}
            for host, group_ids in host_groups.items():
                future = host_futures[host]
                try:
                    group_samples = future.result()
                    for sample in group_samples:
                        samples_by_id[sample.source_id] = sample
                except Exception as error:
                    sampled_at = time.time()
                    for source_id in group_ids:
                        samples_by_id[source_id] = self._build_error_sample(
                            source_id, error, sampled_at=sampled_at
                        )
            return [samples_by_id[sid] for sid in source_ids]

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
                sample = self._build_error_sample(source_id, error)

            samples.append(sample)

        return samples
