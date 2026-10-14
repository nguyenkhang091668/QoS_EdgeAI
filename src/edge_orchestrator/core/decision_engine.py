import time
import threading

try:
    from edge_orchestrator.core.config import (
        QOS_DEGRADE_TRIGGER_SEC, QOS_RECOVER_TRIGGER_SEC,
        QOS_THRESHOLD_LOSS, QOS_THRESHOLD_JITTER, QOS_THRESHOLD_BW_MIN
    )
except ImportError:
    # Fallback cho chạy test độc lập
    QOS_DEGRADE_TRIGGER_SEC = 3
    QOS_RECOVER_TRIGGER_SEC = 10
    QOS_THRESHOLD_LOSS = 5.0
    QOS_THRESHOLD_JITTER = 30.0
    QOS_THRESHOLD_BW_MIN = 0.5


class AIDecisionEngine:
    """
    Theo dõi trạng thái AI (phát hiện người).
    Quyết định chuyển trạng thái giữa NORMAL và ALERT.
    Sử dụng cơ chế Debounce để chống nhiễu (Flapping).
    """

    def __init__(self, alert_trigger_sec=2.0, recover_trigger_sec=3.0):
        self.alert_trigger_sec = alert_trigger_sec
        self.recover_trigger_sec = recover_trigger_sec

        self._current_state = "NORMAL"   # "NORMAL" | "ALERT"
        self._last_alert_time = None
        self._last_normal_time = time.monotonic()
        self._lock = threading.Lock()

    @property
    def current_state(self):
        return self._current_state

    def update(self, person_detected: bool, log_callback=None) -> str | None:
        """
        Đánh giá trạng thái phát hiện người. Trả về state mới nếu có sự thay đổi.
        """
        now = time.monotonic()

        with self._lock:
            if person_detected:
                # Đánh dấu thời điểm bắt đầu phát hiện người
                if self._last_alert_time is None:
                    self._last_alert_time = now
                self._last_normal_time = None

                # Đủ thời gian Debounce chưa?
                elapsed = now - self._last_alert_time
                if elapsed >= self.alert_trigger_sec and self._current_state != "ALERT":
                    self._current_state = "ALERT"
                    if log_callback:
                        log_callback(
                            f"[AI ENGINE] Phát hiện người liên tục trong {elapsed:.1f}s! KÍCH HOẠT CẢNH BÁO.",
                            alert=True
                        )
                    return "ALERT"
            else:
                # Không phát hiện người
                self._last_alert_time = None
                if self._last_normal_time is None:
                    self._last_normal_time = now

                elapsed = now - self._last_normal_time
                if elapsed >= self.recover_trigger_sec and self._current_state != "NORMAL":
                    self._current_state = "NORMAL"
                    if log_callback:
                        log_callback(
                            f"[AI ENGINE] Trạng thái an toàn trở lại sau {elapsed:.1f}s. HỦY CẢNH BÁO.",
                            alert=False
                        )
                    return "NORMAL"

        return None


class QoSDecisionEngine:
    """
    Theo dõi luồng QoS Telemetry (Loss, Jitter, Bitrate).
    Quyết định chuyển trạng thái mạng giữa NORMAL và QOS_DEGRADED.
    Sử dụng cơ chế Debounce để chống nhiễu (Flapping).
    """

    def __init__(self, simulate=False):
        self.degrade_trigger_sec = QOS_DEGRADE_TRIGGER_SEC
        self.recover_trigger_sec = QOS_RECOVER_TRIGGER_SEC
        self.simulate = simulate

        self._current_state = "NORMAL"   # "NORMAL" | "QOS_DEGRADED"
        self._last_degraded_time = None
        self._last_normal_time = time.monotonic()
        self._lock = threading.Lock()

    @property
    def current_state(self):
        return self._current_state

    def update(self, telemetry_metrics: list, log_callback=None) -> str | None:
        """
        Đánh giá mảng metrics từ tshark. Trả về state mới nếu có sự thay đổi.
        """
        now = time.monotonic()
        is_degraded = False
        reasons = []

        # 1. Phân tích từng luồng Camera
        for metric in telemetry_metrics:
            cam_id = metric.get("ID", "Unknown")
            loss = metric.get("Loss_Percent", 0.0)
            jitter = metric.get("Jitter_ms", 0.0)
            bw = metric.get("Bitrate_Mbps", 0.0)

            # Logic phát hiện suy thoái
            if loss > QOS_THRESHOLD_LOSS:
                is_degraded = True
                reasons.append(f"{cam_id} Loss={loss}%")
            if jitter > QOS_THRESHOLD_JITTER:
                is_degraded = True
                reasons.append(f"{cam_id} Jitter={jitter}ms")
            if bw > 0 and bw < QOS_THRESHOLD_BW_MIN:
                is_degraded = True
                reasons.append(f"{cam_id} BW={bw}Mbps")

        # 2. Xử lý State Machine với Debounce
        with self._lock:
            if is_degraded:
                if self._last_degraded_time is None:
                    self._last_degraded_time = now
                self._last_normal_time = None

                elapsed = now - self._last_degraded_time
                if elapsed >= self.degrade_trigger_sec and self._current_state != "QOS_DEGRADED":
                    self._current_state = "QOS_DEGRADED"
                    reason_str = ", ".join(reasons)
                    if log_callback:
                        log_callback(
                            f"[QOS ENGINE]  Cảnh báo QoS Suy thoái! Lý do: {reason_str}. Cảnh báo chất lượng luồng.",
                            alert=True
                        )
                    return "QOS_DEGRADED"
            else:
                self._last_degraded_time = None
                if self._last_normal_time is None:
                    self._last_normal_time = now

                elapsed = now - self._last_normal_time
                if elapsed >= self.recover_trigger_sec and self._current_state != "NORMAL":
                    self._current_state = "NORMAL"
                    if log_callback:
                        log_callback(
                            f"[QOS ENGINE] Mạng đã ổn định trở lại sau {elapsed:.1f}s. Chất lượng luồng đã phục hồi.",
                            alert=False
                        )
                    return "NORMAL"

        return None


if __name__ == "__main__":
    # Khởi tạo hai Decision Engine độc lập
    ai_engine = AIDecisionEngine(
        alert_trigger_sec=2.0, recover_trigger_sec=3.0)
    qos_engine = QoSDecisionEngine(simulate=True)

    def my_logger(msg, alert):
        prefix = "red" if alert else "green"
        print(f"{prefix} {msg}")

    print("--- Test 1: Trạng thái Mặc định / Mạng Tốt & Không có AI ---")
    good_metrics = [{"ID": "CAM01", "Loss_Percent": 0.0,
                     "Jitter_ms": 2.0, "Bitrate_Mbps": 1.2}]

    qos_engine.update(good_metrics, my_logger)
    ai_engine.update(person_detected=False, log_callback=my_logger)

    print("\n--- Test 2: Mạng Bắt đầu Suy thoái (Loss = 10%) & Phát hiện Người ---")
    bad_metrics = [{"ID": "CAM01", "Loss_Percent": 10.0,
                    "Jitter_ms": 5.0, "Bitrate_Mbps": 0.8}]

    for i in range(4):
        print(f"Giây {i+1}...")
        qos_engine.update(bad_metrics, my_logger)
        ai_engine.update(person_detected=True, log_callback=my_logger)
        time.sleep(1)

    print("\n--- Test 3: Mạng Phục hồi & Hết phát hiện Người ---")
    for i in range(11):
        if i == 0 or i == 10:
            print(f"Giây {i+1}...")
        qos_engine.update(good_metrics, my_logger)
        ai_engine.update(person_detected=False, log_callback=my_logger)
        time.sleep(1)
