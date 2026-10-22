"""Photo-only Telegram alerts, delivered outside the Qt thread."""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import json
import queue
import threading
import uuid
from urllib import request, error

VIETNAM = timezone(timedelta(hours=7))


@dataclass(frozen=True)
class CameraAlert:
    camera: str
    location: str
    people: int
    occurred_at: float
    photo: bytes
    qos_mbps: float
    active_router: str

    def caption(self):
        timestamp = datetime.fromtimestamp(self.occurred_at, VIETNAM)
        return (
            f"🚨 {self.camera}\nLocation: {self.location}\n"
            f"People: {self.people}\nState: NORMAL → ALERT\n"
            f"QoS profile: {self.qos_mbps:g} Mbps (requested)\n"
            f"Active router: {self.active_router}\n"
            f"Time: {timestamp:%Y-%m-%d %H:%M:%S} (UTC+07:00)"
        )


def send_photo(token, chat_id, alert, timeout=10):
    """Upload one JPEG with caption; never include the token in errors."""
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in (("chat_id", chat_id), ("caption", alert.caption())):
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; '
                      f'name="{name}"\r\n\r\n{value}\r\n').encode())
    parts.extend([
        (f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; '
         'filename="alert.jpg"\r\nContent-Type: image/jpeg\r\n\r\n').encode(),
        alert.photo, f'\r\n--{boundary}--\r\n'.encode(),
    ])
    req = request.Request(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            result = json.load(response)
        if not result.get("ok"):
            raise RuntimeError("Telegram rejected photo")
    except error.HTTPError as exc:
        # Do not propagate HTTPError: its URL contains the bot token.
        status = exc.code
        exc.close()
        raise RuntimeError(f"Telegram HTTP {status}") from None
    except (error.URLError, OSError, ValueError):
        raise RuntimeError("Telegram connection or response error") from None


class TelegramAlerts:
    """Bounded, best-effort queue. Each accepted event gets at most 4 attempts."""

    def __init__(self, token, chat_id, log=None, sender=send_photo,
                 retry_delays=(1, 2, 4), router_provider=None):
        self.token = token
        self.chat_id = chat_id
        self.log = log or (lambda message, failed: None)
        self.sender = sender
        self.retry_delays = retry_delays
        self.router_provider = router_provider
        self._queue = queue.Queue(maxsize=32)
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    @property
    def enabled(self):
        return bool(self.token and self.chat_id)

    def submit(self, alert):
        if not self.enabled or self._stop.is_set():
            return False
        if not alert.photo:
            self.log(f"[TELEGRAM] [{alert.camera}] Missing alert photo; check camera/pipeline.", True)
            return False
        with self._lock:
            if self._stop.is_set():
                return False
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="telegram-alerts", daemon=True)
                self._thread.start()
            try:
                self._queue.put_nowait(alert)
            except queue.Full:
                self.log("[TELEGRAM] Queue full; alert could not be queued.", True)
                return False
        return True

    def _run(self):
        while not self._stop.is_set():
            try:
                alert = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                if self.router_provider is not None:
                    try:
                        alert = replace(alert, active_router=self.router_provider())
                    except Exception:
                        self.log("[TELEGRAM] VRRP query failed; router is unknown.", True)
                for attempt in range(len(self.retry_delays) + 1):
                    if self._stop.is_set():
                        break
                    try:
                        self.sender(self.token, self.chat_id, alert)
                    except Exception:
                        # Exceptions from transports must never expose credentials.
                        if attempt == len(self.retry_delays):
                            self.log(f"[TELEGRAM] [{alert.camera}] Failed after {attempt + 1} attempts.", True)
                        elif self._stop.wait(self.retry_delays[attempt]):
                            break
                    else:
                        self.log(f"[TELEGRAM] [{alert.camera}] Photo alert sent.", False)
                        break
            finally:
                self._queue.task_done()

    def close(self, timeout=0.2):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
