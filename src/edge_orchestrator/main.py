#!/usr/bin/env python3
import sys
import argparse
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QColor, QPalette

from edge_orchestrator.gui.dashboard import MainDashboard
from edge_orchestrator.network.syslog_receiver import start_syslog_server

def main():
    parser = argparse.ArgumentParser(
        description="DATN Main Orchestrator — MacBook thay thế Raspberry Pi 4"
    )
    parser.add_argument("--gns3", action="store_true",
                        help="Kết nối tới GNS3 thật (Syslog UDP + Netmiko SSH thật)")
    parser.add_argument("--no-sim", action="store_true",
                        help="Tắt hoàn toàn mô phỏng (cần cơ sở hạ tầng thật)")
    parser.add_argument("--port", type=int, default=None,
                        help="Syslog UDP port (default: auto-detect 514 hoặc 5514)")
    args = parser.parse_args()

    sim_ai      = not args.no_sim  # Mô phỏng AI trừ khi --no-sim
    sim_netmiko = not args.gns3    # Mô phỏng Netmiko trừ khi --gns3
    sim_syslog  = not args.gns3    # Mô phỏng Syslog trừ khi --gns3
    
    # Ghi đè cấu hình Telemetry
    import edge_orchestrator.core.config as config
    config.SIMULATE_TELEMETRY = sim_ai

    print("\n" + "="*65)
    print("  DATN TDTU — MacBook Edge Orchestrator (thay thế Raspberry Pi 4)")
    print(f"  Sinh viên: Nguyễn Tiết Khang | MSSV: 41900966")
    print("="*65)
    print("="*65)
    print(f"  Chế độ AI:        {'Mô phỏng' if sim_ai else 'YOLOv8 Thật'}")
    print(f"  Chế độ Netmiko:   {'Mô phỏng' if sim_netmiko else 'SSH Thật → GNS3'}")
    print(f"  Chế độ Syslog:    {'Mô phỏng' if sim_syslog else 'UDP Thật từ GNS3'}")
    print(f"  Chế độ Telemetry: {'Mô phỏng' if sim_ai else 'Tshark Thật (yêu cầu sudo)'}")
    print("="*65 + "\n")

    # Khởi động Syslog Receiver trong background
    start_syslog_server(port=args.port, simulate=sim_syslog)

    # Khởi động GUI
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Dark palette
    palette = QPalette()
    palette.setColor(QPalette.Window,          QColor("#0d1117"))
    palette.setColor(QPalette.WindowText,      QColor("#ffffff"))
    palette.setColor(QPalette.Base,            QColor("#161b22"))
    palette.setColor(QPalette.AlternateBase,   QColor("#0d1117"))
    palette.setColor(QPalette.Text,            QColor("#e6edf3"))
    palette.setColor(QPalette.Button,          QColor("#21262d"))
    palette.setColor(QPalette.ButtonText,      QColor("#ffffff"))
    palette.setColor(QPalette.Highlight,       QColor("#1f6feb"))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)

    dashboard = MainDashboard(
        simulate_ai=sim_ai,
        simulate_netmiko=sim_netmiko,
        simulate_syslog=sim_syslog,
    )
    dashboard.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
