from collections import deque
from dataclasses import dataclass
from datetime import datetime


LOG_CATEGORIES = (
    "Edge AI",
    "Dynamic QoS",
    "Security/ACL",
    "Collector",
    "Cisco",
    "Syslog thô",
    "Hệ thống",
)


@dataclass(frozen=True)
class AuditEntry:
    timestamp: str
    category: str
    message: str
    alert: bool = False


class AuditLogStore:
    def __init__(self, limit=1000):
        if limit <= 0:
            raise ValueError("Giới hạn nhật ký phải lớn hơn 0")

        self._entries = deque(maxlen=limit)

    def append(self, message, category="Hệ thống", alert=False):
        if category not in LOG_CATEGORIES:
            raise ValueError(f"Nhóm nhật ký không hợp lệ: {category}")

        # Một sự kiện được chuẩn hóa thành một đoạn văn bản.
        text = " ".join(str(message).split())

        # Giới hạn cả kích thước từng sự kiện.
        if len(text) > 4096:
            text = text[:4096] + " … [đã rút gọn]"

        entry = AuditEntry(
            timestamp=datetime.now().strftime("%H:%M:%S"),
            category=category,
            message=text,
            alert=bool(alert),
        )

        self._entries.append(entry)
        return entry

    def select(self, category="Tất cả"):
        if category == "Tất cả":
            return list(self._entries)

        if category not in LOG_CATEGORIES:
            raise ValueError(f"Nhóm nhật ký không hợp lệ: {category}")

        return [
            entry
            for entry in self._entries
            if entry.category == category
        ]

    def __len__(self):
        return len(self._entries)