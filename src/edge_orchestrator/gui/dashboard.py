from pathlib import Path
from datetime import datetime
import queue
import threading
import random
import time
import sys
import numpy as np
import matplotlib
matplotlib.use("Qt5Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from edge_orchestrator.network.syslog_receiver import (
    syslog_event_queue,
)
from edge_orchestrator.network.snmp_bandwidth_worker import (
    CiscoBandwidthWorker,
)
from edge_orchestrator.network.snmp_bandwidth import (
    CiscoBandwidthCollector,
)
from edge_orchestrator.network.qos_telemetry import (
    QoSTelemetryWorker,
)
from edge_orchestrator.network.pysnmp_client import (
    PySnmpV3Client,
)
from edge_orchestrator.network.network_controller import (
    NetworkController,
)
from edge_orchestrator.network.bandwidth_types import (
    BandwidthSample,
    CollectorStatus,
)
from edge_orchestrator.network.bandwidth_store import (
    BandwidthStore,
)
from edge_orchestrator.gui.ai_camera_worker import (
    AICameraWorker,
)
from edge_orchestrator.network.local_camera_bandwidth import (
    LocalCameraBandwidthWorker
)
from edge_orchestrator.core.event import EventType
from edge_orchestrator.core.decision_engine import (
    QoSDecisionEngine,
    AIDecisionEngine,
)
from edge_orchestrator.core import config
from PyQt5.QtGui import (
    QFont,
    QImage,
    QPixmap,
    QColor,
    QPalette,
)
from PyQt5.QtCore import (
    QTimer,
    Qt,
    pyqtSignal,
    QObject,
)
from PyQt5.QtWidgets import (
    QWidget,
    QGridLayout,
    QLabel,
    QTextEdit,
    QGroupBox,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
)

try:
    import cv2

    CV2_OK = True

except ImportError:
    CV2_OK = False

BANDWIDTH_SOURCE_IDS = (
    *config.SNMP_DEVICES.keys(),
    *config.RTP_STREAMS.keys(),
    *config.LOCAL_CAMERAS.keys(),
)
BANDWIDTH_LINE_STYLES = {
    "R1": {
        "color": "#00e676",
        "linestyle": "-",
    },
    "R2": {
        "color": "#76ff03",
        "linestyle": "--",
    },
    "DSW1": {
        "color": "#40c4ff",
        "linestyle": "-",
    },
    "DSW2": {
        "color": "#448aff",
        "linestyle": "--",
    },
    "ASW": {
        "color": "#e040fb",
        "linestyle": "-.",
    },
    "CAM01": {
        "color": "#ffca28",
        "linestyle": "-",
    },
    "CAM02": {
        "color": "#ff7043",
        "linestyle": "--",
    },
    "CAM03": {
        "color": "#f06292",
        "linestyle": "-.",
    },
    "CAM04": {
        "color": "#9575cd",
        "linestyle": "--",
    },
}

# Import từ các module nội bộ của package

# ── SIGNAL BRIDGE (thread-safe GUI update) ───────────────────────────────────


class SignalBridge(QObject):
    log_signal = pyqtSignal(str, bool)    # (text, is_alert)
    qos_signal = pyqtSignal(str)          # QoS mode: "alert" / "normal"
    cam_signal = pyqtSignal(bool)         # is_alert


bridge = SignalBridge()

# ── MAIN DASHBOARD WINDOW ─────────────────────────────────────────────────────


class MainDashboard(QWidget):
    def __init__(self, simulate_ai=True, simulate_netmiko=True, simulate_syslog=True):
        super().__init__()
        self.simulate_ai = simulate_ai
        self.simulate_netmiko = simulate_netmiko
        self.simulate_syslog = simulate_syslog

        self.setWindowTitle(
            "TRẠM ĐIỀU PHỐI BIÊN — MacBook [Thay thế Pi 4] — TDTU ELV AGENT | Nguyễn Tiết Khang 41900966"
        )
        try:
            W = config.DASHBOARD_WIDTH
            H = config.DASHBOARD_HEIGHT
        except Exception:
            W, H = 1300, 700

        self.setGeometry(0, 0, W, H)
        self.setStyleSheet("background-color: #0d1117; color: #ffffff;")
        self.bandwidth_store = BandwidthStore(
            source_ids=BANDWIDTH_SOURCE_IDS,
            history_sec=config.BANDWIDTH_HISTORY_SEC,
            stale_after_sec=config.BANDWIDTH_STALE_AFTER_SEC,
        )

        self.snmp_bandwidth_worker = None
        self._telemetry_state_cache = {}
        self._bandwidth_status_cache = {}
        self.bandwidth_lines = {}
        self._bandwidth_legend_signature = None
        self.qos_engine = QoSDecisionEngine(
            simulate=config.SIMULATE_TELEMETRY)
        self.ai_engine = AIDecisionEngine(
            alert_trigger_sec=getattr(config, "ALERT_TRIGGER_SEC", 3),
            recover_trigger_sec=getattr(config, "RECOVER_TRIGGER_SEC", 10),
        )
        self._ai_state = "NORMAL"
        self._ai_available = False
        self._network_state = "NORMAL"
        self._applied_policy = "NORMAL"
        self._qos_mode = "NORMAL"
        self._detect_count = 0
        self._frame_num = 0
        self.telemetry_data = {}
        self.network_controller = None
        self._network_controller_error = None
        if not self.simulate_netmiko:
            try:
                self.network_controller = NetworkController(
                    target_device="R1"
                )
            except Exception as error:
                self._network_controller_error = str(error)

        self._latest_person_detected = False
        self._latest_ai_observation_at = None
        self._latest_ai_event = None
        self.pi_camera_bandwidth_worker = None
        self._local_camera_status_cache = {}
        self.ai_camera_worker = None

        self._init_ui()
        self._connect_signals()
        self._start_timers()

        if not self.simulate_netmiko:
            self._start_snmp_bandwidth_worker()
        else:
            self.log_console(
                "SIMULATE MODE: Netmiko → Disabled"
            )
        if not self.simulate_ai:
            self._start_pi_camera_bandwidth_worker()
            self._start_ai_camera_worker()
        else:
            self.log_console(
                "SIMULATE MODE: AI → Disabled"
            )

    def _init_ui(self):
        main_layout = QGridLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # ── KHUNG 1: Đồ thị Băng thông ────────────────────────────────────────
        g1 = QGroupBox(
            " KHUNG 1: BĂNG THÔNG THỜI GIAN THỰC (Mbps) — CÁC THIẾT BỊ TRONG TRẠM")
        g1.setFont(QFont("Arial", 9, QFont.Bold))
        g1.setStyleSheet("QGroupBox { color: #00ffcc; border: 1.5px solid #00ffcc; "
                         "margin-top: 8px; padding: 4px; border-radius: 4px; }")

        lay1 = QVBoxLayout(g1)
        self.fig, self.ax = plt.subplots(figsize=(5, 2.8), facecolor="#0d1117")
        self.ax.set_facecolor("#111827")
        self.ax.tick_params(colors="#aaaaaa", labelsize=8)
        self.ax.set_xlabel("Thời gian (giây)", color="#aaaaaa", fontsize=8)
        self.ax.set_ylabel("Mbps", color="#aaaaaa", fontsize=8)
        for spine in self.ax.spines.values():
            spine.set_edgecolor("#333333")
        for source_id in BANDWIDTH_SOURCE_IDS:
            style = BANDWIDTH_LINE_STYLES[source_id]
            line, = self.ax.plot([],[],color=style["color"],linestyle=style["linestyle"],linewidth=1.4,
                label=(f"{source_id} [{CollectorStatus.WARMING_UP.value}]"),)
            self.bandwidth_lines[source_id] = line
        self.ax.axhline(y=8.0,color="#ff5252",linestyle=":",linewidth=1,label="Ngưỡng QoS 8 Mbps")
        self.ax.set_xlim(-config.BANDWIDTH_HISTORY_SEC,0)
        self.ax.set_ylim(0, 10)
        self.ax.legend(loc="upper left",fontsize=6,ncol=2,facecolor="#1a1a2e",labelcolor="white",framealpha=0.8,)
        self.canvas = FigureCanvas(self.fig)
        lay1.addWidget(self.canvas)

        # ── KHUNG 2: Camera + AI Status ───────────────────────────────────────
        g2 = QGroupBox(
            " KHUNG 2: LUỒNG CAMERA & TRẠNG THÁI EDGE AI (CAM01 / CAM02)")
        g2.setFont(QFont("Arial", 9, QFont.Bold))
        g2.setStyleSheet("QGroupBox { color: #40c4ff; border: 1.5px solid #40c4ff; "
                         "margin-top: 8px; padding: 4px; border-radius: 4px; }")

        lay2 = QVBoxLayout(g2)
        self.lbl_camera = QLabel("Đang tải luồng RTSP từ CAM01...")
        self.lbl_camera.setAlignment(Qt.AlignCenter)
        self.lbl_camera.setStyleSheet("background-color: #000000; border: 1px solid #223344; "
                                      "border-radius: 3px; min-height: 160px;")
        self.lbl_status = QLabel("  Trạng thái AI: IDLE NORMAL (1 Mbps)")
        self.lbl_status.setFont(QFont("Consolas", 11, QFont.Bold))
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setStyleSheet("color: #00e676; padding: 6px;")
        self.lbl_status.setText("Edge AI unavailable")
        self.lbl_status.setStyleSheet("""background-color: #000000; border: 1px solid #223344;
                                     border-radius: 3px; min-height: 160px;""")
        # Thống kê nhỏ
        stats_row = QHBoxLayout()
        self.lbl_detect_count = QLabel("Phát hiện: 0 lần")
        self.lbl_mode_badge = QLabel("  NORMAL  ")
        self.lbl_mode_badge.setStyleSheet(
            "background: #1b5e20; color: #00e676; font-weight: bold; "
            "border-radius: 3px; padding: 2px 6px;"
        )
        self.lbl_detect_count.setStyleSheet("color: #aaaaaa; font-size: 9px;")
        stats_row.addWidget(self.lbl_detect_count)
        stats_row.addStretch()
        stats_row.addWidget(self.lbl_mode_badge)

        lay2.addWidget(self.lbl_camera, stretch=3)
        lay2.addWidget(self.lbl_status)
        lay2.addLayout(stats_row)

        # ── KHUNG 3: Bảng Log An ninh & QoS ──────────────────────────────────
        g3 = QGroupBox(
            " KHUNG 3: BẢO MẬT SYSLOG & CẢnh BÁO DYNAMIC QOS — Security & Congestion Log")
        g3.setFont(QFont("Arial", 9, QFont.Bold))
        g3.setStyleSheet("QGroupBox { color: #ff5252; border: 1.5px solid #ff5252; "
                         "margin-top: 8px; padding: 4px; border-radius: 4px; }")

        lay3 = QVBoxLayout(g3)
        self.txt_console = QTextEdit()
        self.txt_console.setReadOnly(True)
        self.txt_console.setFont(QFont("Menlo", 9))
        self.txt_console.setStyleSheet("background-color: #0a0a0f; color: #00e676; "
                                       "border: none; padding: 4px;")

        # Nút điều khiển thủ công
        btn_row = QHBoxLayout()
        btn_alert = QPushButton(" Nạp QoS ALERT (8M)")
        btn_normal = QPushButton(" Nạp QoS NORMAL (1M)")
        btn_acl = QPushButton(" Test ACL Remediation")
        for btn in [btn_alert, btn_normal, btn_acl]:
            btn.setFont(QFont("Arial", 8))
            btn.setStyleSheet("QPushButton { background: #1a1a2e; color: #ffffff; "
                              "border: 1px solid #444; border-radius: 3px; padding: 4px 8px; } "
                              "QPushButton:hover { background: #2a2a4e; }")
        btn_alert.clicked.connect(lambda: self._manual_qos("alert"))
        btn_normal.clicked.connect(lambda: self._manual_qos("normal"))
        btn_acl.clicked.connect(lambda: self._manual_acl())
        btn_row.addWidget(btn_alert)
        btn_row.addWidget(btn_normal)
        btn_row.addWidget(btn_acl)

        lay3.addWidget(self.txt_console, stretch=1)
        lay3.addLayout(btn_row)

        # ── Bố trí Grid ───────────────────────────────────────────────────────
        main_layout.addWidget(g1, 0, 0, 1, 1)
        main_layout.addWidget(g2, 0, 1, 1, 1)
        main_layout.addWidget(g3, 1, 0, 1, 2)
        main_layout.setRowStretch(0, 3)
        main_layout.setRowStretch(1, 2)

    def _connect_signals(self):
        bridge.log_signal.connect(self.log_console)
        bridge.qos_signal.connect(self._apply_network_policy)
        bridge.cam_signal.connect(self._update_cam_frame)

    def _start_timers(self):
        self.timer_bw = QTimer()
        self.timer_bw.timeout.connect(self._tick_bandwidth)
        self.timer_ai = QTimer()
        self.timer_ai.timeout.connect(self._tick_ai)
        self.timer_sys = QTimer()
        self.timer_sys.timeout.connect(self._tick_syslog)
        self.timer_bw.start(1000)   # Băng thông mỗi 1s
        self.timer_ai.start(2000)   # AI check mỗi 2s
        self.timer_sys.start(500)   # Syslog check mỗi 0.5s

        # Boot log
        self.log_console(
            "[SYSTEM] ✓ MacBook Edge Orchestrator khởi động (thay thế Pi 4)")
        self.log_console(f"[SYSTEM] Chế độ: AI={'Mô phỏng' if self.simulate_ai else 'Thật'} | "
                         f"SSH={'Mô phỏng' if self.simulate_netmiko else 'Thật'} | "
                         f"Syslog={'Mô phỏng' if self.simulate_syslog else 'Thật'}")
        self.log_console(
            "[NETMIKO] Đang kiểm tra kết nối SSH tới R1 (10.255.255.1)...")
        self.log_console(
            "[TAILSCALE] VPN Overlay đang hoạt động — MacBook đã gia nhập Tailscale subnet")

        # Khởi chạy QoS Telemetry Worker (Tshark)
        self.telemetry_worker = QoSTelemetryWorker(
            simulate=config.SIMULATE_TELEMETRY)
        self.telemetry_worker.telemetry_signal.connect(self._update_telemetry)
        self.telemetry_worker.collector_error_signal.connect(self._on_telemetry_collector_error)
        self.telemetry_worker.start()
        sim_mode = "Mô phỏng" if config.SIMULATE_TELEMETRY else "Thực tế (tshark)"
        self.log_console(
            f"[TELEMETRY]  Đang lắng nghe RTP trên utun4 (Chế độ: {sim_mode})")

    def _start_pi_camera_bandwidth_worker(self,):
        camera_config = config.LOCAL_CAMERAS["CAM03"]
        try:
            self.pi_camera_bandwidth_worker = (
                LocalCameraBandwidthWorker(
                    source_id="CAM03",
                    source=camera_config["source"],
                    device=camera_config.get("device", 0),
                    interval_sec=(config.BANDWIDTH_POLL_INTERVAL_SEC),
                    jpeg_quality=camera_config.get("jpeg_quality", 80),
                )
            )

            (
                self.pi_camera_bandwidth_worker
                .bandwidth_signal.connect(self._on_local_camera_bandwidth)
            )
            self.pi_camera_bandwidth_worker.error_signal.connect(self._on_local_camera_bandwidth_error)
            self.pi_camera_bandwidth_worker.start()
            self.log_console(
                "[CAM03] Đã khởi động "
                "Pi Camera bandwidth collector."
            )
        except Exception as error:
            self._on_local_camera_bandwidth_error(
                "CAM03",
                str(error),
            )
    def _on_local_camera_bandwidth(
        self,
        source_id,
        value_mbps,
        sampled_at,
    ):
        sample = BandwidthSample(
            source_id=source_id,
            source_type="local_encoded",
            status=CollectorStatus.STREAMING,
            sampled_at=sampled_at,
            value_mbps=value_mbps,
            rx_mbps=None,
            tx_mbps=value_mbps,
            details={
                "codec": "JPEG",
                "measurement":
                    "encoded_pipeline_output",
            },
        )

        try:
            accepted = self.bandwidth_store.update(
                sample
            )

        except (KeyError, TypeError) as error:
            self.log_console(
                f"[LOCAL BANDWIDTH] "
                f"{source_id}: {error}",
                alert=True,
            )
            return

        if not accepted:
            return

        previous_status = (
            self._local_camera_status_cache.get(
                source_id
            )
        )

        self._local_camera_status_cache[
            source_id
        ] = CollectorStatus.STREAMING

        if (
            previous_status
            != CollectorStatus.STREAMING
        ):
            self.log_console(
                f"[LOCAL BANDWIDTH] "
                f"{source_id}: STREAMING"
            )

    def _on_local_camera_bandwidth_error(
        self,
        source_id,
        message,
    ):
        sample = BandwidthSample.unavailable(
            source_id=source_id,
            source_type="local_encoded",
            status=(
                CollectorStatus.COLLECTOR_ERROR
            ),
            sampled_at=time.time(),
            details={
                "codec": "JPEG",
                "measurement":
                    "encoded_pipeline_output",
            },
            error=message,
        )

        try:
            self.bandwidth_store.update(
                sample
            )

        except (KeyError, TypeError):
            return

        previous_status = (
            self._local_camera_status_cache.get(
                source_id
            )
        )

        self._local_camera_status_cache[
            source_id
        ] = CollectorStatus.COLLECTOR_ERROR

        if (
            previous_status
            != CollectorStatus.COLLECTOR_ERROR
        ):
            self.log_console(
                f"[LOCAL CAMERA ERROR] "
                f"{source_id}: {message}",
                alert=True,
            )
    def _start_snmp_bandwidth_worker(self):
        try:
            snmp_client = PySnmpV3Client(
                username=config.SNMP_USERNAME,
                auth_key=config.SNMP_AUTH_KEY,
                priv_key=config.SNMP_PRIV_KEY,
                port=config.SNMP_PORT,
                timeout_sec=config.SNMP_TIMEOUT_SEC,
                retries=config.SNMP_RETRIES,
            )
            collector = CiscoBandwidthCollector(
                devices=config.SNMP_DEVICES,
                snmp_client=snmp_client,
                offline_failures=(config.BANDWIDTH_OFFLINE_FAILURES),
                rate_window_sec=(config.BANDWIDTH_RATE_WINDOW_SEC)
            )
            self.snmp_bandwidth_worker = (
                CiscoBandwidthWorker(
                    collector=collector,
                    interval_sec=(
                        config.BANDWIDTH_POLL_INTERVAL_SEC
                    ),
                    max_workers=len(
                        config.SNMP_DEVICES
                    ),
                )
            )
            self.snmp_bandwidth_worker.samples_signal.connect(
                self._on_snmp_bandwidth_samples
            )
            self.snmp_bandwidth_worker.worker_error_signal.connect(
                self._on_snmp_bandwidth_error
            )
            self.snmp_bandwidth_worker.start()
            self.log_console(
                f"[SNMP] Đã khởi động băng thông Cisco: {len(config.SNMP_DEVICES)} thiết bị",
            )
        except Exception as e:
            self.log_console(f"[SNMP] Lỗi khởi động: {e}", alert=True)

    def _on_snmp_bandwidth_samples(self, samples):
        for sample in samples:
            try:
                accepted = self.bandwidth_store.update(
                    sample
                )
            except Exception as error:
                self.log_console(
                    f"[SNMP] Lỗi cập nhật mẫu: {error}", alert=True)
                continue
            if not accepted:
                continue
            current_status = sample.status
            previous_status = (
                self._bandwidth_status_cache.get(
                    sample.source_id
                )
            )
            self._bandwidth_status_cache[sample.source_id] = current_status
            if current_status == previous_status:
                continue
            is_error = current_status in {
                CollectorStatus.OFFLINE,
                CollectorStatus.INTERFACE_DOWN,
                CollectorStatus.COLLECTOR_ERROR,
                CollectorStatus.STALE,
            }
            message = (
                f"[SNMP BANDWIDTH] "
                f"{sample.source_id}: "
                f"{current_status.value}"
            )
            if sample.error:
                message += f" lỗi: {sample.error}"
            self.log_console(message, alert=is_error)

    def _on_snmp_bandwidth_error(self, message):
        self.log_console(
            f"[SNMP WORKER ERROR] {message}",
            alert=True,
        )
    # ── TICK FUNCTIONS ────────────────────────────────────────────────────────

    def _tick_bandwidth(self):
        """Vẽ lịch sử bandwidth thật từ BandwidthStore."""
        now = time.time()
        snapshot = self.bandwidth_store.snapshot(now=now)
        status_signature = []
        maximum_value = 0.0
        valid_statuses = {CollectorStatus.ONLINE, CollectorStatus.STREAMING,}
        for source_id in BANDWIDTH_SOURCE_IDS:
            source_data = snapshot[source_id]
            status = source_data["status"]
            history = source_data["history"]
            x_values = [sampled_at - now for sampled_at, _ in history]
            y_values = [value for _, value in history]
            # Ngắt đường tại thời điểm hiện tại nếu nguồn không còn dữ liệu hợp lệ.
            if status not in valid_statuses:
                x_values.append(0.0)
                y_values.append(np.nan)
            finite_values = [value for value in y_values if np.isfinite(value)]
            if finite_values:
                maximum_value = max(maximum_value, max(finite_values))
            line = self.bandwidth_lines[source_id]
            line.set_data(x_values, y_values)
            line.set_label(f"{source_id} [{status.value}]")
            status_signature.append((source_id, status.value))
        self.ax.set_xlim(-config.BANDWIDTH_HISTORY_SEC, 0)
        self.ax.set_ylim(0, max(10.0, maximum_value * 1.2))
        status_signature = tuple(status_signature)
        if (status_signature!= self._bandwidth_legend_signature):
            self._bandwidth_legend_signature = (status_signature)
            self.ax.legend(
                loc="upper left",
                fontsize=6,
                ncol=2,
                facecolor="#1a1a2e",
                labelcolor="white",
                framealpha=0.8,
            )
        self.canvas.draw_idle()

    def _tick_ai(self):
        if self.simulate_ai:
            probability = getattr(
                config,
                "AI_PERSON_DETECT_PROB",
                0.25,
            )
            person_detected = random.random() < probability
            self._update_cam_frame(person_detected)
        else:
            if not self._ai_available:
                return
            person_detected = (
                self._get_fresh_person_state()
            )
        ai_state_change = self.ai_engine.update(person_detected,log_callback=lambda text, alert=False: self.log_console(text, alert),)
        if ai_state_change is not None:
            self._on_ai_state_change(ai_state_change)
    def _on_ai_state_change(self, state: str):
        self._ai_state = state

        if state == "ALERT":
            self._detect_count += 1
            self.lbl_status.setText(
                f" Trạng thái AI: PERSON DETECTED → ALERT ({self._detect_count} lần)"
            )
            self.lbl_status.setStyleSheet(
                "color: #ff5252; padding: 6px;"
            )
            self.lbl_mode_badge.setText("  ALERT  ")
            self.lbl_mode_badge.setStyleSheet(
                "background: #7f0000; color: #ff5252; "
                "font-weight: bold; border-radius: 3px; "
                "padding: 2px 6px;"
            )

        elif state == "NORMAL":
            self.lbl_status.setText(
                " Trạng thái AI: IDLE NORMAL"
            )
            self.lbl_status.setStyleSheet(
                "color: #00e676; padding: 6px;"
            )
            self.lbl_mode_badge.setText("  NORMAL  ")
            self.lbl_mode_badge.setStyleSheet(
                "background: #1b5e20; color: #00e676; "
                "font-weight: bold; border-radius: 3px; "
                "padding: 2px 6px;"
            )

        else:
            self.log_console(
                f"[AI ENGINE] Trạng thái không hợp lệ: {state}",
                alert=True,
            )
            return

        self._recalculate_target_policy()

    def _tick_syslog(self):
        try:
            while True:
                event = syslog_event_queue.get_nowait()

                event_type = event.get(
                    "type",
                    EventType.UNKNOWN_SYS_LOG,)
                data = event.get("data", {})
                timestamp = event.get(
                    "timestamp",
                    "??:??:??",
                )

                if event_type == EventType.PORT_SECURITY_VIOLATION:
                    mac = data.get("mac")
                    interface = data.get("interface", "?")

                    self.log_console(
                        f"[{timestamp}] PORT SECURITY VIOLATION | "
                        f"MAC: {mac or '?'} | "
                        f"Port: {interface}",
                        alert=True,
                    )

                elif event_type == EventType.DAI_ARP_SPOOFING_DENY:
                    attacker_ip = data.get("src_ip")
                    attacker_mac = data.get("src_mac")
                    interface = data.get("interface", "?")

                    self.log_console(
                        f"[{timestamp}] DAI ARP SPOOFING | "
                        f"IP: {attacker_ip or '?'} | "
                        f"MAC: {attacker_mac or '?'} | "
                        f"Port: {interface}",
                        alert=True,
                    )

                    if attacker_ip:
                        self._run_acl_remediation(
                            attacker_ip,
                            attacker_mac,
                        )
                    else:
                        self.log_console(
                            "[ACL] Không remediation vì event "
                            "không có src_ip.",
                            alert=True,
                        )

                elif event_type == EventType.INTERFACE_LINK_CHANGE:
                    state = data.get(
                        "state",
                        "unknown",
                    ).upper()

                    interface = data.get(
                        "interface",
                        "?",
                    )

                    self.log_console(
                        f"[{timestamp}] Interface "
                        f"{interface}: {state}",
                        alert=(state == "DOWN"),
                    )

                else:
                    raw = event.get("raw", "")
                    details = event.get("details", raw)

                    self.log_console(
                        f"[{timestamp}] "
                        f"{event_type}: {details[:120]}"
                    )

        except queue.Empty:
            pass

    def _manual_acl(self):
        test_ip = "192.168.10.99"

        self.log_console(
            f"[MANUAL ACL] Kích hoạt thủ công ACL chặn IP: {test_ip}",
            alert=True,
        )
        self._run_acl_remediation(
            test_ip,
            "",
        )

    def _on_telemetry_collector_error(self, message,):
        sampled_at = time.time()
        should_log = False
        for source_id, stream_config in (config.RTP_STREAMS.items()):
            previous_state = (self._telemetry_state_cache.get(source_id))
            self._telemetry_state_cache[source_id] = "COLLECTOR_ERROR"
            if previous_state != "COLLECTOR_ERROR":
                should_log = True
            sample = BandwidthSample.unavailable(
                source_id=source_id,
                source_type="rtp",
                status=CollectorStatus.COLLECTOR_ERROR,
                sampled_at=sampled_at,
                details={
                    "destination_ip": stream_config["destination_ip"],
                    "destination_port": stream_config["destination_port"],
                },
                error=message,
            )
            self.bandwidth_store.update(sample)
        if should_log:
            self.log_console(
                f"[RTP COLLECTOR ERROR] {message}",
                alert=True,
            )
    def _update_telemetry(self, metrics):
        """Cập nhật telemetry RTP và chỉ log khi trạng thái thay đổi."""
        valid_qos_metrics = []

        for metric in metrics:
            camera_id = str(metric["ID"])
            port = str(metric["Port"])
            bitrate = float(metric["Bitrate_Mbps"])
            loss = float(metric["Loss_Percent"])
            jitter = float(metric["Jitter_ms"])

            self.telemetry_data[port] = {
                "bitrate": bitrate,
                "loss": loss,
                "jitter": jitter,
            }
            if bitrate <= 0:
                telemetry_state = "NO_STREAM"
            elif (loss > config.QOS_THRESHOLD_LOSS or jitter > config.QOS_THRESHOLD_JITTER):
                telemetry_state = "DEGRADED"
            else:
                telemetry_state = "STREAMING"

            previous_state = self._telemetry_state_cache.get(camera_id)
            self._telemetry_state_cache[camera_id] = telemetry_state

            # Chỉ ghi log khi trạng thái thay đổi.
            if telemetry_state != previous_state:
                if telemetry_state == "NO_STREAM":
                    self.log_console(
                        f"[TELEMETRY] {camera_id} "
                        f"(UDP/{port}): NO_STREAM",
                        alert=True,
                    )

                elif telemetry_state == "DEGRADED":
                    self.log_console(
                        f"[TELEMETRY] {camera_id} "
                        f"(UDP/{port}): DEGRADED | "
                        f"Bitrate={bitrate:.3f} Mbps | "
                        f"Loss={loss:.2f}% | "
                        f"Jitter={jitter:.2f} ms",
                        alert=True,
                    )

                else:
                    self.log_console(
                        f"[TELEMETRY] {camera_id} "
                        f"(UDP/{port}): STREAMING | "
                        f"Bitrate={bitrate:.3f} Mbps"
                    )

            # NO_STREAM là trạng thái nguồn không có luồng,
            # không được xem là packet loss để kích hoạt QoS.
            if telemetry_state != "NO_STREAM":
                valid_qos_metrics.append(metric)

            if bitrate > 0:
                collector_status = CollectorStatus.STREAMING
                graph_value = bitrate
            else:
                collector_status = CollectorStatus.NO_STREAM
                graph_value = None
            bandwidth_sample = BandwidthSample(
                source_id=camera_id,
                source_type="rtp",
                status=collector_status,
                sampled_at=time.time(),
                value_mbps=graph_value,
                rx_mbps=graph_value,
                details={
                    "destination_port": int(port),
                    "loss_percent": loss,
                    "jitter_ms": jitter,
                    "quality_state": telemetry_state,
                },
            )
            try:
                self.bandwidth_store.update(
                    bandwidth_sample
                )
            except (KeyError, TypeError) as error:
                self.log_console(
                    f"[RTP BANDWIDTH] "
                    f"{camera_id}: {error}",
                    alert=True,
                )
        network_state_change = self.qos_engine.update(
            valid_qos_metrics,
            log_callback=(
                lambda text, alert=False:
                self.log_console(text, alert)
            ),
        )

        if network_state_change is not None:
            self._on_network_state_change(
                network_state_change
            )

    def _recalculate_target_policy(self):
        if self._ai_state == "ALERT":
            target_policy = "ALERT"
        elif self._network_state == "QOS_DEGRADED":
            target_policy = "ALERT"
        else:
            target_policy = "NORMAL"

        if target_policy == self._applied_policy:
            return
        success = self._apply_network_policy(target_policy)
        if success:
            self._applied_policy = target_policy

    def _on_network_state_change(self, state: str):
        self._network_state = state
        if state == "QOS_DEGRADED":
            self.log_console(
                "[NETWORK]  Mạng suy thoái — yêu cầu remediation.",
                alert=True,
            )
        elif state == "NORMAL":
            self.log_console(
                "[NETWORK]  Chất lượng mạng đã phục hồi.",
                alert=False,
            )
        self._recalculate_target_policy()

    def _apply_network_policy(self, policy: str):
        if self.simulate_netmiko:
            self.log_console(
                f"[NETMiko SIMULATION] Áp dụng policy {policy}"
            )
            return True
        if self.network_controller is None:
            self.log_console(
                "[NETMIKO] NetworkController chưa được khởi tạo.",
                alert=True,
            )
            return False
        if policy == "ALERT":
            self.network_controller.apply_qos_remediation()
            return True
        elif policy == "NORMAL":
            self.network_controller.revert_qos_remediation()
            return True
        else:
            self.log_console(
                f"[NETMIKO] Policy không hợp lệ: {policy}",
                alert=True,
            )
            return False

    def _manual_qos(self, mode: str):
        policy = mode.upper()
        self.log_console(
            f"[MANUAL] Kích hoạt policy {policy}",
            alert=(policy == "ALERT"),
        )

        self._apply_network_policy(policy)

    def _run_acl_remediation(
        self,
        attacker_ip: str,
        attacker_mac: str | None = None,
    ):
        if self.simulate_netmiko:
            self.log_console(
                f"[ACL SIMULATION] Cô lập {attacker_ip}",
                alert=True,
            )
            return

        if self.network_controller is None:
            self.log_console(
                "[ACL] NetworkController chưa được khởi tạo.",
                alert=True,
            )
            return

        self.network_controller.isolate_attacker(
            attacker_ip,
            attacker_mac,
        )
    # ── CAMERA FRAME RENDERER ─────────────────────────────────────────────────

    def _update_cam_frame(self, is_alert: bool):
        """Render camera frame giả lập lên Khung 2."""
        self._frame_num += 1
        w, h = 480, 200

        if CV2_OK:
            img = np.zeros((h, w, 3), dtype=np.uint8)
            # Background gradient
            for i in range(h):
                val = int(20 + (i / h) * 15)
                img[i, :] = [val, val, val + 10]

            ts_text = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            cv2.putText(img, f"CAM01 LIVE — {ts_text}", (8, 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
            cv2.putText(img, f"Frame #{self._frame_num} | Tailscale Overlay | RTSP :8554",
                        (8, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (100, 150, 200), 1)

            if is_alert:
                # Bounding box người
                cv2.rectangle(img, (150, 50), (300, 180), (0, 0, 255), 2)
                cv2.putText(img, "PERSON 94%", (153, 46),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 80, 255), 2)
                cv2.putText(img, "⚠ ALERT — QoS 8 Mbps DSCP AF41",
                            (8, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 100, 255), 1)
                # Flash border đỏ
                cv2.rectangle(img, (0, 0), (w-1, h-1), (0, 0, 200), 4)
            else:
                cv2.putText(img, "IDLE — Không phát hiện người",
                            (8, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 100), 1)
                cv2.rectangle(img, (0, 0), (w-1, h-1), (0, 80, 40), 2)

            # Chuyển đổi BGR → RGB → QImage
            img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            qimg = QImage(img_rgb.data, w, h, 3 * w, QImage.Format_RGB888)
            self.lbl_camera.setPixmap(QPixmap.fromImage(qimg).scaled(
                self.lbl_camera.width(), self.lbl_camera.height(),
                Qt.KeepAspectRatio, Qt.SmoothTransformation
            ))
        else:
            # Fallback nếu không có cv2
            text = "ALERT — PERSON DETECTED" if is_alert else " IDLE — NO PERSON"
            color = "#ff4444" if is_alert else "#44ff88"
            self.lbl_camera.setText(
                f'<span style="color:{color}; font-size:14pt; font-weight:bold;">{text}</span>')
            self.lbl_camera.setTextFormat(Qt.RichText)

    # ── LOG CONSOLE ───────────────────────────────────────────────────────────
    def log_console(self, text: str, alert: bool = False):
        color = "#ff5252" if alert else "#00e676"
        ts = datetime.now().strftime("[%H:%M:%S]")
        self.txt_console.append(f"<font color='{color}'>{ts} {text}</font>")
        # Auto-scroll
        sb = self.txt_console.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_real_camera_frame(self, frame):
        if frame is None:
            return

        rgb = cv2.cvtColor(
            frame,
            cv2.COLOR_BGR2RGB,
        )

        height, width, channels = rgb.shape
        bytes_per_line = channels * width

        image = QImage(
            rgb.data,
            width,
            height,
            bytes_per_line,
            QImage.Format_RGB888,
        ).copy()

        pixmap = QPixmap.fromImage(image)

        self.lbl_camera.setPixmap(
            pixmap.scaled(
                self.lbl_camera.width(),
                self.lbl_camera.height(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )

    def _on_ai_observation(
        self,
        person_detected: bool,
        event: dict,
    ):
        self._latest_person_detected = person_detected
        self._latest_ai_observation_at = time.monotonic()
        self._latest_ai_event = event

        label = event.get("label", "unknown")
        score = event.get("score", 0.0)
        class_id = event.get("class_id", -1)

        self.log_console(
            f"[EDGE AI] class={class_id} "
            f"label={label} "
            f"score={score:.2f} "
            f"person={person_detected}",
            alert=person_detected,
        )

    def _get_fresh_person_state(self):
        if self._latest_ai_observation_at is None:
            return False

        age = (
            time.monotonic()
            - self._latest_ai_observation_at
        )

        ttl = getattr(
            config,
            "AI_OBSERVATION_TTL_SEC",
            3.0,
        )

        if age > ttl:
            return False

        return self._latest_person_detected

    def closeEvent(self, event):
        if hasattr(self, "timer_bw"):
            self.timer_bw.stop()
        if hasattr(self, "timer_ai"):
            self.timer_ai.stop()
        if hasattr(self, "timer_sys"):
            self.timer_sys.stop()
        if hasattr(self, "telemetry_worker"):
            self.telemetry_worker.stop()
            self.telemetry_worker.wait(3000)
        if self.ai_camera_worker is not None:
            self.ai_camera_worker.stop()
        if self.snmp_bandwidth_worker is not None:
            self.snmp_bandwidth_worker.stop()
            stopped = self.snmp_bandwidth_worker.wait(
                5000
            )
        if (
            self.pi_camera_bandwidth_worker
            is not None
        ):
            self.pi_camera_bandwidth_worker.stop()

            stopped = (
                self.pi_camera_bandwidth_worker.wait(
                    3000
                )
            )

            if not stopped:
                self.log_console(
                    "[CAM03] Worker chưa dừng "
                    "trong 3 giây.",
                    alert=True,
                )
        event.accept()

    def _start_ai_camera_worker(self):
        usb_camera_config = config.LOCAL_CAMERAS["CAM04"]
        configured_model = getattr(
            config,
            "EDGE_MODEL_PATH",
            None,
        )

        if configured_model:
            model_path = Path(configured_model)
        else:
            model_path = (
                Path(__file__).resolve().parents[1]
                / "Edge"
                / "yolov8n.pt"
            )

        if not model_path.exists():
            self._on_ai_camera_error(
                f"Không tìm thấy model: {model_path}"
            )
            return
        self.ai_camera_worker = AICameraWorker(
            source_id="CAM04",
            bandwidth_interval_sec=(config.BANDWIDTH_POLL_INTERVAL_SEC),
            jpeg_quality=usb_camera_config.get("jpeg_quality", 80),
            camera_index=usb_camera_config[
                "device"
            ],
            model_path=str(model_path),
            confidence=getattr(
                config,
                "AI_CONFIDENCE",
                0.5,
            ),
            v4l2=usb_camera_config.get(
                "v4l2",
                True,
            ),
        )

        self.ai_camera_worker.frame_signal.connect(
            self._on_real_camera_frame
        )

        self.ai_camera_worker.observation_signal.connect(
            self._on_ai_observation
        )

        self.ai_camera_worker.error_signal.connect(
            self._on_ai_camera_error
        )
        self.ai_camera_worker.status_signal.connect(
            self._on_ai_camera_status
        )
        self.ai_camera_worker.bandwidth_signal.connect(
            self._on_local_camera_bandwidth
        )
        self.ai_camera_worker.start()

    def _on_ai_camera_error(self, message: str):
        self._on_local_camera_bandwidth_error("CAM04", message)
        self._latest_person_detected = False
        self._latest_ai_observation_at = None

        self.log_console(
            f"[EDGE AI ERROR] {message}",
            alert=True,
        )

    def _on_ai_camera_status(self, status: str):
        self._ai_available = status == "RUNNING"

        if status == "RUNNING":
            self.log_console(
                "[EDGE AI] Camera và model đã sẵn sàng."
            )

        elif status == "ERROR":
            self.log_console(
                "[EDGE AI] Camera/model gặp lỗi.",
                alert=True,
            )

        elif status == "STOPPED":
            self._latest_person_detected = False
            self._latest_ai_observation_at = None
