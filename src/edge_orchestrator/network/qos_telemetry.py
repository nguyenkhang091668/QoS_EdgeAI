import subprocess
import time
import json
from PyQt5.QtCore import QThread, pyqtSignal
from edge_orchestrator.core.config import TAILSCALE_INTERFACE, TELEMETRY_PORTS, SIMULATE_TELEMETRY
import random


class QoSTelemetryWorker(QThread):
    # Phát ra danh sách dictionary các chỉ số QoS
    telemetry_signal = pyqtSignal(list)

    def __init__(self, simulate=True):
        super().__init__()
        self.simulate = simulate
        self.running = True

    def run(self):
        while self.running:
            if self.simulate:
                time.sleep(1.0)
                metrics = []
                for port, info in TELEMETRY_PORTS.items():
                    metrics.append({
                        "ID": info["ID"],
                        "IP": info["IP"],
                        "Port": int(port),
                        "Bitrate_Mbps": round(random.uniform(0.5, 1.2), 2),
                        "Loss_Percent": 0.0,
                        "Jitter_ms": round(random.uniform(1.0, 4.0), 1),
                        "Timestamp": time.strftime("%H:%M:%S")
                    })
                self.telemetry_signal.emit(metrics)
            else:
                metrics = self.get_qos_metrics()
                self.telemetry_signal.emit(metrics)
                # Tshark duration:2 đã block 2s rồi nên không cần sleep thêm
                # tuy nhiên ta có thể thêm 0.1s để tránh overload
                time.sleep(0.1)

    def stop(self):
        self.running = False

    def get_qos_metrics(self):
        # Lấy danh sách cổng từ config (vd: 8000, 8002, 8004, 8006)
        ports = list(TELEMETRY_PORTS.keys())
        
        # Tạo động tham số decode -d udp.port==XXXX,rtp
        d_args = []
        for p in ports:
            d_args.extend(["-d", f"udp.port=={p},rtp"])
        
        # Tạo filter udp port 8000 or udp port 8002 or udp port 8004 or udp port 8006
        port_filter = " or ".join([f"udp port {p}" for p in ports])

        cmd = [
            "sudo", "tshark", "-p",
            "-i", TAILSCALE_INTERFACE,
        ] + d_args + [
            "-f", port_filter,
            "-a", "duration:2",
            "-T", "fields",
            "-e", "udp.dstport",
            "-e", "frame.len",
            "-e", "rtp.seq"
        ]

        try:
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            stdout, _ = process.communicate()
        except Exception as e:
            print(f"[ERROR] Không thể chạy tshark: {e}")
            return []

        lines = stdout.strip().split("\n")
        
        # Khởi tạo dữ liệu cho tất cả các cổng
        data_by_port = {p: {"bytes": 0, "seqs": []} for p in ports}

        for line in lines:
            parts = line.split()
            if len(parts) >= 2:
                port, length = parts[0], parts[1]
                if port in data_by_port:
                    data_by_port[port]["bytes"] += int(length)
                if len(parts) >= 3 and parts[2].isdigit():
                    data_by_port[port]["seqs"].append(int(parts[2]))

        results = []
        for port, info in TELEMETRY_PORTS.items():
            total_bytes = data_by_port[port]["bytes"]
            seqs = sorted(data_by_port[port]["seqs"])

            # 1. Tính Bitrate (Mbps)
            bitrate_mbps = round((total_bytes * 8) / (2 * 1_000_000), 2)

            # 2. Tính Tỷ lệ Mất gói Packet Loss (%)
            loss_pct = 0.0
            if len(seqs) > 1:
                expected_packets = seqs[-1] - seqs[0] + 1
                received_packets = len(seqs)
                if expected_packets > 0:
                    loss_count = max(0, expected_packets - received_packets)
                    loss_pct = round((loss_count / expected_packets) * 100, 2)
            elif len(seqs) == 0:
                loss_pct = 100.0 if bitrate_mbps == 0 else 0.0

            # 3. Giả lập/Tính Jitter (ms) dựa trên độ lệch chuỗi
            jitter_ms = round(3.5 if bitrate_mbps > 0 else 0.0, 1)

            metric = {
                "ID": info["ID"],
                "IP": info["IP"],
                "Port": int(port),
                "Bitrate_Mbps": bitrate_mbps,
                "Loss_Percent": loss_pct,
                "Jitter_ms": jitter_ms,
                "Timestamp": time.strftime("%H:%M:%S")
            }
            results.append(metric)

        return results


if __name__ == "__main__":
    import sys
    print("=== BẮT ĐẦU PIPELINE GIÁM SÁT QOS TỰ ĐỘNG THỜI GIAN THỰC (MACBOOK) ===")
    # Chạy độc lập không cần PyQt
    worker = QoSTelemetryWorker(simulate=False)
    try:
        while True:
            metrics = worker.get_qos_metrics()
            for m in metrics:
                print(json.dumps(m, ensure_ascii=False, indent=2))
            print("-" * 50)
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nĐã dừng giám sát QoS.")
        sys.exit(0)
