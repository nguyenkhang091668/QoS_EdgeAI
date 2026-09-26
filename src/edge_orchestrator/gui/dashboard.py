import sys
import time
import random
import threading
import queue
from datetime import datetime

from PyQt5.QtWidgets import (
    QWidget, QGridLayout, QLabel, QTextEdit,
    QGroupBox, QVBoxLayout, QHBoxLayout, QPushButton, QSizePolicy
)
from PyQt5.QtCore import QTimer, Qt, pyqtSignal, QObject
from PyQt5.QtGui import QFont, QImage, QPixmap, QColor, QPalette
import matplotlib
matplotlib.use("Qt5Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
import numpy as np
try:
    import cv2
    CV2_OK = True
except ImportError:
    CV2_OK = False

# Import từ các module nội bộ của package
from edge_orchestrator.core import config
from edge_orchestrator.core.decision_engine import DecisionEngine
from edge_orchestrator.network.syslog_receiver import syslog_event_queue
from edge_orchestrator.network.netmiko_qos_controller import apply_dynamic_qos, apply_acl_remediation
from edge_orchestrator.network.qos_telemetry import QoSTelemetryWorker

# ── SIGNAL BRIDGE (thread-safe GUI update) ───────────────────────────────────
class SignalBridge(QObject):
    log_signal  = pyqtSignal(str, bool)    # (text, is_alert)
    qos_signal  = pyqtSignal(str)          # QoS mode: "alert" / "normal"
    cam_signal  = pyqtSignal(bool)         # is_alert

bridge = SignalBridge()

# ── BANDWIDTH DATA GENERATOR ──────────────────────────────────────────────────
class BandwidthGenerator:
    """Mô phỏng dữ liệu băng thông theo trạng thái QoS."""
    def __init__(self):
        self._state = "NORMAL"

    def set_state(self, state: str):
        self._state = state

    def get_sample(self) -> dict:
        if self._state == "ALERT":
            base    = 8.0
            noise_r = random.uniform(-0.3, 0.5)
            noise_c = random.uniform(-0.2, 0.3)
        else:
            base    = 1.0
            noise_r = random.uniform(-0.05, 0.15)
            noise_c = random.uniform(-0.05, 0.1)

        return {
            "r1":  max(0, base + noise_r),
            "cam": max(0, base + noise_c),
            "dsw": max(0, (base * 0.6) + random.uniform(-0.1, 0.1)),
        }

# ── MAIN DASHBOARD WINDOW ─────────────────────────────────────────────────────
class MainDashboard(QWidget):
    def __init__(self, simulate_ai=True, simulate_netmiko=True, simulate_syslog=True):
        super().__init__()
        self.simulate_ai      = simulate_ai
        self.simulate_netmiko = simulate_netmiko
        self.simulate_syslog  = simulate_syslog

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

        # Data
        self.bw_data   = {"r1": [1.0]*60, "cam": [1.0]*60, "dsw": [1.0]*60}
        self.bw_gen    = BandwidthGenerator()
        self.decision  = DecisionEngine(
            alert_sec=getattr(config, "ALERT_TRIGGER_SECONDS", 3),
            normal_sec=getattr(config, "NORMAL_TRIGGER_SECONDS", 10),
            simulate=simulate_ai
        )
        self._qos_mode     = "NORMAL"
        self._detect_count = 0
        self._frame_num    = 0
        self._latest_cam_bitrate = 1.0
        self.telemetry_data = {}

        self._init_ui()
        self._connect_signals()
        self._start_timers()

    def _init_ui(self):
        main_layout = QGridLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # ── KHUNG 1: Đồ thị Băng thông ────────────────────────────────────────
        g1 = QGroupBox("📊 KHUNG 1: BĂNG THÔNG THỜI GIAN THỰC (Mbps) — R1 / DSW / Camera RTSP")
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

        x = list(range(60))
        self.line_r1,  = self.ax.plot(x, self.bw_data["r1"],  color="#00e676", linewidth=1.5, label="Core R1")
        self.line_cam, = self.ax.plot(x, self.bw_data["cam"], color="#ffcc00", linewidth=1.5, label="Camera RTSP")
        self.line_dsw, = self.ax.plot(x, self.bw_data["dsw"], color="#40c4ff", linewidth=1,   label="DSW1", linestyle="--")
        self.ax.axhline(y=8.0, color="#ff5252", linestyle="--", linewidth=1, label="Ngưỡng nghẽn (8M)")
        self.ax.set_ylim(0, 10)
        self.ax.legend(loc="upper left", fontsize=7, facecolor="#1a1a2e", labelcolor="white",
                       framealpha=0.8)
        self.canvas = FigureCanvas(self.fig)
        lay1.addWidget(self.canvas)

        # ── KHUNG 2: Camera + AI Status ───────────────────────────────────────
        g2 = QGroupBox("📷 KHUNG 2: LUỒNG CAMERA & TRẠNG THÁI EDGE AI (CAM01 / CAM02)")
        g2.setFont(QFont("Arial", 9, QFont.Bold))
        g2.setStyleSheet("QGroupBox { color: #40c4ff; border: 1.5px solid #40c4ff; "
                         "margin-top: 8px; padding: 4px; border-radius: 4px; }")

        lay2 = QVBoxLayout(g2)
        self.lbl_camera = QLabel("Đang tải luồng RTSP từ CAM01...")
        self.lbl_camera.setAlignment(Qt.AlignCenter)
        self.lbl_camera.setStyleSheet("background-color: #000000; border: 1px solid #223344; "
                                      "border-radius: 3px; min-height: 160px;")
        self.lbl_status = QLabel("🟢  Trạng thái AI: IDLE NORMAL (1 Mbps)")
        self.lbl_status.setFont(QFont("Consolas", 11, QFont.Bold))
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setStyleSheet("color: #00e676; padding: 6px;")

        # Thống kê nhỏ
        stats_row = QHBoxLayout()
        self.lbl_detect_count = QLabel("Phát hiện: 0 lần")
        self.lbl_mode_badge   = QLabel("  NORMAL  ")
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
        g3 = QGroupBox("🔴 KHUNG 3: BẢO MẬT SYSLOG & CẢnh BÁO DYNAMIC QOS — Security & Congestion Log")
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
        btn_alert  = QPushButton("⚡ Nạp QoS ALERT (8M)")
        btn_normal = QPushButton("✅ Nạp QoS NORMAL (1M)")
        btn_acl    = QPushButton("🚫 Test ACL Remediation")
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
        bridge.qos_signal.connect(self._on_qos_change)
        bridge.cam_signal.connect(self._update_cam_frame)

    def _start_timers(self):
        self.timer_bw  = QTimer(); self.timer_bw.timeout.connect(self._tick_bandwidth)
        self.timer_ai  = QTimer(); self.timer_ai.timeout.connect(self._tick_ai)
        self.timer_sys = QTimer(); self.timer_sys.timeout.connect(self._tick_syslog)
        self.timer_bw.start(1000)   # Băng thông mỗi 1s
        self.timer_ai.start(2000)   # AI check mỗi 2s
        self.timer_sys.start(500)   # Syslog check mỗi 0.5s

        # Boot log
        self.log_console("[SYSTEM] ✓ MacBook Edge Orchestrator khởi động (thay thế Pi 4)")
        self.log_console(f"[SYSTEM] Chế độ: AI={'Mô phỏng' if self.simulate_ai else 'Thật'} | "
                         f"SSH={'Mô phỏng' if self.simulate_netmiko else 'Thật'} | "
                         f"Syslog={'Mô phỏng' if self.simulate_syslog else 'Thật'}")
        self.log_console("[NETMIKO] Đang kiểm tra kết nối SSH tới R1 (10.255.255.1)...")
        self.log_console("[TAILSCALE] VPN Overlay đang hoạt động — MacBook đã gia nhập Tailscale subnet")

        # Khởi chạy QoS Telemetry Worker (Tshark)
        self.telemetry_worker = QoSTelemetryWorker(simulate=config.SIMULATE_TELEMETRY)
        self.telemetry_worker.telemetry_signal.connect(self._update_telemetry)
        self.telemetry_worker.start()
        sim_mode = "Mô phỏng" if config.SIMULATE_TELEMETRY else "Thực tế (tshark)"
        self.log_console(f"[TELEMETRY] 📈 Đang lắng nghe RTP trên utun4 (Chế độ: {sim_mode})")

    # ── TICK FUNCTIONS ────────────────────────────────────────────────────────
    def _tick_bandwidth(self):
        """Cập nhật biểu đồ băng thông mỗi giây."""
        sample = self.bw_gen.get_sample()
        for key, data in self.bw_data.items():
            data.pop(0)
            if key == "cam":
                data.append(self._latest_cam_bitrate)
            else:
                data.append(sample[key])

        x = list(range(60))
        self.line_r1.set_data(x, self.bw_data["r1"])
        self.line_cam.set_data(x, self.bw_data["cam"])
        self.line_dsw.set_data(x, self.bw_data["dsw"])
        self.ax.set_xlim(0, 59)
        self.canvas.draw_idle()

    def _tick_ai(self):
        """Giả lập / đọc luồng AI mỗi 2 giây."""
        if self.simulate_ai:
            try:
                prob = config.AI_PERSON_DETECT_PROB
            except Exception:
                prob = 0.25
            person_detected = random.random() < prob
        else:
            # Placeholder: đọc từ YOLOv8 thật (khi chuyển Pi 4)
            person_detected = False  # sẽ thay bằng AI engine thật

        state_change = self.decision.update(
            person_detected, log_callback=lambda t, alert=False: self.log_console(t, alert)
        )
        if state_change:
            self._on_qos_change(state_change)

        # Cập nhật cam frame
        self._update_cam_frame(person_detected)

        if person_detected:
            self._detect_count += 1
            self.lbl_detect_count.setText(f"Phát hiện: {self._detect_count} lần")

    def _tick_syslog(self):
        """Đọc sự kiện từ syslog_event_queue và hiển thị."""
        try:
            while True:
                event = syslog_event_queue.get_nowait()
                etype = event.get("type", "GENERIC")
                data  = event.get("data", {})
                ts    = event.get("timestamp", "??:??:??")
                is_security = etype in ("PORT_SECURITY", "DAI_ARP")

                if etype == "PORT_SECURITY":
                    msg = (f"[{ts}] 🚨 PORT SECURITY VIOLATION | "
                           f"MAC: {data.get('mac','?')} | Port: {data.get('port','?')}")
                    self.log_console(msg, alert=True)
                    # Trigger Auto-Remediation (dùng IP giả lập nếu cần)
                    attacker_ip = "192.168.10.99"  # Trong thực tế: parse từ event
                    threading.Thread(
                        target=self._run_acl_remediation,
                        args=(attacker_ip, data.get('mac')),
                        daemon=True
                    ).start()

                elif etype == "DAI_ARP":
                    msg = (f"[{ts}] 🚨 DAI ARP SPOOFING | "
                           f"IP: {data.get('ip','?')} | MAC: {data.get('mac','?')}")
                    self.log_console(msg, alert=True)

                elif etype == "INTERFACE_FLAP":
                    state = data.get('state','?').upper()
                    msg   = f"[{ts}] ⚡ Interface {state}: {data.get('interface','?')}"
                    self.log_console(msg, alert=(state == "DOWN"))

                else:
                    msg = f"[{ts}] 📋 {event['raw'][:100]}"
                    self.log_console(msg)

        except queue.Empty:
            pass

    def _update_telemetry(self, metrics):
        """Nhận dữ liệu từ tshark (chạy mỗi 2s) và cập nhật đồ thị Khung 1"""
        total_cam_bitrate = 0.0
        for m in metrics:
            port = str(m["Port"])
            self.telemetry_data[port] = {
                "bitrate": m["Bitrate_Mbps"],
                "loss": m["Loss_Percent"],
                "jitter": m["Jitter_ms"]
            }
            total_cam_bitrate += m["Bitrate_Mbps"]
            
            # Cảnh báo QoS nếu mạng kém
            if m["Loss_Percent"] > 5.0 or m["Jitter_ms"] > 30.0:
                self.log_console(f"[TELEMETRY] ⚠️ CAM {m['ID']} ({port}) mạng kém: Loss={m['Loss_Percent']}%, Jitter={m['Jitter_ms']}ms", alert=True)
                
            # Log periodic nhẹ cho console đỡ trôi
            elif random.random() < 0.1:  # In 10% sample để show-off telemetry đang chạy
                self.log_console(f"[TELEMETRY] CAM {m['ID']} (Port {port}): {m['Bitrate_Mbps']} Mbps | Loss: {m['Loss_Percent']}% | Jitter: {m['Jitter_ms']}ms")

        # Ghi đè dữ liệu cho _tick_bandwidth vẽ đồ thị
        self._latest_cam_bitrate = total_cam_bitrate

    # ── QOS & ACL HANDLERS ────────────────────────────────────────────────────
    def _on_qos_change(self, mode: str):
        """Khi Decision Engine thay đổi state — trigger Netmiko QoS."""
        self._qos_mode = mode
        self.bw_gen.set_state(mode)

        if mode == "ALERT":
            self.lbl_status.setText("🔴  Trạng thái AI: PERSON DETECTED → ALERT (8 Mbps)")
            self.lbl_status.setStyleSheet("color: #ff5252; padding: 6px;")
            self.lbl_mode_badge.setText("  ALERT  ")
            self.lbl_mode_badge.setStyleSheet(
                "background: #7f0000; color: #ff5252; font-weight: bold; "
                "border-radius: 3px; padding: 2px 6px;"
            )
            self.log_console("[ALERT] 🚨 Phát hiện NGƯỜI → Kích hoạt Dynamic QoS 8 Mbps!", alert=True)
        else:
            self.lbl_status.setText("🟢  Trạng thái AI: IDLE NORMAL (1 Mbps)")
            self.lbl_status.setStyleSheet("color: #00e676; padding: 6px;")
            self.lbl_mode_badge.setText("  NORMAL  ")
            self.lbl_mode_badge.setStyleSheet(
                "background: #1b5e20; color: #00e676; font-weight: bold; "
                "border-radius: 3px; padding: 2px 6px;"
            )
            self.log_console("[NORMAL] ✅ Không còn người → Hạ QoS về 1 Mbps")

        # Chạy Netmiko trong background thread (không block GUI)
        threading.Thread(
            target=apply_dynamic_qos,
            kwargs={
                "mode": mode.lower(),
                "simulate": self.simulate_netmiko,
                "log_callback": lambda t, alert=False: self.log_console(t, alert),
            },
            daemon=True
        ).start()

    def _run_acl_remediation(self, attacker_ip: str, attacker_mac: str = None):
        apply_acl_remediation(
            attacker_ip=attacker_ip,
            attacker_mac=attacker_mac,
            simulate=self.simulate_netmiko,
            log_callback=lambda t, alert=False: self.log_console(t, alert),
        )

    def _manual_qos(self, mode: str):
        """Nút bấm thủ công test QoS."""
        self.log_console(f"[MANUAL] Người dùng kích hoạt QoS: {mode.upper()}", alert=(mode == "alert"))
        self._on_qos_change(mode.upper())

    def _manual_acl(self):
        """Nút bấm thủ công test ACL Remediation."""
        test_ip = "192.168.10.99"
        self.log_console(f"[MANUAL] Test Auto-Remediation ACL cho IP: {test_ip}", alert=True)
        threading.Thread(target=self._run_acl_remediation, args=(test_ip,), daemon=True).start()

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
            qimg    = QImage(img_rgb.data, w, h, 3 * w, QImage.Format_RGB888)
            self.lbl_camera.setPixmap(QPixmap.fromImage(qimg).scaled(
                self.lbl_camera.width(), self.lbl_camera.height(),
                Qt.KeepAspectRatio, Qt.SmoothTransformation
            ))
        else:
            # Fallback nếu không có cv2
            text = "🔴 ALERT — PERSON DETECTED" if is_alert else "🟢 IDLE — NO PERSON"
            color = "#ff4444" if is_alert else "#44ff88"
            self.lbl_camera.setText(f'<span style="color:{color}; font-size:14pt; font-weight:bold;">{text}</span>')
            self.lbl_camera.setTextFormat(Qt.RichText)

    # ── LOG CONSOLE ───────────────────────────────────────────────────────────
    def log_console(self, text: str, alert: bool = False):
        color = "#ff5252" if alert else "#00e676"
        ts    = datetime.now().strftime("[%H:%M:%S]")
        self.txt_console.append(f"<font color='{color}'>{ts} {text}</font>")
        # Auto-scroll
        sb = self.txt_console.verticalScrollBar()
        sb.setValue(sb.maximum())
