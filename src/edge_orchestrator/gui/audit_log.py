from collections import deque
from dataclasses import dataclass
from datetime import datetime
import time

LOG_CATEGORIES = (
    "Edge AI",
    "Dynamic QoS",
    "Security/ACL",
    "Collector",
    "Cisco",
    "Syslog thô",
    "Hệ thống",
)

SEVERITY_LEVEL = ("CRITICAL", "WARNING", "ACTION", "INFO")


@dataclass
class AuditEntry:
    timestamp: str
    category: str
    message: str
    alert: bool = False
    severity: str = "INFO"
    dedup_key: str = ""
    count: int = 1
    first_seen_mono: float = 0.0
    last_seen_mono: float = 0.0
    coalesced: bool = False

    @property
    def display_message(self) -> str:
        if self.count > 1:
            return f"{self.message} (lặp lại ×{self.count})"
        return self.message


class AuditLogStore:
    def __init__(self, limit=1000, coalesce_window_sec=45.0):
        if limit <= 0:
            raise ValueError("Giới hạn nhật ký phải lớn hơn 0")

        self._entries = deque(maxlen=limit)
        self.coalesce_window_sec = float(coalesce_window_sec)

    def append(self, message, category="Hệ thống", alert=False, severity=None, dedup_key=None, now_mono=None):
        if category not in LOG_CATEGORIES:
            raise ValueError(f"Nhóm nhật ký không hợp lệ: {category}")

        # Một sự kiện được chuẩn hóa thành một đoạn văn bản.
        text = " ".join(str(message).split())

        # Giới hạn cả kích thước từng sự kiện.
        if len(text) > 4096:
            text = text[:4096] + " … [đã rút gọn]"
        if severity not in SEVERITY_LEVEL:
            severity = "CRITICAL" if alert else "INFO"
        now_mono = time.monotonic() if now_mono is None else float(now_mono)
        now_str = datetime.now().strftime("%H:%M:%S")
        key = str(dedup_key or text)
        for existing in reversed(self._entries):
            if now_mono - existing.last_seen_mono > self.coalesce_window_sec:
                break
            if existing.category == category and existing.dedup_key == key:
                existing.count += 1
                existing.timestamp = now_str
                existing.last_seen_mono = now_mono
                existing.message = text
                existing.alert = bool(alert)
                existing.severity = severity
                existing.coalesced = True
                return existing

        entry = AuditEntry(
            timestamp=now_str,
            category=category,
            message=text,
            alert=bool(alert),
            severity=severity,
            dedup_key=key,
            count=1,
            first_seen_mono=now_mono,
            last_seen_mono=now_mono,
            coalesced=False,
        )

        self._entries.append(entry)
        return entry

    def select(self, category="Tất cả"):
        if category == "Tất cả":
            return [
                entry
                for entry in self._entries
                if entry.category != "Syslog thô"
            ]

        if category not in LOG_CATEGORIES:
            raise ValueError(f"Nhóm nhật ký không hợp lệ: {category}")

        return [
            entry
            for entry in self._entries
            if entry.category == category
        ]

    def __len__(self):
        return len(self._entries)
