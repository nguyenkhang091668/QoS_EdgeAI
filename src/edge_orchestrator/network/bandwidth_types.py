import time

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class CollectorStatus(str, Enum):
    WARMING_UP = "WARMING_UP"
    ONLINE = "ONLINE"
    STREAMING = "STREAMING"
    NO_STREAM = "NO_STREAM"
    INTERFACE_DOWN = "INTERFACE_DOWN"
    OFFLINE = "OFFLINE"
    COLLECTOR_ERROR = "COLLECTOR_ERROR"
    STALE = "STALE"


@dataclass(frozen=True)
class BandwidthSample:
    source_id: str
    source_type: str
    status: CollectorStatus
    sampled_at: float
    value_mbps: Optional[float] = None
    rx_mbps: Optional[float] = None
    tx_mbps: Optional[float] = None
    details: dict = field(default_factory=dict)
    error: Optional[str] = None

    @classmethod
    def unavailable(
        cls,
        source_id,
        source_type,
        status,
        error=None,
        details=None,
        sampled_at=None,
    ):
        return cls(
            source_id=source_id,
            source_type=source_type,
            status=status,
            sampled_at=(
                time.time()
                if sampled_at is None
                else float(sampled_at)
            ),
            value_mbps=None,
            details=details or {},
            error=error,
        )
