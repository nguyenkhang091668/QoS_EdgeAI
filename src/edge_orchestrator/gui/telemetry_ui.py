import sys
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, 
    QTableWidgetItem, QHeaderView, QApplication
)
from PyQt5.QtCore import Qt, QTimer
import matplotlib
matplotlib.use("Qt5Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
import random
import time

class QoSTelemetryUI(QWidget):
    """
    Giao diện độc lập hiển thị Đồ thị Băng thông và Bảng thông số QoS.
    Hỗ trợ Dynamic Data-Driven (Tự động thêm thiết bị mới).
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("QoS Telemetry Monitor - Live Data")
        self.resize(800, 440)
        
        # Dữ liệu nội bộ để vẽ đồ thị
        self.max_history = 30
        self.history_data = {}
        self.init_ui()

    def init_ui(self):
        main_layout = QHBoxLayout(self)
        
        # --- Phần 1: Đồ thị Băng thông (Bên trái) ---
        self.figure, self.ax = plt.subplots(figsize=(6, 4))
        self.canvas = FigureCanvas(self.figure)
        main_layout.addWidget(self.canvas, stretch=2)
        
        # Cấu hình đồ thị cơ bản
        self.ax.set_title("Real-time Bandwidth (Mbps)")
        self.ax.set_xlabel("Time (ticks)")
        self.ax.set_ylabel("Bitrate (Mbps)")
        self.ax.grid(True, linestyle="--", alpha=0.6)
        self.figure.tight_layout()

        # --- Phần 2: Bảng thông số QoS (Bên phải) ---
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "Device ID", "IP", "Port", "Bitrate (Mbps)", "Loss (%)", "Jitter (ms)"
        ])
        # Thiết lập cột giãn đều
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        
        main_layout.addWidget(self.table, stretch=1)

    def update_telemetry_data(self, metrics_list):
        """
        Nhận dữ liệu từ QoSTelemetryWorker và cập nhật UI.
        metrics_list: list of dicts [{'ID': 'CAM01', 'Bitrate_Mbps': 1.1, ...}, ...]
        """
        # 1. Cập nhật Bảng (Table)
        self.table.setRowCount(len(metrics_list))
        for row, metric in enumerate(metrics_list):
            device_id = metric.get("ID", f"Unknown-{row}")
            self.table.setItem(row, 0, QTableWidgetItem(str(device_id)))
            self.table.setItem(row, 1, QTableWidgetItem(str(metric.get("IP", ""))))
            self.table.setItem(row, 2, QTableWidgetItem(str(metric.get("Port", ""))))
            self.table.setItem(row, 3, QTableWidgetItem(f"{metric.get('Bitrate_Mbps', 0):.2f}"))
            self.table.setItem(row, 4, QTableWidgetItem(f"{metric.get('Loss_Percent', 0):.1f}"))
            self.table.setItem(row, 5, QTableWidgetItem(f"{metric.get('Jitter_ms', 0):.1f}"))
            
            # 2. Cập nhật Dữ liệu Đồ thị
            if device_id not in self.history_data:
                self.history_data[device_id] = {"times": [], "bitrates": []}
                
            history = self.history_data[device_id]
            
            # Dùng timestamp nếu có, không thì đếm số frame
            current_time = metric.get("Timestamp", time.strftime("%H:%M:%S"))
            
            history["times"].append(current_time)
            history["bitrates"].append(metric.get("Bitrate_Mbps", 0))
            
            # Cắt bớt dữ liệu cũ nếu vượt quá giới hạn
            if len(history["times"]) > self.max_history:
                history["times"].pop(0)
                history["bitrates"].pop(0)

        # 3. Vẽ lại đồ thị
        self.ax.clear()
        self.ax.set_title("Real-time Bandwidth (Mbps)")
        self.ax.set_xlabel("Time (last 60 ticks)")
        self.ax.set_ylabel("Bitrate (Mbps)")
        self.ax.grid(True, linestyle="--", alpha=0.6)

        # Vẽ từng đường của từng thiết bị
        for device_id, history in self.history_data.items():
            # Thay vì hiển thị string time dài dòng, ta vẽ theo index
            x_data = range(len(history["bitrates"]))
            self.ax.plot(x_data, history["bitrates"], marker='o', markersize=4, label=device_id)

        # Hiển thị Legend
        self.ax.legend(loc="upper left")
        self.figure.tight_layout()
        self.canvas.draw()


# ==========================================
# KHỐI CHẠY ĐỘC LẬP TÍCH HỢP CI/CD (LẤY DỮ LIỆU THẬT TỪ TSHARK)
# ==========================================
if __name__ == "__main__":
    # Import trực tiếp Worker từ file qos_telemetry
    from edge_orchestrator.network.qos_telemetry import QoSTelemetryWorker

    app = QApplication(sys.argv)
    window = QoSTelemetryUI()
    window.show()

    # KHỞI TẠO WORKER: Bắt buộc truyền simulate=False để ép lấy luồng dữ liệu từ tshark thay vì random
    worker = QoSTelemetryWorker(simulate=False)
    
    # KẾT NỐI: Tín hiệu telemetry_signal từ Worker (List các Dictionary QoS) -> Hàm update giao diện
    worker.telemetry_signal.connect(window.update_telemetry_data)
    
    # Khởi động luồng chạy ngầm của Worker để liên tục quét card mạng
    worker.start()

    sys.exit(app.exec_())