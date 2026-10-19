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
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
)
from PyQt5.QtCore import (
    QTimer,
    Qt,
    pyqtSignal,
    QObject,
)
from PyQt5.QtGui import (
    QFont,
    QImage,
    QPixmap,
    QColor,
    QPalette,
)
from edge_orchestrator.gui.audit_log import AuditLogStore
from edge_orchestrator.core import config
from edge_orchestrator.core.decision_engine import (
    QoSDecisionEngine,
    AIDecisionEngine,
)
from edge_orchestrator.core.event import EventType
from edge_orchestrator.network.local_camera_bandwidth import (
    LocalCameraBandwidthWorker
)
from edge_orchestrator.gui.ai_camera_worker import (
    AICameraWorker,
    SharedYOLOInferenceWorker,
)
from edge_orchestrator.network.bandwidth_store import (
    BandwidthStore,
)
from edge_orchestrator.network.bandwidth_types import (
    BandwidthSample,
    CollectorStatus,
)
from edge_orchestrator.network.network_controller import (
    NetworkController,
)
from edge_orchestrator.network.qos_sync import RouterQoSSynchronizer
from edge_orchestrator.network.pysnmp_client import (
    PySnmpV3Client,
)
from edge_orchestrator.network.qos_telemetry import (
    QoSTelemetryWorker,
)
from edge_orchestrator.network.snmp_bandwidth import (
    CiscoBandwidthCollector,
)
from edge_orchestrator.network.snmp_bandwidth_worker import (
    CiscoBandwidthWorker,
)
from edge_orchestrator.network.syslog_receiver import (
    syslog_event_queue,
)
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
import matplotlib.pyplot as plt
from pathlib import Path
from datetime import datetime
from html import escape
import queue
import threading
import random
import time
import sys
import numpy as np
import matplotlib
matplotlib.use("Qt5Agg")

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
DEVICE_COLOR = {
    "R1": "#00e676",
    "R2": "#76ff03",
    "DSW1": "#40c4ff",
    "DSW2": "#448aff",
    "ASW": "#e040fb",
    "CAM01": "#ffca28",
    "CAM02": "#ff7043",
    "CAM03": "#f06292",
    "CAM04": "#00e5ff",
}
LINE_STYLE = ("-", "--", ":", "-.")
BANDWIDTH_LINE_STYLES = {
    source_id: {
        "color": DEVICE_COLOR.get(
            source_id.split("_")[0],
            "#ffffff",
        ),
        "linestyle": LINE_STYLE[i % len(LINE_STYLE)],
    }
    for i, source_id in enumerate(BANDWIDTH_SOURCE_IDS)
}
# Import từ các module nội bộ của package

# ── SIGNAL BRIDGE (thread-safe GUI update) ───────────────────────────────────


class SignalBridge(QObject):
    log_signal = pyqtSignal(str, bool)    # (text, is_alert)
    qos_signal = pyqtSignal(str)          # QoS mode: "alert" / "normal"
    qos_result_signal = pyqtSignal(object)  # worker -> GUI: per-router result
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
            "TRẠM ĐIỀU PHỐI BIÊN — TDTU ELV AGENT | Nguyễn Tiết Khang 41900966"
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
        self._desired_policy = "NORMAL"
        self._qos_closing = False
        self._qos_mode = "NORMAL"
        self._detect_count = 0
        self._frame_num = 0
        self.telemetry_data = {}
        self.network_controllers = {}
        self._network_controller_errors = {}
        if not self.simulate_netmiko:
            for name in ("R1", "R2"):
                try:
                    self.network_controllers[name] = NetworkController(
                        target_device=name)
                except Exception as error:
                    self._network_controller_errors[name] = str(error)
        self.qos_sync = RouterQoSSynchronizer(
            self.network_controllers,
            callback=bridge.qos_result_signal.emit,
            retry_sec=15,
            verify_sec=15,
        )

        self._latest_person_detected = False
        self._latest_ai_observation_at = None
        self._latest_ai_event = None
        self._ai_observations_by_camera = {}
        self.pi_camera_bandwidth_worker = None
        self._local_camera_status_cache = {}
        self.ai_camera_worker = None
        self.ai_camera_workers = {}
        self.shared_yolo_worker = None
        self.audit_log_store = AuditLogStore(1000)
        self._init_ui()
        self._connect_signals()
        self._device_last_syslog = {}
        self._start_timers()

        if not self.simulate_netmiko:
            self._start_snmp_bandwidth_worker()
        else:
            self.log_console(
                "SIMULATE MODE: Netmiko → Disabled"
            )
        if not self.simulate_ai:
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
            line, = self.ax.plot([], [], color=style["color"], linestyle=style["linestyle"], linewidth=1.4,
                                 label=(f"{source_id} [{CollectorStatus.WARMING_UP.value}]"),)
            self.bandwidth_lines[source_id] = line
        self.ax.axhline(y=8.0, color="#ff5252", linestyle=":",
                        linewidth=1, label="Ngưỡng QoS 8 Mbps")
        self.ax.set_xlim(-config.BANDWIDTH_HISTORY_SEC, 0)
        self.ax.set_ylim(0, 10)
        self.ax.legend(loc="upper left", fontsize=6, ncol=2,
                       facecolor="#1a1a2e", labelcolor="white", framealpha=0.8,)
        self.canvas = FigureCanvas(self.fig)
        lay1.addWidget(self.canvas)

        # ── KHUNG 2: Camera + AI Status ───────────────────────────────────────
        g2 = QGroupBox(" KHUNG 2: CAM03 (USB) & CAM04 (CSI) — YOLOv8 NCNN ")
        g2.setFont(QFont("Arial", 9, QFont.Bold))
        g2.setStyleSheet(
            "QGroupBox { color: #40c4ff; border: 1.5px solid #40c4ff; "
            "margin-top: 8px; padding: 4px; border-radius: 4px; }"
        )
        lay2 = QVBoxLayout(g2)
        cam_row = QHBoxLayout()
        self.lbl_cam03 = QLabel("Đang mở CAM03 (USB)")
        self.lbl_cam04 = QLabel("Đang mở CAM04 (CSI)")
        for lbl in (self.lbl_cam03, self.lbl_cam04):
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(
                "background-color: #000000; border: 1px solid #223344; "
                "border-radius: 3px; min-height: 160px;"
            )
            cam_row.addWidget(lbl, stretch=1)
        self.lbl_camera = self.lbl_cam03

        self.lbl_status = QLabel(
            "CAM03 & CAM04: đang khởi tạo camera và model...")
        self.lbl_status.setFont(QFont("Consolas", 11, QFont.Bold))
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setStyleSheet("color: #ffca28; padding: 6px;")
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

        lay2.addLayout(cam_row, stretch=3)
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
        self.txt_console.document().setMaximumBlockCount(1000)
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
        # ── KHUNG 4: Tình trạng Online của Cổng & Thiết bị ────────────────────
        g4 = QGroupBox(
            " KHUNG 4: TÌNH TRẠNG ONLINE CÁC CỔNG & THIẾT BỊ — Device & Port Status"
        )
        g4.setFont(QFont("Arial", 9, QFont.Bold))
        g4.setStyleSheet(
            "QGroupBox { color: #ffca28; border: 1.5px solid #ffca28; "
            "margin-top: 8px; padding: 4px; border-radius: 4px; }"
        )
        lay4 = QVBoxLayout(g4)
        self.tbl_status = QTableWidget()
        self.tbl_status.setColumnCount(4)
        self.tbl_status.setHorizontalHeaderLabels(
            ["Thiết bị / Nguồn", "Cổng / Luồng", "Trạng thái", "Chi tiết / QoS"]
        )
        self.tbl_status.verticalHeader().setVisible(False)
        self.tbl_status.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_status.setSelectionMode(QAbstractItemView.NoSelection)
        self.tbl_status.setFocusPolicy(Qt.NoFocus)
        self.tbl_status.setFont(QFont("Menlo", 8))
        self.tbl_status.setStyleSheet(
            "QTableWidget { background-color: #0a0a0f; color: #e6edf3; "
            "gridline-color: #21262d; border: none; } "
            "QHeaderView::section { background-color: #161b22; color: #ffca28; "
            "font-weight: bold; font-size: 8pt; border: 1px solid #21262d; padding: 2px; }"
        )
        header4 = self.tbl_status.horizontalHeader()
        header4.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header4.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header4.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header4.setSectionResizeMode(3, QHeaderView.Stretch)

        # Khởi tạo sẵn các hàng cố định (15 cổng SNMP + 2 luồng RTP + 2 Camera локаl)
        self._status_rows = (
            *config.SNMP_DEVICES.keys(),
            *config.RTP_STREAMS.keys(),
            *config.LOCAL_CAMERAS.keys(),
        )
        self.tbl_status.setRowCount(len(self._status_rows))
        self._status_cells = {}
        for row_idx, source_id in enumerate(self._status_rows):
            self.tbl_status.setRowHeight(row_idx, 20)
            port_label = self._get_source_port_label(source_id)
            items = [
                QTableWidgetItem(source_id),
                QTableWidgetItem(port_label),
                QTableWidgetItem("WARMING_UP"),
                QTableWidgetItem("—"),
            ]
            for col_idx, item in enumerate(items):
                if col_idx in (0, 2):
                    item.setTextAlignment(Qt.AlignCenter)
                self.tbl_status.setItem(row_idx, col_idx, item)
            self._status_cells[source_id] = items

        lay4.addWidget(self.tbl_status)

        # ── Bố trí Grid ───────────────────────────────────────────────────────
        main_layout.addWidget(g1, 0, 0, 1, 1)
        main_layout.addWidget(g2, 0, 1, 1, 1)
        main_layout.addWidget(g3, 1, 0, 1, 1)
        main_layout.addWidget(g4, 1, 1, 1, 1)
        main_layout.setRowStretch(0, 3)
        main_layout.setRowStretch(1, 2)

    def _get_source_port_label(self, source_id: str) -> str:
        if source_id in config.SNMP_DEVICES:
            dev = config.SNMP_DEVICES[source_id]
            return f"{dev['interface']} ({dev['direction'].upper()})"
        if source_id in config.RTP_STREAMS:
            rtp = config.RTP_STREAMS[source_id]
            return f"UDP/{rtp['destination_port']}"
        if source_id in config.LOCAL_CAMERAS:
            cam = config.LOCAL_CAMERAS[source_id]
            return f"{cam['source'].upper()} #{cam['device']}"
        return "—"

    def _update_device_status_table(self, snapshot: dict):
        if not hasattr(self, "_status_cells"):
            return
        qos_snap = self.qos_sync.snapshot() if hasattr(self, "qos_sync") else {}

        for source_id, items in self._status_cells.items():
            info = snapshot.get(source_id, {})
            status_enum = info.get("status", CollectorStatus.WARMING_UP)
            sample = info.get("sample")
            status_text = status_enum.value if hasattr(
                status_enum, "value") else str(status_enum)

            # Xác định màu sắc theo trạng thái
            if status_text in ("ONLINE", "STREAMING", "RUNNING"):
                color = QColor("#00e676")  # Xanh lá
            elif status_text in ("WARMING_UP", "STALE", "NO_STREAM"):
                color = QColor("#ffca28")  # Vàng
            else:
                # Đỏ (INTERFACE_DOWN, OFFLINE, COLLECTOR_ERROR)
                color = QColor("#ff5252")

            # Chi tiết tốc độ / QoS / Loss
            detail_text = "—"
            if sample is not None and sample.value_mbps is not None:
                detail_text = f"{sample.value_mbps:.2f} Mbps"
                if source_id in config.RTP_STREAMS and sample.details:
                    loss = sample.details.get("loss_percent", 0.0)
                    jitter = sample.details.get("jitter_ms", 0.0)
                    detail_text += f" | L:{loss:.1f}% J:{jitter:.1f}ms"
            elif sample is not None and sample.error:
                detail_text = sample.error[:35]

            # Nếu là nhánh WAN của R1/R2 thì ghép thêm trạng thái QoS Policy
            router_prefix = source_id.split("_")[0]
            if source_id in ("R1_WAN", "R2_WAN") and router_prefix in qos_snap:
                applied = qos_snap[router_prefix].get(
                    "applied_policy") or self._desired_policy
                detail_text = f"{detail_text} | QoS:{applied}"

            # Chỉ cập nhật GUI khi text thay đổi để tiết kiệm CPU
            if items[2].text() != status_text:
                items[2].setText(status_text)
                items[2].setForeground(color)
            if items[3].text() != detail_text:
                items[3].setText(detail_text)

    def _connect_signals(self):
        bridge.log_signal.connect(self.log_console)
        bridge.qos_signal.connect(self._apply_network_policy)
        bridge.qos_result_signal.connect(self._on_qos_sync_result)
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
        self.timer_qos = QTimer(self)
        self.timer_qos.timeout.connect(self._tick_qos_sync)
        # Schedule only; SSH runs in per-router workers.
        self.timer_qos.start(1000)

        # Boot log
        self.log_console(
            "[SYSTEM] ✓ MacBook Edge Orchestrator khởi động (thay thế Pi 4)")
        self.log_console(f"[SYSTEM] Chế độ: AI={'Mô phỏng' if self.simulate_ai else 'Thật'} | "
                         f"SSH={'Mô phỏng' if self.simulate_netmiko else 'Thật'} | "
                         f"Syslog={'Mô phỏng' if self.simulate_syslog else 'Thật'}")
        if not self.simulate_netmiko:
            self.log_console(
                "[DYNAMIC QOS] Điều phối R1/R2 độc lập; retry và xác minh mỗi 15s.")
            for name, error in self._network_controller_errors.items():
                self.log_console(
                    f"[NETMIKO] [{name}] Lỗi khởi tạo: {error}", alert=True)
        self.log_console(
            "[TAILSCALE] VPN Overlay đang hoạt động — MacBook đã gia nhập Tailscale subnet")

        # Khởi chạy QoS Telemetry Worker (Tshark)
        self.telemetry_worker = QoSTelemetryWorker(
            simulate=config.SIMULATE_TELEMETRY)
        self.telemetry_worker.telemetry_signal.connect(self._update_telemetry)
        self.telemetry_worker.collector_error_signal.connect(
            self._on_telemetry_collector_error)
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
            self.pi_camera_bandwidth_worker.error_signal.connect(
                self._on_local_camera_bandwidth_error)
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
                    max_workers=min(5, len(config.SNMP_DEVICES)),
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
                f"[SNMP] Đã khởi động băng thông Cisco: "
                f"{len(config.SNMP_DEVICES)} nguồn interface",
            )
        except Exception as error:
            sampled_at = time.time()
            for source_id, device_config in config.SNMP_DEVICES.items():
                error_sample = BandwidthSample.unavailable(
                    source_id=source_id,
                    source_type="snmp",
                    status=CollectorStatus.COLLECTOR_ERROR,
                    sampled_at=sampled_at,
                    details={
                        "host": device_config["host"],
                        "interface": device_config["interface"],
                        "direction": device_config["direction"],
                        "label": device_config.get(
                            "label",
                            "",
                        ),
                    },
                    error=str(error),
                )

                try:
                    self.bandwidth_store.update(error_sample)
                except Exception as store_error:
                    self.log_console(
                        f"[SNMP] Không thể lưu trạng thái lỗi {source_id}: {store_error}", alert=True)
                self._bandwidth_status_cache[source_id] = CollectorStatus.COLLECTOR_ERROR
            self.log_console(
                f"[SNMP] Lỗi khởi động collector: {error}", alert=True)

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
        """Vẽ lịch sử bandwidth thật từ BandwidthStore bằng kỹ thuật Blitting (<10ms trên Pi 4)."""
        now = time.time()
        snapshot = self.bandwidth_store.snapshot(now=now)
        status_signature = []
        maximum_value = 0.0
        valid_statuses = {CollectorStatus.ONLINE, CollectorStatus.STREAMING}
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
            status_signature.append((source_id, status.value))

        status_signature = tuple(status_signature)
        legend_changed = status_signature != self._bandwidth_legend_signature
        if legend_changed:
            self._bandwidth_legend_signature = status_signature
            for source_id, status_val in status_signature:
                self.bandwidth_lines[source_id].set_label(
                    f"{source_id} [{status_val}]"
                )
            self.ax.legend(
                loc="upper left",
                fontsize=6,
                ncol=2,
                facecolor="#1a1a2e",
                labelcolor="white",
                framealpha=0.8,
            )

        target_ymax = max(10.0, round(maximum_value * 1.2, 1))
        current_ylim = self.ax.get_ylim()
        ylim_changed = abs(current_ylim[1] - target_ymax) > 0.5
        if ylim_changed:
            self.ax.set_ylim(0, target_ymax)

        canvas_size = (self.canvas.width(), self.canvas.height())
        bg_cache = getattr(self, "_bw_bg_cache", None)
        cached_size = getattr(self, "_bw_canvas_size", None)

        try:
            if (
                bg_cache is None
                or cached_size != canvas_size
                or legend_changed
                or ylim_changed
            ):
                for line in self.bandwidth_lines.values():
                    line.set_visible(False)
                self.canvas.draw()
                bg_cache = self.canvas.copy_from_bbox(self.ax.bbox)
                self._bw_bg_cache = bg_cache
                self._bw_canvas_size = canvas_size
                for line in self.bandwidth_lines.values():
                    line.set_visible(True)

            self.canvas.restore_region(bg_cache)
            for line in self.bandwidth_lines.values():
                self.ax.draw_artist(line)
            legend = self.ax.get_legend()
            if legend is not None:
                self.ax.draw_artist(legend)
            self.canvas.blit(self.ax.bbox)
        except Exception:
            self.canvas.draw_idle()

        self._update_device_status_table(snapshot)

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
            if person_detected is None:
                # Không coi dữ liệu hết hạn/chưa xác nhận là không có người
                return
        ai_state_change = self.ai_engine.update(
            person_detected, log_callback=lambda text, alert=False: self.log_console(text, alert),)
        if ai_state_change is not None:
            self._on_ai_state_change(ai_state_change)

    def _on_ai_state_change(self, state: str):
        self._ai_state = state

        if state == "ALERT":
            self._detect_count += 1
            self.lbl_detect_count.setText(
                f" Phát hiện: {self._detect_count} lần"
            )
            self.lbl_status.setText(
                " Trạng thái AI: PERSON DETECTED → ALERT"
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
                device = event.get("device_name") or event.get(
                    "source_ip", "UNKOWN")
                self._device_last_syslog[device] = f"{event_type}"
                if event_type == EventType.PORT_SECURITY_VIOLATION:
                    mac = data.get("mac")
                    interface = data.get("interface", "?")
                    self.log_console(
                        f"[{timestamp}] [{device}] PORT SECURITY VIOLATION | MAC: {mac or '?'} | Port: {interface}", alert=True)
                elif event_type == EventType.DAI_ARP_SPOOFING_DENY:
                    attacker_ip = data.get("src_ip")
                    attacker_mac = data.get("src_mac")
                    interface = data.get("interface", "?")

                    self.log_console(
                        f"[{timestamp}] [{device}] DAI ARP SPOOFING | IP: {attacker_ip or '?'} | MAC: {attacker_mac or '?'} | Port: {interface}", alert=True)

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
                    state = data.get("state", "unknown").upper()
                    interface = data.get("interface", "?")
                    self.log_console(
                        f"[{timestamp}] [{device}] Interface {interface}: {state}", alert=(state == "DOWN"))
                else:
                    raw = event.get("raw", "")
                    details = event.get("details", raw)
                    self.log_console(
                        f"[{timestamp}] [{device}] {event_type}: {details[:120]}")

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
        # Telemetry remains monitoring-only. Automatic QoS follows AI alone.
        if self._ai_state == "ALERT":
            self._apply_network_policy("ALERT")
        elif self._ai_state == "NORMAL":
            self._apply_network_policy("NORMAL")

    def _on_network_state_change(self, state: str):
        self._network_state = state
        if state == "QOS_DEGRADED":
            self.log_console(
                "[NETWORK]  Mạng suy thoái — cảnh báo giám sát.",
                alert=True,
            )
        elif state == "NORMAL":
            self.log_console(
                "[NETWORK]  Chất lượng mạng đã phục hồi.",
                alert=False,
            )

    def _apply_network_policy(self, policy: str):
        policy = policy.upper()
        if self._qos_closing or policy not in {"ALERT", "NORMAL"}:
            if not self._qos_closing:
                self.log_console(
                    f"[DYNAMIC QOS] Policy không hợp lệ: {policy}", alert=True)
            return False
        changed = policy != self._desired_policy
        self._desired_policy = policy
        if self.simulate_netmiko:
            if changed:
                self.log_console(
                    f"[NETMIKO SIMULATION] Yêu cầu policy {policy}")
            return True
        if changed:
            self.log_console(
                f"[DYNAMIC QOS] Yêu cầu {policy} cho R1/R2; chờ xác minh từng router.")
        self.qos_sync.set_desired_policy(policy)
        self.qos_sync.reconcile()
        # This is a request acknowledgement, never an applied-policy result.
        return bool(self.network_controllers)

    def _tick_qos_sync(self):
        if not self._qos_closing and not self.simulate_netmiko:
            self.qos_sync.reconcile()

    def _on_qos_sync_result(self, event):
        if self._qos_closing:
            return
        self.log_console(
            f"[DYNAMIC QOS] [{event.device}] {event.message}",
            alert=not event.success,
        )

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

        controller = self.network_controllers.get("R1")
        if controller is None:
            self.log_console(
                "[ACL] Controller R1 chưa được khởi tạo.",
                alert=True,
            )
            return

        controller.isolate_attacker(
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
    def log_console(
        self,
        text: str,
        alert: bool = False,
        category: str = "Hệ thống",
    ):
        if category == "Hệ thống" and text.startswith("[DYNAMIC QOS]"):
            category = "Dynamic QoS"
        entry = self.audit_log_store.append(
            message=text, category=category, alert=alert)
        self._append_log_entry(entry)

    def _append_log_entry(self, entry):
        color = "#ff5252" if entry.alert else "#00e676"
        timestamp = escape(entry.timestamp)
        category = escape(entry.category)
        message = escape(entry.message)
        self.txt_console.append(
            f"<font color='{color}'>"
            f"[{timestamp}] [{category}] {message}"
            f"</font>"
        )
        scrollbar = self.txt_console.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _on_real_camera_frame(self, frame, source_id="CAM03"):
        if frame is None:
            return

        target_label = (
            getattr(self, "lbl_cam04", self.lbl_camera)
            if source_id == "CAM04"
            else getattr(self, "lbl_cam03", self.lbl_camera)
        )
        src_h, src_w = frame.shape[:2]
        lbl_w = max(1, target_label.width())
        lbl_h = max(1, target_label.height())

        # Co giãn bằng OpenCV trước khi đổi hệ màu & tạo QPixmap để giảm 4x số điểm ảnh xử lý trên Main Thread
        scale = min(lbl_w / float(src_w), lbl_h / float(src_h))
        if 0.05 < scale < 0.98:
            dst_w = max(1, int(src_w * scale))
            dst_h = max(1, int(src_h * scale))
            resized = cv2.resize(
                frame,
                (dst_w, dst_h),
                interpolation=cv2.INTER_NEAREST,
            )
        else:
            resized = frame

        rgb = cv2.cvtColor(
            resized,
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
        )
        target_label.setPixmap(QPixmap.fromImage(image))

    def _on_real_camera_frame_packet(
        self,
        source_id: str,
        frame_id: int,
        captured_at: float,
        frame,
    ):
        # Cơ chế 1-Slot Latest-Frame: nếu trong hàng đợi Qt đã có frame mới hơn từ cùng camera thì bỏ qua ngay frame cũ
        workers_map = getattr(self, "ai_camera_workers", None)
        if workers_map:
            worker = workers_map.get(source_id)
            if (
                worker is not None
                and getattr(worker, "latest_emitted_frame_id", 0) > frame_id
            ):
                worker.dropped_gui_frames = (
                    getattr(worker, "dropped_gui_frames", 0) + 1
                )
                return

        frame_meta = getattr(self, "_latest_frame_meta_by_camera", None)
        if frame_meta is None:
            frame_meta = {}
            self._latest_frame_meta_by_camera = frame_meta
        frame_meta[source_id] = {
            "source_id": source_id,
            "frame_id": frame_id,
            "captured_at": captured_at,
        }
        self._on_real_camera_frame(frame, source_id)

    def _on_ai_observation(
        self,
        person_detected: bool,
        event: dict,
        source_id: str = None,
    ):
        now_mono = time.monotonic()
        obs_map = getattr(self, "_ai_observations_by_camera", None)
        if obs_map is None:
            obs_map = {}
            self._ai_observations_by_camera = obs_map

        cam_id = (
            source_id
            or (event.get("source_id") if isinstance(event, dict) else None)
            or "CAM03"
        )
        frame_id = event.get("frame_id") if isinstance(event, dict) else None
        captured_at = event.get("captured_at") if isinstance(
            event, dict) else None

        prev = obs_map.get(cam_id)
        if (
            prev is not None
            and frame_id is not None
            and prev.get("frame_id") is not None
            and frame_id < prev["frame_id"]
        ):
            return

        obs_map[cam_id] = {
            "source_id": cam_id,
            "person_detected": bool(person_detected),
            "observed_at": now_mono,
            "captured_at": captured_at,
            "frame_id": frame_id,
            "event": event,
        }
        self._latest_person_detected = any(
            item.get("person_detected", False) for item in obs_map.values()
        )
        self._latest_ai_observation_at = now_mono
        self._latest_ai_event = event

    def _get_fresh_person_state(self):
        now = time.monotonic()
        ttl = getattr(config, "AI_OBSERVATION_TTL_SEC", 3.5)
        obs_map = getattr(self, "_ai_observations_by_camera", None) or {}

        if not obs_map:
            if self._latest_ai_observation_at is None:
                return None
            age = now - self._latest_ai_observation_at
            if age > ttl:
                return None
            return self._latest_person_detected

        has_fresh_true = False
        has_fresh_false = False
        has_expired_true = False

        active_cams = getattr(self, "_active_ai_cameras", None)
        cam_ids = active_cams if active_cams else obs_map.keys()

        for cam_id in cam_ids:
            item = obs_map.get(cam_id)
            if item is None or item.get("observed_at") is None:
                continue
            age = now - item["observed_at"]
            detected = bool(item.get("person_detected", False))
            if age <= ttl:
                if detected:
                    has_fresh_true = True
                else:
                    has_fresh_false = True
            else:
                if detected:
                    has_expired_true = True

        if has_fresh_true:
            return True
        if has_expired_true:
            # Dữ liệu phát hiện người đã hết hạn nhưng chưa có kết quả inference mới xác nhận không người
            return None
        if has_fresh_false:
            return False
        return None

    def _stop_ai_workers(self):
        """Dừng và giải phóng an toàn: dừng các luồng capture trước, sau đó đánh thức và dừng worker YOLO chung."""
        workers = list(getattr(self, "ai_camera_workers", {}).values())
        for worker in workers:
            if hasattr(worker, "request_stop"):
                worker.request_stop()
        for worker in workers:
            worker.stop()
        if (
            self.ai_camera_worker is not None
            and self.ai_camera_worker not in workers
        ):
            self.ai_camera_worker.stop()
        self.ai_camera_workers = {}
        self.ai_camera_worker = None

        yolo_worker = getattr(self, "shared_yolo_worker", None)
        if yolo_worker is not None:
            yolo_worker.stop()
            self.shared_yolo_worker = None

    def closeEvent(self, event):
        self._qos_closing = True
        if hasattr(self, "timer_qos"):
            self.timer_qos.stop()
        self.qos_sync.close(timeout_sec=1.0)
        if hasattr(self, "timer_bw"):
            self.timer_bw.stop()
        if hasattr(self, "timer_ai"):
            self.timer_ai.stop()
        if hasattr(self, "timer_sys"):
            self.timer_sys.stop()
        if hasattr(self, "telemetry_worker"):
            self.telemetry_worker.stop()
            self.telemetry_worker.wait(3000)
        self._stop_ai_workers()
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
        configured_model = getattr(config, "EDGE_MODEL_PATH", None)
        edge_dir = Path(__file__).resolve().parents[1] / "Edge"
        ncnn_path = edge_dir / "yolov8n_ncnn_model"
        model_path = Path(configured_model) if configured_model else (
            ncnn_path if ncnn_path.exists() else edge_dir / "yolov8n.pt"
        )
        if not model_path.exists():
            self._on_ai_camera_error(f"Không tìm thấy model: {model_path}")
            return

        self._stop_ai_workers()
        self.ai_camera_workers = {}
        self._active_ai_cameras = set()
        self._ai_observations_by_camera = {}

        enabled_cam_ids = [
            cam_id
            for cam_id, cam_cfg in config.LOCAL_CAMERAS.items()
            if cam_cfg.get("enabled", True)
        ]
        if not enabled_cam_ids:
            return

        self.shared_yolo_worker = SharedYOLOInferenceWorker(
            model_path=str(model_path),
            confidence=getattr(config, "AI_CONFIDENCE", 0.5),
            threads=getattr(config, "AI_THREADS", 2),
            camera_order=tuple(enabled_cam_ids),
        )
        self.shared_yolo_worker.observation_signal.connect(
            self._on_ai_observation)
        self.shared_yolo_worker.error_signal.connect(
            lambda src, msg: self._on_ai_camera_error(msg, src)
        )
        self.shared_yolo_worker.start()

        for cam_id, cam_cfg in config.LOCAL_CAMERAS.items():
            if not cam_cfg.get("enabled", True):
                continue
            worker = AICameraWorker(
                source_id=cam_id,
                source=cam_cfg.get("source", "usb"),
                bandwidth_interval_sec=config.BANDWIDTH_POLL_INTERVAL_SEC,
                jpeg_quality=cam_cfg.get("jpeg_quality", 80),
                camera_index=int(cam_cfg.get("device", 0)),
                model_path=str(model_path),
                confidence=getattr(config, "AI_CONFIDENCE", 0.5),
                v4l2=cam_cfg.get("v4l2", False),
                shared_yolo_worker=self.shared_yolo_worker,
                max_box_age_sec=getattr(config, "AI_MAX_BOX_AGE_SEC", 2.0),
                active_recheck_sec=getattr(
                    config, "AI_ACTIVE_RECHECK_SEC", 0.8),
                idle_recheck_sec=getattr(config, "AI_IDLE_RECHECK_SEC", 1.5),
            )
            worker.frame_packet_signal.connect(
                self._on_real_camera_frame_packet)
            worker.observation_signal.connect(self._on_ai_observation)
            worker.error_signal.connect(
                lambda msg, cid=cam_id: self._on_ai_camera_error(msg, cid)
            )
            worker.status_signal.connect(
                lambda status, cid=cam_id: self._on_ai_camera_status(
                    status, cid)
            )
            worker.bandwidth_signal.connect(self._on_local_camera_bandwidth)
            worker.start()
            self.ai_camera_workers[cam_id] = worker
            if cam_id == "CAM03":
                self.ai_camera_worker = worker

    def _on_ai_camera_error(self, message: str, source_id: str = "CAM03"):
        active_cams = getattr(self, "_active_ai_cameras", set())
        active_cams.discard(source_id)
        obs_map = getattr(self, "_ai_observations_by_camera", None)
        if obs_map is not None:
            obs_map.pop(source_id, None)
        worker = getattr(self, "ai_camera_workers", {}).get(source_id)
        if worker is not None and getattr(worker, "_camera_ref", None) is not None:
            worker._camera_ref.clear_ai_state()
        if self.shared_yolo_worker is not None:
            self.shared_yolo_worker.unregister_camera(source_id)
        if source_id in config.LOCAL_CAMERAS:
            self._on_local_camera_bandwidth_error(source_id, message)
        if not active_cams:
            self._ai_available = False
            self.lbl_status.setText(f"{source_id} error: {message}")
            self.lbl_status.setStyleSheet("color: #ff5252; padding: 6px;")
            self._latest_person_detected = False
            self._latest_ai_observation_at = None

        self.log_console(
            f"[EDGE AI ERROR][{source_id}] {message}",
            alert=True,
        )

    def _on_ai_camera_status(self, status: str, source_id: str = "CAM03"):
        active_cams = getattr(self, "_active_ai_cameras", set())
        if status == "RUNNING":
            active_cams.add(source_id)
            self._active_ai_cameras = active_cams
            self._ai_available = True
            cams_txt = ", ".join(sorted(active_cams))
            self.lbl_status.setText(
                f"{cams_txt}: Đã khởi tạo — sẵn sàng nhận diện")
            self.lbl_status.setStyleSheet("color: #00e676; padding: 6px;")
            self.log_console(
                f"[EDGE AI][{source_id}] Camera và model đã sẵn sàng.")
        elif status == "ERROR":
            active_cams.discard(source_id)
            obs_map = getattr(self, "_ai_observations_by_camera", None)
            if obs_map is not None:
                obs_map.pop(source_id, None)
            self.log_console(
                f"[EDGE AI][{source_id}] Camera/model gặp lỗi.", alert=True)
        elif status == "STOPPED":
            active_cams.discard(source_id)
            obs_map = getattr(self, "_ai_observations_by_camera", None)
            if obs_map is not None:
                obs_map.pop(source_id, None)
            if not active_cams:
                self._ai_available = False
                self._latest_person_detected = False
                self._latest_ai_observation_at = None
