import math
import threading
import time
from collections import deque
from edge_orchestrator.network.bandwidth_types import CollectorStatus, BandwidthSample


class BandwidthStore:
    """Lưu sample hiện tại và lịch sử bandwidth của các nguồn."""

    def __init__(
        self,
        source_ids,
        history_sec=60.0,
        stale_after_sec=6.0,
        clock=time.time,
    ):
        source_ids = tuple(source_ids)

        if not source_ids:
            raise ValueError("source_ids must not be empty")

        if len(set(source_ids)) != len(source_ids):
            raise ValueError("source_ids must be unique")

        if history_sec <= 0:
            raise ValueError("history_sec must be positive")

        if stale_after_sec <= 0:
            raise ValueError("stale_after_sec must be positive")

        self.history_sec = float(history_sec)
        self.stale_after_sec = float(stale_after_sec)
        self._clock = clock
        self._created_at = float(clock())

        self._latest = {}
        self._last_timestamp = {}

        self._history = {
            source_id: deque()
            for source_id in source_ids
        }

        self._lock = threading.RLock()

    @property
    def source_ids(self):
        return tuple(self._history.keys())

    def update(self, sample):
        if not isinstance(sample, BandwidthSample):
            raise TypeError("sample must be a BandwidthSample")

        source_id = sample.source_id

        with self._lock:
            if source_id not in self._history:
                raise KeyError(
                    "Unknown bandwidth source: "
                    f"{source_id}"
                )

            previous_timestamp = self._last_timestamp.get(
                source_id
            )

            # Bỏ sample bị lặp hoặc đến sai thứ tự.
            if (
                previous_timestamp is not None
                and sample.sampled_at <= previous_timestamp
            ):
                return False

            self._latest[source_id] = sample
            self._last_timestamp[source_id] = (
                sample.sampled_at
            )

            if sample.value_mbps is None:
                graph_value = math.nan
            else:
                graph_value = float(sample.value_mbps)

            self._history[source_id].append(
                (
                    float(sample.sampled_at),
                    graph_value,
                )
            )

            self._trim_locked(
                now=float(sample.sampled_at)
            )

            return True

    def update_many(self, samples):
        results = []

        for sample in samples:
            results.append(self.update(sample))

        return results

    def latest(self, source_id):
        with self._lock:
            self._require_source(source_id)
            return self._latest.get(source_id)

    def effective_status(self, source_id, now=None):
        with self._lock:
            self._require_source(source_id)

            current_time = (
                float(self._clock())
                if now is None
                else float(now)
            )

            sample = self._latest.get(source_id)

            if sample is None:
                age = current_time - self._created_at

                if age > self.stale_after_sec:
                    return CollectorStatus.STALE

                return CollectorStatus.WARMING_UP

            age = current_time - sample.sampled_at

            if age > self.stale_after_sec:
                return CollectorStatus.STALE

            return sample.status

    def history(self, source_id, now=None):
        with self._lock:
            self._require_source(source_id)

            current_time = (
                float(self._clock())
                if now is None
                else float(now)
            )

            self._trim_locked(current_time)

            return list(self._history[source_id])

    def snapshot(self, now=None):
        with self._lock:
            current_time = (
                float(self._clock())
                if now is None
                else float(now)
            )

            self._trim_locked(current_time)

            return {
                source_id: {
                    "sample": self._latest.get(source_id),
                    "status": self.effective_status(
                        source_id,
                        now=current_time,
                    ),
                    "history": list(
                        self._history[source_id]
                    ),
                }
                for source_id in self._history
            }

    def _trim_locked(self, now):
        cutoff = now - self.history_sec

        for points in self._history.values():
            while points and points[0][0] < cutoff:
                points.popleft()

    def _require_source(self, source_id):
        if source_id not in self._history:
            raise KeyError(
                "Unknown bandwidth source: "
                f"{source_id}"
            )
