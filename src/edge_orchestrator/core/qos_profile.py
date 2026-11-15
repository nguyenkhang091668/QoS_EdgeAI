"""Explicit camera queue guarantees; selecting a profile never configures a router."""

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class QoSProfile:
    name: str
    normal_kbps: int
    alert_kbps: int
    expected_parent_bps: int

    def __post_init__(self):
        values = (self.normal_kbps, self.alert_kbps, self.expected_parent_bps)
        if any(type(value) is not int or value <= 0 for value in values):
            raise ValueError("QoS bandwidth values must be positive integers")
        if self.normal_kbps > self.alert_kbps:
            raise ValueError("NORMAL bandwidth must not exceed ALERT bandwidth")
        if self.alert_kbps * 1000 > self.expected_parent_bps:
            raise ValueError("ALERT bandwidth exceeds the expected parent shaper")

    def bandwidth_kbps(self, policy):
        policy = policy.upper()
        if policy == "NORMAL":
            return self.normal_kbps
        if policy == "ALERT":
            return self.alert_kbps
        raise ValueError("QoS policy must be NORMAL or ALERT")


PROFILES = {
    "standard": QoSProfile("standard", 1000, 8000, 10_000_000),
    "lab-1m": QoSProfile("lab-1m", 200, 700, 1_000_000),
}


def load_qos_profile(environ=None):
    environ = os.environ if environ is None else environ
    name = environ.get("EDGE_QOS_PROFILE", "standard").strip().lower()
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError("EDGE_QOS_PROFILE must be standard or lab-1m") from None
