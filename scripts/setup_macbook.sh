#!/bin/bash
# ============================================================================
# setup_macbook.sh — Cài đặt toàn bộ môi trường MacBook thay thế Pi 4
# Đồ án: Nguyễn Tiết Khang (41900966) - TDTU
# Chạy: bash setup_macbook.sh
# ============================================================================

set -e  # Dừng ngay nếu có lỗi

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"

echo ""
echo -e "${CYAN}============================================================${NC}"
echo -e "${CYAN}  DATN TDTU — Setup MacBook as Pi 4 Edge Orchestrator${NC}"
echo -e "${CYAN}  Sinh viên: Nguyễn Tiết Khang (41900966)${NC}"
echo -e "${CYAN}============================================================${NC}"
echo ""

# ─── BƯỚC 1: Kiểm tra Homebrew ───────────────────────────────────────────────
echo -e "${YELLOW}[BƯỚC 1/8] Kiểm tra Homebrew...${NC}"
if ! command -v brew &>/dev/null; then
    echo -e "${RED}  ✗ Homebrew chưa cài. Cài đặt Homebrew...${NC}"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
else
    echo -e "${GREEN}  ✓ Homebrew đã có: $(brew --version | head -1)${NC}"
fi

# ─── BƯỚC 2: Cài Python 3.11+ ────────────────────────────────────────────────
echo -e "${YELLOW}[BƯỚC 2/8] Kiểm tra Python 3.11+...${NC}"
PYTHON_CMD=""
for cmd in python3.12 python3.11 python3; do
    if command -v $cmd &>/dev/null; then
        VER=$($cmd --version 2>&1 | awk '{print $2}')
        MAJOR=$(echo $VER | cut -d. -f1)
        MINOR=$(echo $VER | cut -d. -f2)
        if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 11 ]; then
            PYTHON_CMD=$cmd
            echo -e "${GREEN}  ✓ Python $VER tìm thấy: $cmd${NC}"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo -e "${RED}  ✗ Cần Python 3.11+. Đang cài qua Homebrew...${NC}"
    brew install python@3.11
    PYTHON_CMD="python3.11"
fi

# ─── BƯỚC 3: Cài iPerf3 ──────────────────────────────────────────────────────
echo -e "${YELLOW}[BƯỚC 3/8] Cài iPerf3...${NC}"
if ! command -v iperf3 &>/dev/null; then
    brew install iperf3
    echo -e "${GREEN}  ✓ iPerf3 đã cài${NC}"
else
    echo -e "${GREEN}  ✓ iPerf3 đã có: $(iperf3 --version 2>&1 | head -1)${NC}"
fi

# ─── BƯỚC 4: Kiểm tra Tailscale ──────────────────────────────────────────────
echo -e "${YELLOW}[BƯỚC 4/8] Kiểm tra Tailscale...${NC}"
if ! command -v tailscale &>/dev/null; then
    echo -e "${RED}  ✗ Tailscale chưa cài!${NC}"
    echo -e "  → Tải tại: https://tailscale.com/download/mac"
    echo -e "  → Sau khi cài, chạy lại script này."
    # Không dừng — để tiếp tục cài Python deps
else
    TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || echo "Chưa kết nối")
    echo -e "${GREEN}  ✓ Tailscale IP MacBook: ${TAILSCALE_IP}${NC}"
    echo -e "  → Hãy điền IP này vào config.py: TAILSCALE_MACBOOK_IP"
fi

# ─── BƯỚC 5: Tạo Virtual Environment ─────────────────────────────────────────
echo -e "${YELLOW}[BƯỚC 5/8] Tạo Python Virtual Environment (.venv)...${NC}"
if [ ! -d "$VENV_DIR" ]; then
    $PYTHON_CMD -m venv "$VENV_DIR"
    echo -e "${GREEN}  ✓ .venv tạo thành công tại: $VENV_DIR${NC}"
else
    echo -e "${GREEN}  ✓ .venv đã tồn tại${NC}"
fi

source "$VENV_DIR/bin/activate"
pip install --upgrade pip --quiet

# ─── BƯỚC 6: Cài Python dependencies ─────────────────────────────────────────
echo -e "${YELLOW}[BƯỚC 6/8] Cài Python packages (PyQt5, Netmiko, OpenCV, v.v.)...${NC}"
python -m pip install --quiet -r "$PROJECT_DIR/requirement.txt"

echo -e "${GREEN}  ✓ Tất cả Python packages đã cài${NC}"

# ─── BƯỚC 7: Kiểm tra permission UDP 514 ─────────────────────────────────────
echo -e "${YELLOW}[BƯỚC 7/8] Kiểm tra permission Syslog UDP 514...${NC}"
echo -e "  ℹ️  Trên macOS, lắng nghe cổng < 1024 cần sudo."
echo -e "  → Có 2 cách:"
echo -e "     a) Chạy: sudo python syslog_receiver.py    (port 514 thật)"
echo -e "     b) Dùng port 5514 (không cần sudo) + cấu hình switch: logging port 5514"
echo -e "  → Trong config.py có SYSLOG_FALLBACK_PORT = 5514 nếu không dùng sudo."

# ─── BƯỚC 8: Tạo file cấu hình sudoers cho UDP 514 (tùy chọn) ───────────────
echo -e "${YELLOW}[BƯỚC 8/8] Hoàn tất...${NC}"
echo ""
echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN}  ✅ Môi trường MacBook SẴNG SÀNG thay thế Pi 4!${NC}"
echo -e "${GREEN}============================================================${NC}"
echo ""
echo -e "📋 BƯỚC TIẾP THEO:"
echo -e "  1. Điền IP Tailscale thực tế vào: ${CYAN}config.py${NC}"
echo -e "     Xem IP: ${YELLOW}tailscale status${NC}"
echo -e ""
echo -e "  2. Chạy Syslog receiver (Terminal riêng):"
echo -e "     ${YELLOW}source .venv/bin/activate && python syslog_receiver.py${NC}"
echo -e ""
echo -e "  3. Chạy Dashboard chính:"
echo -e "     ${YELLOW}source .venv/bin/activate && python main_orchestrator.py${NC}"
echo ""
