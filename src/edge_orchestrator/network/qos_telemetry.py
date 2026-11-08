import subprocess
import threading
import time
import json
from PyQt5.QtCore import QThread, pyqtSignal
from edge_orchestrator.core.config import (
    BANDWIDTH_POLL_INTERVAL_SEC,
    SIMULATE_TELEMETRY,
    TAILSCALE_INTERFACE,
    TELEMETRY_PORTS,
    TAILSCALE_IP_PI4,
)


class QoSTelemetryWorker(QThread):
    # Phát ra danh sách dictionary các chỉ số QoS
    telemetry_signal = pyqtSignal(list)
    collector_error_signal = pyqtSignal(str)

    # Tái khởi động nhẹ phiên tshark mỗi 5 phút (thay vì mỗi 2 giây -> giảm 150x số lần fork)
    # để giải phóng bộ đệm tạm của dumpcap/wireshark khi chạy 24/7.
    STREAM_SESSION_DURATION_SEC = 300

    def __init__(self, simulate=True):
        super().__init__()
        self.simulate = simulate
        self.running = True
        self._stop_event = threading.Event()
        self._proc_lock = threading.Lock()
        self._process = None

    def run(self):
        self._stop_event.clear()
        while self.running and not self._stop_event.is_set():
            if self.simulate:
                # Không phát dữ liệu giả.
                # BandwidthStore sẽ tự chuyển nguồn
                # sang STALE và biểu đồ để khoảng trống.
                self._stop_event.wait(BANDWIDTH_POLL_INTERVAL_SEC)
                continue

            try:
                self._run_streaming_session()
            except Exception as error:
                if not self.running or self._stop_event.is_set():
                    break
                self.collector_error_signal.emit(str(error))
                self._stop_event.wait(BANDWIDTH_POLL_INTERVAL_SEC)

    def stop(self):
        self.running = False
        self._stop_event.set()
        self._terminate_active_process()

    def _terminate_active_process(self):
        with self._proc_lock:
            proc = self._process
            self._process = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=0.5)
        except OSError:
            pass

    @staticmethod
    def _build_tshark_cmd(*, duration_sec: float, line_buffered: bool = False):
        ports = list(TELEMETRY_PORTS.keys())
        d_args = []
        for port in ports:
            d_args.extend(["-d", f"udp.port=={port},rtp"])

        port_filter = " or ".join(f"udp dst port {port}" for port in ports)
        capture_filter = f"dst host {TAILSCALE_IP_PI4} and ({port_filter})"

        cmd = ["tshark"]
        if line_buffered:
            cmd.extend(["-l", "-n"])
        cmd.extend(
            [
                "-p",
                "-i",
                TAILSCALE_INTERFACE,
                *d_args,
                "-f",
                capture_filter,
                "-a",
                f"duration:{duration_sec:g}",
                "-T",
                "fields",
                "-e",
                "udp.dstport",
                "-e",
                "frame.len",
                "-e",
                "rtp.seq",
                "-e",
                "frame.time_relative",
            ]
        )
        return cmd

    def _run_streaming_session(self):
        window_sec = max(0.2, float(BANDWIDTH_POLL_INTERVAL_SEC))
        session_sec = max(window_sec * 2.0, float(self.STREAM_SESSION_DURATION_SEC))
        cmd = self._build_tshark_cmd(
            duration_sec=session_sec,
            line_buffered=True,
        )

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as error:
            raise RuntimeError(f"Không thể chạy Tshark: {error}") from error

        with self._proc_lock:
            if not self.running or self._stop_event.is_set():
                proc.terminate()
                return
            self._process = proc

        line_buffer = []
        buffer_lock = threading.Lock()
        reader_done = threading.Event()

        def _stdout_reader():
            try:
                if proc.stdout is not None:
                    for raw_line in proc.stdout:
                        if raw_line:
                            with buffer_lock:
                                line_buffer.append(raw_line)
            except Exception:
                pass
            finally:
                reader_done.set()

        reader_thread = threading.Thread(
            target=_stdout_reader,
            name="tshark-stream-reader",
            daemon=True,
        )
        reader_thread.start()

        try:
            window_start = time.monotonic()
            next_tick = window_start + window_sec

            while self.running and not self._stop_event.is_set():
                wait_sec = max(0.0, next_tick - time.monotonic())
                if self._stop_event.wait(wait_sec):
                    break

                now_mono = time.monotonic()
                elapsed = max(0.2, now_mono - window_start)
                window_start = now_mono
                next_tick = now_mono + window_sec

                with buffer_lock:
                    batch_lines = list(line_buffer)
                    line_buffer.clear()

                retcode = proc.poll()
                if retcode is not None:
                    reader_done.wait(timeout=0.2)
                    with buffer_lock:
                        if line_buffer:
                            batch_lines.extend(line_buffer)
                            line_buffer.clear()

                    if retcode != 0 and self.running and not self._stop_event.is_set():
                        stderr_txt = ""
                        if proc.stderr is not None:
                            try:
                                stderr_txt = proc.stderr.read().strip()
                            except Exception:
                                stderr_txt = ""
                        raise RuntimeError(
                            stderr_txt or f"Tshark exit code {retcode}"
                        )

                    # Phiên 5 phút kết thúc sạch (returncode == 0): phát nốt mẫu nếu có rồi mở phiên mới
                    if batch_lines:
                        metrics = self.compute_metrics_from_lines(
                            batch_lines, elapsed
                        )
                        self.telemetry_signal.emit(metrics)
                    break

                metrics = self.compute_metrics_from_lines(batch_lines, elapsed)
                self.telemetry_signal.emit(metrics)
        finally:
            self._terminate_active_process()
            reader_done.wait(timeout=0.3)

    def get_qos_metrics(self):
        capture_duration_sec = float(BANDWIDTH_POLL_INTERVAL_SEC)
        cmd = self._build_tshark_cmd(
            duration_sec=capture_duration_sec,
            line_buffered=False,
        )
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = process.communicate()
        except OSError as error:
            raise RuntimeError(f"Không thể chạy Tshark: {error}") from error
        if process.returncode != 0:
            error_text = stderr.strip()
            if not error_text:
                error_text = f"Tshark exit code {process.returncode}"
            raise RuntimeError(error_text)
        lines = stdout.strip().split("\n") if stdout else []
        return self.compute_metrics_from_lines(lines, capture_duration_sec)

    @staticmethod
    def compute_metrics_from_lines(lines, capture_duration_sec: float, telemetry_ports=None):
        ports_cfg = telemetry_ports if telemetry_ports is not None else TELEMETRY_PORTS
        ports = list(ports_cfg.keys())
        duration = max(1e-3, float(capture_duration_sec))

        # Khởi tạo dữ liệu cho tất cả các cổng
        data_by_port = {
            p: {"bytes": 0, "packets": [], "times": []}
            for p in ports
        }

        for line in lines:
            if not line or not line.strip():
                continue
            parts = (
                [p.strip() for p in line.split("\t")]
                if "\t" in line
                else line.split()
            )
            if len(parts) >= 2:
                port, length_str = parts[0], parts[1]
                if port not in data_by_port or not length_str.isdigit():
                    continue
                pkt_len = int(length_str)
                data_by_port[port]["bytes"] += pkt_len

                if len(parts) >= 3 and parts[2]:
                    seq_items = [
                        int(s) for s in parts[2].split(",") if s.isdigit()
                    ]
                    if seq_items:
                        # Nếu tshark tách được nhiều seq trong cùng 1 frame thì chia đều độ dài
                        sub_len = max(1, pkt_len // len(seq_items))
                        for s in seq_items:
                            data_by_port[port]["packets"].append((s, sub_len))

                if len(parts) >= 4 and parts[3]:
                    try:
                        data_by_port[port]["times"].append(float(parts[3]))
                    except ValueError:
                        pass

        results = []
        for port, info in ports_cfg.items():
            total_bytes = data_by_port[port]["bytes"]
            packets = sorted(
                data_by_port[port]["packets"], key=lambda item: item[0]
            )
            times = data_by_port[port]["times"]

            # 1. Tính Bitrate (Mbps)
            bitrate_mbps = round(
                (total_bytes * 8 / duration / 1_000_000), 3
            )

            # 2. Tính Tỷ lệ Mất gói Packet Loss (%) có bù gói gộp UDP GRO (Len > 1400)
            loss_pct = 0.0
            if len(packets) > 1:
                expected_packets = 0
                received_packets = 0
                for idx in range(len(packets) - 1):
                    curr_seq, curr_len = packets[idx]
                    next_seq, _ = packets[idx + 1]
                    gap = next_seq - curr_seq
                    # Bỏ qua các bước nhảy dị thường khi stream_loop quay vòng hoặc trùng gói
                    if 0 < gap < 1000:
                        expected_packets += gap
                        coalesced = (
                            max(1, round(curr_len / 1150))
                            if curr_len > 1400
                            else 1
                        )
                        received_packets += min(coalesced, gap)

                last_len = packets[-1][1]
                last_coalesced = (
                    max(1, round(last_len / 1150)) if last_len > 1400 else 1
                )
                expected_packets += last_coalesced
                received_packets += last_coalesced

                if expected_packets > 0:
                    loss_count = max(0, expected_packets - received_packets)
                    loss_pct = round((loss_count / expected_packets) * 100, 2)

            # 3. Tính Độ trễ / Jitter thực tế (ms) từ thời gian đến của gói tin
            jitter_ms = 0.0
            if len(times) > 1:
                deltas_ms = [
                    (times[i] - times[i - 1]) * 1000.0
                    for i in range(1, len(times))
                    if times[i] >= times[i - 1]
                ]
                # Lọc các khoảng cách gói liên tiếp trong cùng cụm frame (< 25ms)
                intra_burst_ms = [d for d in deltas_ms if 0.0 < d <= 25.0]
                if intra_burst_ms:
                    jitter_ms = round(
                        sum(intra_burst_ms) / len(intra_burst_ms), 2
                    )
                elif deltas_ms:
                    # Fallback RFC 3550 mean deviation nếu chỉ có các gói thưa
                    diffs = [
                        abs(deltas_ms[i] - deltas_ms[i - 1])
                        for i in range(1, len(deltas_ms))
                    ]
                    jitter_ms = (
                        round(sum(diffs) / len(diffs), 2)
                        if diffs
                        else round(deltas_ms[0], 2)
                    )
            elif bitrate_mbps > 0:
                jitter_ms = 1.0

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
            time.sleep(BANDWIDTH_POLL_INTERVAL_SEC)
    except KeyboardInterrupt:
        print("\nĐã dừng giám sát QoS.")
        sys.exit(0)
