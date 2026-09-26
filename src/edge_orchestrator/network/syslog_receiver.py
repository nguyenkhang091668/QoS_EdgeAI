#!/usr/bin/env python3
"""
syslog_receiver.py — Bước 13-15 Pipeline: Thu thập & Phân tích Syslog từ GNS3 Switches
Đồ án: Nguyễn Tiết Khang (41900966) - TDTU

Chức năng:
  - Lắng nghe UDP port 514 (hoặc 5514 nếu không có sudo) từ ASW L2 trong GNS3
  - Dùng Regex Parser bóc tách Port Security Violation và DAI ARP Spoofing
  - Phát sự kiện ra queue để Dashboard và AutoRemediation xử lý
  - Có thể chạy độc lập hoặc được import bởi main_orchestrator.py

Chạy độc lập:
  sudo python syslog_receiver.py       # port 514 (thật)
  python syslog_receiver.py --port 5514  # không cần sudo

Cấu hình Switch GNS3 gửi Syslog về MacBook:
  ASW(config)# logging host TAILSCALE_MACBOOK_IP
  ASW(config)# logging trap informational
  ASW(config)# service timestamps log datetime msec
"""

import socket
import re
import time
import queue
import threading
import argparse
import sys
import os
from datetime import datetime

# Đảm bảo python tìm thấy module edge_orchestrator khi chạy độc lập
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))


# Import config (với fallback nếu chạy độc lập)
try:
    from edge_orchestrator.core.config import (
        SYSLOG_LISTEN_IP, SYSLOG_LISTEN_PORT, SYSLOG_FALLBACK_PORT,
        TAILSCALE_MACBOOK_IP
    )
except ImportError:
    SYSLOG_LISTEN_IP       = "0.0.0.0"
    SYSLOG_LISTEN_PORT     = 514
    SYSLOG_FALLBACK_PORT   = 5514
    TAILSCALE_MACBOOK_IP   = "100.108.25.63"

# Import bộ Parser Engine chuẩn hóa
from edge_orchestrator.network.syslog_parser_engine import parse_cisco_syslog


# ─── GLOBAL EVENT QUEUE (để Dashboard đọc) ───────────────────────────────────
syslog_event_queue = queue.Queue(maxsize=200)

# ─── MÀU TERMINAL ────────────────────────────────────────────────────────────
class Colors:
    RED     = "\033[91m"
    YELLOW  = "\033[93m"
    GREEN   = "\033[92m"
    CYAN    = "\033[96m"
    BLUE    = "\033[94m"
    RESET   = "\033[0m"
    BOLD    = "\033[1m"

def parse_syslog_message(raw: str, source_ip: str) -> dict | None:
    """
    Phân tích raw syslog message bằng Engine chuẩn hóa, trả về dict sự kiện.
    """
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    parsed_result = parse_cisco_syslog(raw)
    
    # Lấy tên thiết bị từ IP (sẽ bị đè nếu NAT)
    device_name = DEVICE_MAP.get(source_ip, source_ip)
    
    # Xử lý NAT: Bóc tách Hostname trực tiếp từ nội dung Log (nếu Cisco có cấu hình 'logging origin-id hostname')
    # Mẫu: "<189>141: DSW2: *Sep 20 ..." -> Trích xuất chữ 'DSW2'
    import re
    host_match = re.search(r'(?:<\d+>)?(?:\d+:\s+)?([A-Za-z0-9_-]+):\s+(?:\*)?(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)', raw)
    if host_match:
        device_name = host_match.group(1).upper()
        
    return {
        "timestamp": timestamp,
        "source_ip": source_ip,
        "device_name": device_name,
        "type": parsed_result["event_type"],
        "severity": parsed_result["severity"],
        "details": parsed_result["details"],
        "raw": raw.strip(),
        "data": parsed_result["data"],
    }

# Bản đồ IP tĩnh (User sẽ điền IP thật của GNS3 vào đây)
DEVICE_MAP = {
    # --- CORE ROUTER R1 --- 
    "192.168.99.254": "R1",   # Subinterface Gi0/2.99 
    "10.255.255.1": "R1",     # Loopback0 R1 
    "192.168.122.197": "R1",  # WAN Gi0/0 R1 

    # --- CORE ROUTER R2 --- 
    "192.168.99.253": "R2",   # Subinterface Gi0/2.99 
    "10.255.255.2": "R2",     # Loopback0 R2 
    "192.168.122.48": "R2",   # WAN Gi0/0 R2 

    # --- DISTRIBUTION SWITCH DSW1 --- 
    "192.168.99.2": "DSW1",   # SVI Vlan99 DSW1 (Nguồn Syslog chuẩn) 
    "10.255.255.11": "DSW1",  # Loopback0 DSW1 
    "192.168.10.2": "DSW1",   # SVI Vlan10 
    "192.168.20.2": "DSW1",   # SVI Vlan20 

    # --- DISTRIBUTION SWITCH DSW2 --- 
    "192.168.99.3": "DSW2",   # SVI Vlan99 DSW2 (Nguồn Syslog chuẩn) 
    "10.255.255.12": "DSW2",  # Loopback0 DSW2 
    "192.168.10.3": "DSW2",   # SVI Vlan10 
    "192.168.20.3": "DSW2",   # SVI Vlan20 

    # --- ACCESS SWITCH ASW --- 
    "192.168.99.100": "ASW",  # SVI Vlan99 ASW 
}

def format_event_display(event: dict) -> tuple[str, str]:
    """Trả về (message hiển thị, màu terminal)."""
    t    = event["timestamp"]
    sev  = event["severity"]
    device_name = event.get("device_name", event["source_ip"])
    
    if sev in ["EMERGENCY", "CRITICAL", "ALERT"]:
        color = Colors.RED
    elif sev == "WARNING":
        color = Colors.YELLOW
    elif sev == "INFO":
        color = Colors.GREEN
    else:
        color = Colors.BLUE

    if event["type"] == "UNKNOWN_SYS_LOG":
        msg = f"[{t}] [SYSLOG] [{device_name}]: {event['raw'][:120]}"
    else:
        # Thay icon bằng Tên Thiết Bị
        msg = f"[{t}] [{device_name}] [{sev}] {event['type']} -> {event['details']}"

    return msg, color

def inject_simulated_syslog():
    """
    GIẢ LẬP Syslog từ GNS3 khi không có hạ tầng thật.
    Tạo các sự kiện mẫu theo đúng định dạng Cisco IOS.
    Chạy trong background thread.
    """
    simulated_messages = [
        ("10.255.255.11", "*Sep 18 05:18:23.239: %HSRP-5-STATECHANGE: Vlan10 Grp 10 state Standby -> Active"),
        ("10.255.255.1",  "*Sep 18 05:18:45.455: %OSPF-5-ADJCHG: Process 1, Nbr 10.255.255.1 on Vlan10 from FULL to DOWN, Neighbor Down: Dead timer expired"),
        ("10.255.255.12", "*Sep 18 05:19:31.852: %EC-5-L3DONTBNDL2: Gi0/3 suspended: LACP currently not enabled on the remote port."),
        ("192.168.99.100", "%PORT_SECURITY-2-PSECURE_VIOLATION: Security violation occurred, caused by MAC address 0242.59cf.2200 on port GigabitEthernet0/2"),
        ("192.168.99.100", "%SW_DAI-4-DHCP_SNOOPING_DENY: 1 Packets Denied, slot: 0, port: Gi0/2, vlan 10, Source IP: 192.168.10.55, Source MAC: 0242.77a5.3000"),
        ("192.168.99.100", "%CDP-4-NATIVE_VLAN_MISMATCH: Native VLAN mismatch discovered on GigabitEthernet0/2 (1), with ASW.tdtu.edu.vn GigabitEthernet0/1 (99)."),
        ("192.168.99.100", "%SPANTREE-2-UNBLOCK_CONSIST_PORT: Unblocking GigabitEthernet0/1 on VLAN0099. Port consistency restored."),
        ("192.168.99.100", "%PM-4-ERR_DISABLE: link-flap error detected on GigabitEthernet0/2")
    ]

    import random
    while True:
        time.sleep(random.uniform(8, 20))  # Gửi event ngẫu nhiên mỗi 8-20 giây
        src_ip, msg = random.choice(simulated_messages)
        event = parse_syslog_message(msg, src_ip)
        if event:
            try:
                syslog_event_queue.put_nowait(event)
            except queue.Full:
                pass
            display_msg, color = format_event_display(event)
            print(f"{color}{display_msg}{Colors.RESET}")

def start_syslog_server(port: int = None, simulate: bool = False):
    """
    Khởi động UDP Syslog Server.
    simulate=True: Chạy thread giả lập thay vì lắng nghe UDP thật.
    """
    actual_port = port or SYSLOG_LISTEN_PORT

    print(f"\n{Colors.CYAN}{'='*60}{Colors.RESET}")
    print(f"{Colors.BOLD}  SYSLOG RECEIVER — Bước 13-15 Pipeline DATN{Colors.RESET}")
    print(f"{Colors.CYAN}{'='*60}{Colors.RESET}")

    if simulate:
        print(f"{Colors.YELLOW}  Chế độ: GIẢ LẬP (không cần GNS3 thật){Colors.RESET}")
        print(f"  → Đang tạo sự kiện mô phỏng Cisco IOS Syslog...")
        sim_thread = threading.Thread(target=inject_simulated_syslog, daemon=True)
        sim_thread.start()
        print(f"{Colors.GREEN}  ✓ Syslog Simulator đang chạy...{Colors.RESET}\n")
        return sim_thread

    # Chạy UDP server thật
    print(f"  Lắng nghe: {SYSLOG_LISTEN_IP}:{actual_port} (UDP)")
    print(f"  MacBook Tailscale IP: {TAILSCALE_MACBOOK_IP}")
    print(f"\n  {Colors.YELLOW}Cấu hình trên Cisco Switch trong GNS3:{Colors.RESET}")
    print(f"    logging host {TAILSCALE_MACBOOK_IP}")
    print(f"    logging trap informational")
    print(f"    service timestamps log datetime msec")
    if actual_port != 514:
        print(f"    logging discriminator DATN severity includes 2-6")
    print()

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((SYSLOG_LISTEN_IP, actual_port))
        print(f"{Colors.GREEN}  ✓ UDP Socket bind thành công tại port {actual_port}{Colors.RESET}")
        print(f"  Đang lắng nghe Syslog... (Ctrl+C để dừng)\n")
    except PermissionError:
        print(f"{Colors.RED}  ✗ Permission denied cho port {actual_port}!{Colors.RESET}")
        print(f"  Thử 1: sudo python syslog_receiver.py")
        print(f"  Thử 2: python syslog_receiver.py --port {SYSLOG_FALLBACK_PORT}")
        sys.exit(1)
    except OSError as e:
        print(f"{Colors.RED}  ✗ Lỗi bind socket: {e}{Colors.RESET}")
        sys.exit(1)

    def udp_listener():
        while True:
            try:
                data, addr = sock.recvfrom(4096)
                raw_msg = data.decode("utf-8", errors="replace")
                source_ip = addr[0]

                event = parse_syslog_message(raw_msg, source_ip)
                if event:
                    try:
                        syslog_event_queue.put_nowait(event)
                    except queue.Full:
                        pass  # Queue đầy, bỏ qua

                    display_msg, color = format_event_display(event)
                    print(f"{color}{display_msg}{Colors.RESET}")

            except Exception as e:
                print(f"{Colors.RED}  [ERROR] Syslog recv: {e}{Colors.RESET}")

    listen_thread = threading.Thread(target=udp_listener, daemon=True, name="SyslogUDPListener")
    listen_thread.start()
    return listen_thread

# ─── CHẠY ĐỘC LẬP ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DATN Syslog Receiver")
    parser.add_argument("--port", type=int, default=None, help="UDP port (default: 514, fallback: 5514)")
    parser.add_argument("--simulate", action="store_true", help="Chạy chế độ giả lập (không cần GNS3)")
    args = parser.parse_args()

    use_port    = args.port or SYSLOG_LISTEN_PORT
    use_simulate = args.simulate

    # Nếu không truyền --port và không phải root, tự động dùng fallback port
    if not args.port and not use_simulate:
        try:
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            test_sock.bind(("127.0.0.1", 514))
            test_sock.close()
        except PermissionError:
            print(f"{Colors.YELLOW}  ⚠ Không có quyền port 514. Chuyển sang port {SYSLOG_FALLBACK_PORT}.{Colors.RESET}")
            print(f"  Hoặc chạy: sudo python syslog_receiver.py")
            use_port = SYSLOG_FALLBACK_PORT

    thread = start_syslog_server(port=use_port, simulate=use_simulate)

    # Vòng lặp chính: in events từ queue
    print(f"{Colors.CYAN}  [Queue Monitor] Đang monitor syslog_event_queue...{Colors.RESET}")
    try:
        while True:
            try:
                event = syslog_event_queue.get(timeout=2)
                # Khi chạy độc lập, event đã được in trong thread
                # Chỉ print nếu từ queue (duplicate check đơn giản)
            except queue.Empty:
                pass
            time.sleep(0.1)
    except KeyboardInterrupt:
        print(f"\n{Colors.CYAN}  [SYSLOG] Đã dừng.{Colors.RESET}")
