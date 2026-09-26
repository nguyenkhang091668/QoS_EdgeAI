#!/usr/bin/env python3
"""
config.py — Cấu hình tập trung cho toàn bộ Pipeline DATN
Đồ án: Nguyễn Tiết Khang (41900966) - TDTU
Hướng dẫn: Điền IP thực tế Tailscale của bạn trước khi chạy.
"""

import os

# ==============================================================================
# [BƯỚC 1] ĐIỀN IP TAILSCALE THỰC TẾ CỦA BẠN VÀO ĐÂY
# Lệnh kiểm tra: tailscale status
# ==============================================================================
TAILSCALE_MACBOOK_IP = "100.108.25.63"    # IP Tailscale MacBook (thay Pi 4)
TAILSCALE_GNS3_IP = "100.106.252.93"    # IP Tailscale GNS3 Server Ubuntu

# ==============================================================================
# [BƯỚC 2] IP THIẾT BỊ MẠNG TRONG GNS3 (Loopback & VLAN99)
# ==============================================================================
R1_LOOPBACK_IP = "10.255.255.1"    # Core Router R1 Loopback 0
R2_LOOPBACK_IP = "10.255.255.2"    # Core Router R2 Loopback 0
DSW1_LOOPBACK_IP = "10.255.255.11"   # Distribution Switch DSW1 Loopback 0
DSW2_LOOPBACK_IP = "10.255.255.12"   # Distribution Switch DSW2 Loopback 0
ASW_VLAN99_IP = "192.168.99.100"  # Access Switch VLAN 99 Management IP

# Danh sách tất cả thiết bị Netmiko SSH
NETWORK_DEVICES = [
    {"name": "R1",   "ip": R1_LOOPBACK_IP,   "device_type": "cisco_ios"},
    {"name": "R2",   "ip": R2_LOOPBACK_IP,   "device_type": "cisco_ios"},
    {"name": "DSW1", "ip": DSW1_LOOPBACK_IP, "device_type": "cisco_ios"},
    {"name": "DSW2", "ip": DSW2_LOOPBACK_IP, "device_type": "cisco_ios"},
    {"name": "ASW",  "ip": ASW_VLAN99_IP,    "device_type": "cisco_ios"},
]

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
RTSP_STREAM_URL_CAM01 = f"rtsp://{TAILSCALE_MACBOOK_IP}:{RTSP_SERVER_PORT}/cam01"
RTSP_STREAM_URL_CAM02 = f"rtsp://{TAILSCALE_MACBOOK_IP}:{RTSP_SERVER_PORT}/cam02"
RTSP_FALLBACK_SIMULATE = True   # True = Mô phỏng camera khi không có GNS3 thật

# ==============================================================================
# [BƯỚC 5] CHẾ ĐỘ AI
# ==============================================================================
SIMULATE_AI = True   # True = Mô phỏng (MacBook không có Pi camera)
# False = Dùng YOLOv8 thật (khi chuyển sang Pi 4)
AI_PERSON_DETECT_PROB = 0.25   # Xác suất giả lập phát hiện người (25%)

# ==============================================================================
# [BƯỚC 6] SYSLOG SERVER & TELEMETRY (Tshark)
# ==============================================================================
SYSLOG_LISTEN_IP = "0.0.0.0"   # Lắng nghe tất cả interface
SYSLOG_LISTEN_PORT = 514         # UDP 514 (cần sudo trên Mac)
SYSLOG_FALLBACK_PORT = 5514        # Port thay thế không cần sudo

# Lắng nghe QoS Telemetry từ Tshark trên Macbook
TAILSCALE_INTERFACE = "utun4"     # Card mạng Tailscale (Macbook)
TELEMETRY_PORTS = {
    "8000": {"ID": "CAM01", "IP": "192.168.10.11"},
    "8002": {"ID": "CAM02", "IP": "192.168.10.12"},
    "8004": {"ID": "CAM03", "IP": "192.168.10.13"},
    "8006": {"ID": "CAM04", "IP": "192.168.10.14"}
}
# Chuyển thành False nếu muốn bắt gói tshark thật (Yêu cầu sudo)
SIMULATE_TELEMETRY = False

# ==============================================================================
# [BƯỚC 7] DEBOUNCE LOGIC
# ==============================================================================
ALERT_TRIGGER_SECONDS = 3    # Phát hiện người ≥ 3 giây → ALERT
NORMAL_TRIGGER_SECONDS = 10   # Không phát hiện ≥ 10 giây → NORMAL

# ==============================================================================
# [BƯỚC 8] BANDWIDTH QoS - LÝ THUYẾT & HỌC THUẬT
# ==============================================================================
# Cơ chế chuyển trạng thái Dynamic QoS (1 Mbps <-> 8 Mbps) được điều khiển
# theo 2 trạng thái băng thông của Decision Engine:
#
# 1. Trạng thái NORMAL (Bình thường / IDLE - NO_PERSON):
QOS_NORMAL_BW_MBPS = 1

# 2. Trạng thái ALERT (Khẩn cấp - PERSON_DETECTED):

QOS_ALERT_BW_MBPS = 8

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
