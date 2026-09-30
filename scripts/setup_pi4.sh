#!/bin/bash
# ============================================================================
# setup_pi4.sh — Script migrate và cài môi trường trên Raspberry Pi 4 thật
# Đồ án: Nguyễn Tiết Khang (41900966) - TDTU
# Chạy trên Pi 4: bash setup_pi4.sh
# ============================================================================
set -e

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
RED='\033[0;31m'; NC='\033[0m'; BOLD='\033[1m'

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
INSTALL_USER="${SUDO_USER:-$(id -un)}"
INSTALL_GROUP="$(id -gn "$INSTALL_USER")"

echo ""
echo -e "${CYAN}============================================================${NC}"
echo -e "${CYAN}  DATN TDTU — Setup Raspberry Pi 4 (Deployment Mode)${NC}"
echo -e "${CYAN}  Sinh viên: Nguyễn Tiết Khang (41900966)${NC}"
echo -e "${CYAN}============================================================${NC}"
echo ""

# ─── BƯỚC 1: Cập nhật hệ thống ───────────────────────────────────────────────
echo -e "${YELLOW}[BƯỚC 1/9] Cập nhật apt packages...${NC}"
sudo apt-get update -qq
echo -e "${GREEN}  ✓ System updated${NC}"

# ─── BƯỚC 2: Cài các dependencies hệ thống ───────────────────────────────────
# Các dependency dành riêng cho Pi được cài qua requirement_pi.txt.
echo -e "${YELLOW}[BƯỚC 2/9] Cài system dependencies...${NC}"
sudo apt-get install -y \
    python3-pip python3-venv python3-dev \
    libopenblas-dev libopenjp2-7 libtiff6 \
    libavcodec-dev libavformat-dev libswscale-dev \
    libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
    libgl1 libglib2.0-0t64 \
    iperf3 \
    net-tools curl wget git
echo -e "${GREEN}  ✓ System dependencies cài xong${NC}"

# ─── BƯỚC 3: Cài PyQt5 cho Pi (cần cài từ apt, không phải pip) ──────────────
echo -e "${YELLOW}[BƯỚC 3/9] Cài PyQt5 cho Raspberry Pi...${NC}"
sudo apt-get install -y python3-pyqt5 python3-pyqt5.qtsvg
echo -e "${GREEN}  ✓ PyQt5 cài xong${NC}"

# ─── BƯỚC 4: Tạo Virtual Environment ─────────────────────────────────────────
echo -e "${YELLOW}[BƯỚC 4/9] Tạo Python venv (với system-packages)...${NC}"
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR" --system-site-packages
    echo -e "${GREEN}  ✓ .venv tạo với system-site-packages (dùng lại PyQt5 hệ thống)${NC}"
else
    echo -e "${GREEN}  ✓ .venv đã tồn tại${NC}"
fi
source "$VENV_DIR/bin/activate"
pip install --upgrade pip --quiet

# ─── BƯỚC 5: Cài Python packages ─────────────────────────────────────────────
echo -e "${YELLOW}[BƯỚC 5/9] Cài Python packages...${NC}"
python -m pip install --no-cache-dir -r "$PROJECT_DIR/requirement_pi.txt"

# Các dependency dành riêng cho Pi được cài qua requirement_pi.txt.

echo -e "${GREEN}  ✓ Python packages cài xong${NC}"

# ─── BƯỚC 6: Cài YOLOv8 nano (Edge AI thật) ──────────────────────────────────
echo -e "${YELLOW}[BƯỚC 6/9] Cài Ultralytics YOLOv8 (Edge AI thật)...${NC}"
# Ultralytics và backend NCNN được cài qua requirement_pi.txt.
echo -e "${GREEN}  ✓ YOLOv8 cài xong${NC}"

# ─── BƯỚC 7: Cài Tailscale ───────────────────────────────────────────────────
echo -e "${YELLOW}[BƯỚC 7/9] Cài Tailscale VPN...${NC}"
if ! command -v tailscale &>/dev/null; then
    curl -fsSL https://tailscale.com/install.sh | sh
    echo -e "${GREEN}  ✓ Tailscale cài xong${NC}"
    echo -e "  → Chạy: sudo tailscale up --authkey=<YOUR_AUTH_KEY>"
else
    TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || echo "Chưa kết nối")
    echo -e "${GREEN}  ✓ Tailscale đã có | IP: ${TAILSCALE_IP}${NC}"
fi

# ─── BƯỚC 8: Cấu hình Syslog UDP 514 không cần sudo ─────────────────────────
echo -e "${YELLOW}[BƯỚC 8/9] Cấu hình systemd service...${NC}"
# Cấp riêng quyền bind UDP 514 thông qua systemd capability

# Tạo systemd service để tự khởi động
cat <<EOF | sudo tee /etc/systemd/system/datn-orchestrator.service > /dev/null
[Unit]
Description=DATN Edge Orchestrator — Nguyen Tiet Khang 41900966
After=network-online.target tailscaled.service display-manager.service
Wants=network-online.target

[Service]
User=${INSTALL_USER}
Group=${INSTALL_GROUP}
WorkingDirectory=${PROJECT_DIR}
Environment="DISPLAY=:0"
Environment="PYTHONPATH=${PROJECT_DIR}/src"
Environment="EDGE_MODEL_PATH=${PROJECT_DIR}/src/edge_orchestrator/Edge/yolov8n_ncnn_model"
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
ExecStart=${VENV_DIR}/bin/python -m edge_orchestrator.main --gns3 --no-sim
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable datn-orchestrator.service
echo -e "${GREEN}  ✓ Systemd service đã tạo và enable${NC}"

# ─── B 9ƯỚC Triền kse) ─────────────────────────
echo -e "${YELLOW}[BƯỚC 9/9] Xác nhận chế độ triển khai thật...${NC}"
echo -e "${GREEN}  ✓ AI, Netmiko và Syslog thật được bật bằng --gns3 --no-sim${NC}"
echo -e "${GREEN}  ✓ Model NCNN được truyền bằng EDGE_MODEL_PATH${NC}"

# ─── HOÀN TẤT ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN}   Raspberry Pi 4 SẴN SÀNG!${NC}"
echo -e "${GREEN}============================================================${NC}"
echo ""
echo -e " BƯỚC TIẾP THEO:"
echo -e "  1. Kết nối Tailscale: ${YELLOW}sudo tailscale up${NC}"
echo -e "  2. Cắm màn hình LCD 7-inch vào HDMI/DSI"
echo -e "  3. Khởi động service:"
echo -e "     ${YELLOW}sudo systemctl start datn-orchestrator.service${NC}"
echo -e "  4. Xem log:"
echo -e "     ${YELLOW}journalctl -u datn-orchestrator.service -f${NC}"
echo ""
echo -e "  Hoặc chạy thủ công:"
echo -e "     ${YELLOW}EDGE_MODEL_PATH=\"${PROJECT_DIR}/src/edge_orchestrator/Edge/yolov8n_ncnn_model\" PYTHONPATH=src .venv/bin/python -m edge_orchestrator.main --gns3 --no-sim${NC}"
echo ""
