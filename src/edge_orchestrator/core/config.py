#!/usr/bin/env python3
"""
config.py — Cấu hình tập trung cho toàn bộ Pipeline DATN
Đồ án: Nguyễn Tiết Khang (41900966) - TDTU
Hướng dẫn: Điền IP thực tế Tailscale của bạn trước khi chạy.
"""

import os

from edge_orchestrator.core.qos_profile import load_qos_profile

# ==============================================================================
# [BƯỚC 1] ĐIỀN IP TAILSCALE THỰC TẾ CỦA BẠN VÀO ĐÂY
# Lệnh kiểm tra: tailscale status
# ==============================================================================
TAILSCALE_IP_PI4 = "100.103.159.73"
TAILSCALE_MACBOOK_IP = TAILSCALE_IP_PI4
TAILSCALE_GNS3_IP = "100.106.252.93"
RTP_CAPTURE_INTERFACE = os.environ.get(
    "RTP_CAPTURE_INTERFACE",
    "tailscale0",
)
BANDWIDTH_RATE_WINDOW_SEC = 6.0
BANDWIDTH_POLL_INTERVAL_SEC = 2.0
BANDWIDTH_STALE_AFTER_SEC = 6.0
BANDWIDTH_HISTORY_SEC = 60
BANDWIDTH_OFFLINE_FAILURES = 3

# ==============================================================================
# [BƯỚC 2] IP THIẾT BỊ MẠNG TRONG GNS3 (Loopback & VLAN99)
# ==============================================================================
R1_LOOPBACK_IP = "10.255.255.1"    # Core Router R1 Loopback 0
R2_LOOPBACK_IP = "10.255.255.2"    # Core Router R2 Loopback 0
DSW1_LOOPBACK_IP = "10.255.255.11"   # Distribution Switch DSW1 Loopback 0
DSW2_LOOPBACK_IP = "10.255.255.12"   # Distribution Switch DSW2 Loopback 0
ASW_VLAN99_IP = "192.168.99.100"  # Access Switch VLAN 99 Management IP

NETWORK_DEVICES = [
    {
        "name": "R1",
        "ip": R1_LOOPBACK_IP,
        "device_type": "cisco_ios",
    },
    {
        "name": "R2",
        "ip": R2_LOOPBACK_IP,
        "device_type": "cisco_ios",
    },
    {
        "name": "DSW1",
        "ip": DSW1_LOOPBACK_IP,
        "device_type": "cisco_ios",
    },
    {
        "name": "DSW2",
        "ip": DSW2_LOOPBACK_IP,
        "device_type": "cisco_ios",
    },
    {
        "name": "ASW",
        "ip": ASW_VLAN99_IP,
        "device_type": "cisco_ios",
    },
]
SNMP_PORT = int(
    os.environ.get(
        "SNMP_PORT",
        "161",
    )
)

SNMP_USERNAME = os.environ.get(
    "SNMP_USERNAME",
    "",
)

SNMP_AUTH_KEY = os.environ.get(
    "SNMP_AUTH_KEY",
    "",
)

SNMP_PRIV_KEY = os.environ.get(
    "SNMP_PRIV_KEY",
    "",
)

SNMP_TIMEOUT_SEC = float(
    os.environ.get(
        "SNMP_TIMEOUT_SEC",
        "0.8",
    )
)

SNMP_RETRIES = int(
    os.environ.get(
        "SNMP_RETRIES",
        "0",
    )
)
SNMP_DEVICES = {
    # ==========================================================
    # ASW — Camera đi vào và hai uplink tới DSW
    # ==========================================================
    "ASW_CAM": {
        "host": ASW_VLAN99_IP,
        "interface": "GigabitEthernet1/0",
        "direction": "rx",
        "label": "ASW CAM03 Gi1/0 RX",
    },
    "ASW_DSW1": {
        "host": ASW_VLAN99_IP,
        "interface": "GigabitEthernet0/0",
        "direction": "tx",
        "label": "ASW to DSW1 Gi0/0 TX",
    },
    "ASW_DSW2": {
        "host": ASW_VLAN99_IP,
        "interface": "GigabitEthernet0/1",
        "direction": "tx",
        "label": "ASW to DSW2 Gi0/1 TX",
    },

    # ==========================================================
    # DSW1 — ASW, EtherChannel và uplink R1
    # ==========================================================
    "DSW1_ASW": {
        "host": DSW1_LOOPBACK_IP,
        "interface": "GigabitEthernet0/2",
        "direction": "rx",
        "label": "DSW1 from ASW Gi0/2 RX",
    },
    "DSW1_PO1": {
        "host": DSW1_LOOPBACK_IP,
        "interface": "Port-channel1",
        "direction": "max",
        "label": "DSW1 Port-channel1",
    },
    "DSW1_R1": {
        "host": DSW1_LOOPBACK_IP,
        "interface": "GigabitEthernet0/0",
        "direction": "tx",
        "label": "DSW1 to R1 Gi0/0 TX",
    },

    # ==========================================================
    # DSW2 — ASW, EtherChannel và uplink R2
    # ==========================================================
    "DSW2_ASW": {
        "host": DSW2_LOOPBACK_IP,
        "interface": "GigabitEthernet0/2",
        "direction": "rx",
        "label": "DSW2 from ASW Gi0/2 RX",
    },
    "DSW2_PO1": {
        "host": DSW2_LOOPBACK_IP,
        "interface": "Port-channel1",
        "direction": "max",
        "label": "DSW2 Port-channel1",
    },
    "DSW2_R2": {
        "host": DSW2_LOOPBACK_IP,
        "interface": "GigabitEthernet0/0",
        "direction": "tx",
        "label": "DSW2 to R2 Gi0/0 TX",
    },

    # ==========================================================
    # R1 — DSW1, kết nối R2 và WAN
    # ==========================================================
    "R1_DSW1": {
        "host": R1_LOOPBACK_IP,
        "interface": "GigabitEthernet0/2.99",
        "direction": "rx",
        "label": "R1 from DSW1 Gi0/2.99 RX",
    },
    "R1_R2": {
        "host": R1_LOOPBACK_IP,
        "interface": "GigabitEthernet0/1",
        "direction": "rx",
        "label": "R1 from R2 Gi0/1 RX",
    },
    "R1_WAN": {
        "host": R1_LOOPBACK_IP,
        "interface": "GigabitEthernet0/0",
        "direction": "tx",
        "label": "R1 WAN Gi0/0 TX",
    },

    # ==========================================================
    # R2 — DSW2, kết nối R1 và WAN DHCP
    # ==========================================================
    "R2_DSW2": {
        "host": R2_LOOPBACK_IP,
        "interface": "GigabitEthernet0/2.99",
        "direction": "rx",
        "label": "R2 from DSW2 Gi0/2.99 RX",
    },
    "R2_R1": {
        "host": R2_LOOPBACK_IP,
        "interface": "GigabitEthernet0/1",
        "direction": "tx",
        "label": "R2 to R1 Gi0/1 TX",
    },
    "R2_WAN": {
        "host": R2_LOOPBACK_IP,
        "interface": "GigabitEthernet0/0",
        "direction": "tx",
        "label": "R2 WAN Gi0/0 TX",
    },
}

# ==============================================================================
# [BƯỚC 3] CREDENTIALS SSH
# ==============================================================================
SSH_USERNAME = os.environ.get("SSH_USERNAME", "")
SSH_PASSWORD = os.environ.get("SSH_PASSWORD", "")
SSH_ENABLE_PASSWORD = os.environ.get("SSH_ENABLE_PASSWORD", "")
SSH_PORT = 22
SSH_TIMEOUT = 10

# ==============================================================================
# [BƯỚC 4] RTSP CAMERA (từ GNS3 Docker CAM01 / CAM02)
# ==============================================================================
# MacBook lắng nghe RTSP trên cổng 8554 (MediaMTX)
# CAM01 trong GNS3 sẽ đẩy về: rtsp://TAILSCALE_MACBOOK_IP:8554/live
RTSP_SERVER_PORT = 8554
RTSP_STREAM_URL_CAM01 = (
    f"rtsp://{TAILSCALE_IP_PI4}:{RTSP_SERVER_PORT}/cam01"
)

RTSP_STREAM_URL_CAM02 = (
    f"rtsp://{TAILSCALE_IP_PI4}:{RTSP_SERVER_PORT}/cam02"
)

RTSP_FALLBACK_SIMULATE = True
RTP_STREAMS = {
    "CAM01": {
        "destination_ip": TAILSCALE_IP_PI4,
        "destination_port": 8000,
        "label": "CAM01 RTP",
    },
    "CAM02": {
        "destination_ip": TAILSCALE_IP_PI4,
        "destination_port": 8002,
        "label": "CAM02 RTP",
    },
}
USB_CAMERA_DEVICE = int(
    os.environ.get(
        "USB_CAMERA_DEVICE",
        "0",
    )
)

LOCAL_CAMERAS = {
    "CAM03": {
        "source": "usb",
        "device": USB_CAMERA_DEVICE,
        "v4l2": True,
        "jpeg_quality": 80,
        "enabled": os.environ.get("CAM03_ENABLED", "1") != "0",
        "label": "CAM03 USB-CAMERA",
    },
    "CAM04": {
        "source": os.environ.get("CAM04_SOURCE", "picamera2"),
        "device": int(os.environ.get("CAM04_DEVICE", "0")),
        "v4l2": False,
        "jpeg_quality": 80,
        "enabled": os.environ.get("CAM04_ENABLED", "1") != "0",
        "label": "CAM04 Pi Camera",
    }
}
# Opt-in RTP from the same capture owner as AI. Encoder never opens /dev/video0.
# Values are validated in the camera worker so encoder configuration errors do not kill AI.
CAM03_RTP_ENABLED = os.environ.get("CAM03_RTP_ENABLED", "0") == "1"
CAM03_RTP_CONFIG = {
    "bitrate_kbps": int(os.environ.get("CAM03_RTP_BITRATE_KBPS","600")),
    "destination": os.environ.get("CAM03_RTP_DESTINATION", "100.108.25.63"),
    "local_address": os.environ.get("CAM03_RTP_LOCAL_ADDRESS", "192.168.10.13"),
    "interface": "eth0",
    "sdp_path": os.environ.get("CAM03_RTP_SDP_PATH", "/tmp/datn-cam03-app.sdp"),
}
AI_CONFIDENCE = 0.5
AI_THREADS = int(os.environ.get("AI_THREADS", "2"))
AI_OBSERVATION_TTL_SEC = float(os.environ.get("AI_OBSERVATION_TTL_SEC", "4.5"))
AI_MAX_BOX_AGE_SEC = float(os.environ.get("AI_MAX_BOX_AGE_SEC", "2.4"))
AI_ACTIVE_RECHECK_SEC = float(os.environ.get("AI_ACTIVE_RECHECK_SEC", "0.4"))
AI_ALERT_HOLD_RECHECK_SEC = float(
    os.environ.get("AI_ALERT_HOLD_RECHECK_SEC", "1.0")
)
AI_IDLE_RECHECK_SEC = float(os.environ.get("AI_IDLE_RECHECK_SEC", "2.5"))
GUI_PREVIEW_MAX_FPS = float(os.environ.get("GUI_PREVIEW_MAX_FPS", "20.0"))
GUI_PREVIEW_MAX_WIDTH = int(os.environ.get("GUI_PREVIEW_MAX_WIDTH", "480"))
GUI_PREVIEW_MAX_HEIGHT = int(os.environ.get("GUI_PREVIEW_MAX_HEIGHT", "360"))
EDGE_MODEL_PATH = os.environ.get("EDGE_MODEL_PATH")

# ==============================================================================
# [BƯỚC 5] CHẾ ĐỘ AI
# ==============================================================================
SIMULATE_AI = True   # True = Mô phỏng (MacBook không có Pi camera)
AI_PERSON_DETECT_PROB = 0.25   # Xác suất giả lập phát hiện người (25%)
CAMERA_INDEX = 0
# ==============================================================================
# [BƯỚC 6] SYSLOG SERVER & TELEMETRY (Tshark)
# ==============================================================================
SYSLOG_LISTEN_IP = "0.0.0.0"   # Lắng nghe tất cả interface
SYSLOG_LISTEN_PORT = 514         # UDP 514 (cần sudo trên Mac)
SYSLOG_FALLBACK_PORT = 5514        # Port thay thế không cần sudo

# Lắng nghe QoS Telemetry từ Tshark trên Macbook
TAILSCALE_INTERFACE = RTP_CAPTURE_INTERFACE     # Card mạng Tailscale (Macbook)
TELEMETRY_PORTS = {
    "8000": {"ID": "CAM01", "IP": "192.168.10.11"},
    "8002": {"ID": "CAM02", "IP": "192.168.10.12"},
}
# Chuyển thành False nếu muốn bắt gói tshark thật (Yêu cầu sudo)
SIMULATE_TELEMETRY = False

# ==============================================================================
# [BƯỚC 7] DEBOUNCE LOGIC (BẤT ĐỐI XỨNG: FAST-ATTACK / SLOW-RELEASE)
# ==============================================================================
ALERT_TRIGGER_SEC = float(os.environ.get("ALERT_TRIGGER_SEC", "0.8"))
ALERT_MIN_CONFIRMATIONS = int(os.environ.get("ALERT_MIN_CONFIRMATIONS", "2"))
RECOVER_TRIGGER_SEC = float(os.environ.get("RECOVER_TRIGGER_SEC", "10.0"))
AI_WATCHDOG_INTERVAL_MS = int(os.environ.get("AI_WATCHDOG_INTERVAL_MS", "500"))

# ==============================================================================
# [BƯỚC 8] BANDWIDTH QoS - LÝ THUYẾT & HỌC THUẬT
# ==============================================================================
# Giá trị kbps dùng chung cho lệnh router, xác minh và phần hiển thị.
# EDGE_QOS_PROFILE=lab-1m chọn 200/700; mặc định standard vẫn 1000/8000.
# Chọn profile không tự thay parent shaper trên router.
QOS_PROFILE = load_qos_profile()
QOS_NORMAL_BW_KBPS = QOS_PROFILE.normal_kbps
QOS_ALERT_BW_KBPS = QOS_PROFILE.alert_kbps
QOS_NORMAL_BW_MBPS = QOS_NORMAL_BW_KBPS / 1000.0
QOS_ALERT_BW_MBPS = QOS_ALERT_BW_KBPS / 1000.0

# ==============================================================================
# [BƯỚC 9] DASHBOARD GUI
# ==============================================================================
DASHBOARD_WIDTH = 1024   # Độ phân giải LCD 7-inch
DASHBOARD_HEIGHT = 600
DASHBOARD_UPDATE_MS = 1000   # Cập nhật mỗi 1 giây

# ==============================================================================
# [BƯỚC 10] MACBOOK → PI 4 MIGRATION FLAG
# ==============================================================================
RUNNING_ON_PI4 = False  # Đổi thành True khi deploy lên Pi 4 thật

# ==============================================================================
# [BƯỚC 11] QOS TELEMETRY THRESHOLDS (NGƯỠNG SUY THOÁI)
# ==============================================================================
# Ngưỡng suy thoái mạng để kích hoạt Auto-Remediation (QoS Policy-map)
QOS_THRESHOLD_LOSS = 5.0       # Tỷ lệ mất gói > 5%
QOS_THRESHOLD_JITTER = 30.0    # Jitter > 30ms
QOS_THRESHOLD_BW_MIN = 0.5     # Băng thông rớt dưới 0.5 Mbps

# Debounce để tránh Flapping (thay đổi trạng thái liên tục)
QOS_DEGRADE_TRIGGER_SEC = 3    # Mạng suy thoái liên tục 3s -> Kích hoạt DEGRADED
QOS_RECOVER_TRIGGER_SEC = 10   # Mạng ổn định liên tục 10s -> Kích hoạt NORMAL


# Telegram photo alerts (disabled until both credentials are configured).
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
CAMERA_LOCATIONS = {
    "CAM03": "Kho tài liệu bảo mật tầng 8",
    "CAM04": os.environ.get("CAM04_LOCATION", "Kho tài liệu mật tầng 9"),
}

