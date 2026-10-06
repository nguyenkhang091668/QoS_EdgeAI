import threading
import time

from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Protocol

from edge_orchestrator.network.bandwidth_types import (
    BandwidthSample,
    CollectorStatus,
)


class SnmpTimeoutError(Exception):
    """Thiết bị không phản hồi trong thời gian SNMP timeout."""


class SnmpReadError(Exception):
    """SNMP trả về dữ liệu lỗi hoặc không tìm thấy OID/interface."""


@dataclass(frozen=True)
class InterfaceCounters:
    admin_status: int
    oper_status: int
    in_octets: int
    out_octets: int


class SnmpClientProtocol(Protocol):
    def resolve_if_index(
        self,
        host,
        interface_name,
    ):
        """Tìm ifIndex từ IF-MIB::ifName."""

    def read_interface(
        self,
        host,
        if_index,
    ):
        """Trả về InterfaceCounters."""


@dataclass(frozen=True)
class CounterObservation:
    in_octets: int
    out_octets: int
    monotonic_at: float


@dataclass
class DeviceCounterState:
    if_index: Optional[int] = None
    observations: deque[
        CounterObservation
    ] = field(default_factory=deque)
    failure_count: int = 0


class CiscoBandwidthCollector:
    """Tính bandwidth từ counter SNMP của thiết bị Cisco."""

    def __init__(
        self,
        devices,
        snmp_client,
        offline_failures=3,
        rate_window_sec=6.0,
        monotonic_clock=time.monotonic,
        wall_clock=time.time,
    ):
        if not devices:
            raise ValueError("devices must not be empty")

        if offline_failures <= 0:
            raise ValueError(
                "offline_failures must be positive"
            )

        if rate_window_sec <= 0:
            raise ValueError(
                "rate_window_sec must be positive"
            )

        self.devices = {
            source_id: dict(device_config)
            for source_id, device_config
            in devices.items()
        }

        self.snmp_client = snmp_client
        self.offline_failures = int(
            offline_failures
        )
        self.rate_window_sec = float(
            rate_window_sec
        )
        self._monotonic_clock = monotonic_clock
        self._wall_clock = wall_clock

        self._states = {
            source_id: DeviceCounterState()
            for source_id in self.devices
        }

        # Mỗi thiết bị có lock riêng để worker có thể
        # polling năm thiết bị đồng thời.
        self._locks = {
            source_id: threading.RLock()
            for source_id in self.devices
        }

        self._validate_device_configs()

    @property
    def source_ids(self):
        return tuple(self.devices.keys())

    def poll_all(self):
        """Polling tuần tự, chủ yếu dùng cho test."""
        return [
            self.poll_device(source_id)
            for source_id in self.source_ids
        ]

    def poll_device(self, source_id):
        if source_id not in self.devices:
            raise KeyError(
                f"Unknown SNMP source: {source_id}"
            )

        with self._locks[source_id]:
            return self._poll_device_locked(
                source_id
            )

    def _poll_device_locked(self, source_id):
        config = self.devices[source_id]
        state = self._states[source_id]

        try:
            if state.if_index is None:
                if_index = (
                    self.snmp_client.resolve_if_index(
                        config["host"],
                        config["interface"],
                    )
                )

                if not isinstance(if_index, int):
                    raise SnmpReadError(
                        "ifIndex must be an integer"
                    )

                if if_index <= 0:
                    raise SnmpReadError(
                        "ifIndex must be positive"
                    )

                state.if_index = if_index

            counters = (
                self.snmp_client.read_interface(
                    config["host"],
                    state.if_index,
                )
            )

            self._validate_counters(counters)

        except SnmpTimeoutError as error:
            return self._handle_timeout(
                source_id,
                config,
                state,
                error,
            )

        except SnmpReadError as error:
            state.failure_count = 0
            state.if_index = None
            self._reset_baseline(state)

            return self._unavailable(
                source_id,
                config,
                CollectorStatus.COLLECTOR_ERROR,
                error,
            )

        except Exception as error:
            # Không để worker chết vì một thiết bị.
            return self._unavailable(
                source_id,
                config,
                CollectorStatus.COLLECTOR_ERROR,
                error,
            )

        # Thiết bị đã phản hồi SNMP thành công.
        state.failure_count = 0

        if (
            counters.admin_status != 1
            or counters.oper_status != 1
        ):
            self._reset_baseline(state)

            return self._unavailable(
                source_id,
                config,
                CollectorStatus.INTERFACE_DOWN,
                None,
                extra_details={
                    "admin_status":
                        counters.admin_status,
                    "oper_status":
                        counters.oper_status,
                    "if_index":
                        state.if_index,
                },
            )

        now_monotonic = float(
            self._monotonic_clock()
        )
        now_wall = float(self._wall_clock())

        observation = CounterObservation(
            in_octets=counters.in_octets,
            out_octets=counters.out_octets,
            monotonic_at=now_monotonic,
        )

        if not state.observations:
            state.observations.append(observation)

            return self._unavailable(
                source_id,
                config,
                CollectorStatus.WARMING_UP,
                None,
                sampled_at=now_wall,
                extra_details={
                    "if_index": state.if_index,
                    "rate_window_sec":
                        self.rate_window_sec,
                },
            )

        previous = state.observations[-1]

        counter_reset = (
            observation.in_octets
            < previous.in_octets
            or observation.out_octets
            < previous.out_octets
        )

        invalid_time = (
            observation.monotonic_at
            <= previous.monotonic_at
        )

        if counter_reset or invalid_time:
            self._reset_baseline(state)
            state.observations.append(observation)

            return self._unavailable(
                source_id,
                config,
                CollectorStatus.WARMING_UP,
                None,
                sampled_at=now_wall,
                extra_details={
                    "if_index": state.if_index,
                    "counter_reset": counter_reset,
                    "rate_window_sec":
                        self.rate_window_sec,
                },
            )

        state.observations.append(observation)

        cutoff = (
            now_monotonic
            - self.rate_window_sec
        )

        # Giữ lại observation gần nhất nhưng không
        # mới hơn mốc cutoff. Nó là baseline cho
        # cửa sổ đo hiện tại.
        while (
            len(state.observations) > 1
            and state.observations[1].monotonic_at
            <= cutoff
        ):
            state.observations.popleft()

        baseline = state.observations[0]

        elapsed = (
            observation.monotonic_at
            - baseline.monotonic_at
        )

        if elapsed < self.rate_window_sec:
            return self._unavailable(
                source_id,
                config,
                CollectorStatus.WARMING_UP,
                None,
                sampled_at=now_wall,
                extra_details={
                    "if_index": state.if_index,
                    "elapsed_sec": elapsed,
                    "rate_window_sec":
                        self.rate_window_sec,
                },
            )

        delta_in = (
            observation.in_octets
            - baseline.in_octets
        )

        delta_out = (
            observation.out_octets
            - baseline.out_octets
        )

        rx_mbps = (
            delta_in
            * 8
            / elapsed
            / 1_000_000
        )

        tx_mbps = (
            delta_out
            * 8
            / elapsed
            / 1_000_000
        )
        direction = config["direction"]
        if direction == "rx":
            display_value = rx_mbps
        else:
            display_value = tx_mbps

        return BandwidthSample(
            source_id=source_id,
            source_type="snmp",
            status=CollectorStatus.ONLINE,
            sampled_at=now_wall,
            value_mbps=display_value,
            rx_mbps=rx_mbps,
            tx_mbps=tx_mbps,
            details=self._details(
                config,
                {
                    "if_index": state.if_index,
                    "elapsed_sec": elapsed,
                    "rate_window_sec": self.rate_window_sec,
                },
            ),
            error=None,
        )

    def _handle_timeout(
        self,
        source_id,
        config,
        state,
        error,
    ):
        state.failure_count += 1

        if (
            state.failure_count
            >= self.offline_failures
        ):
            status = CollectorStatus.OFFLINE

            # Khi khôi phục phải lấy baseline mới.
            self._reset_baseline(state)
        else:
            status = (
                CollectorStatus.COLLECTOR_ERROR
            )

        return self._unavailable(
            source_id,
            config,
            status,
            error,
            extra_details={
                "if_index": state.if_index,
                "failure_count":
                    state.failure_count,
            },
        )

    def _unavailable(
        self,
        source_id,
        config,
        status,
        error=None,
        sampled_at=None,
        extra_details=None,
    ):
        return BandwidthSample.unavailable(
            source_id=source_id,
            source_type="snmp",
            status=status,
            sampled_at=(
                self._wall_clock()
                if sampled_at is None
                else sampled_at
            ),
            details=self._details(
                config,
                extra_details,
            ),
            error=(
                None
                if error is None
                else str(error)
            ),
        )

    @staticmethod
    def _details(
        config,
        extra_details=None,
    ):
        details = {
            "host": config["host"],
            "interface": config["interface"],
            "direction": config["direction"],
            "label": config.get("label", ""),
        }

        if extra_details:
            details.update(extra_details)

        return details

    @staticmethod
    def _reset_baseline(state):
        state.observations.clear()

    @staticmethod
    def _validate_counters(counters):
        if not isinstance(
            counters,
            InterfaceCounters,
        ):
            raise SnmpReadError(
                "read_interface must return "
                "InterfaceCounters"
            )

        for name in (
            "admin_status",
            "oper_status",
            "in_octets",
            "out_octets",
        ):
            value = getattr(counters, name)

            if not isinstance(value, int):
                raise SnmpReadError(
                    f"{name} must be an integer"
                )

            if value < 0:
                raise SnmpReadError(
                    f"{name} must not be negative"
                )

    def _validate_device_configs(self):
        for source_id, config in (
            self.devices.items()
        ):
            for field_name in (
                "host",
                "interface",
                "direction",
            ):
                if not config.get(field_name):
                    raise ValueError(
                        f"{source_id} missing "
                        f"{field_name}"
                    )

            if (
                config["direction"]
                not in {"rx", "tx"}
            ):
                raise ValueError(
                    f"{source_id} direction "
                    "must be rx or tx"
                )
