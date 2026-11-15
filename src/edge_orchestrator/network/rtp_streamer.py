"""Bounded, paced RTP encoder fed by application frames, never by a camera device."""
from dataclasses import dataclass
import ipaddress
import json
import math
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import threading
import time


@dataclass(frozen=True)
class RtpStreamConfig:
    destination: str = "100.108.25.63"
    port: int = 8004
    local_address: str = "192.168.10.13"
    interface: str = "eth0"
    sdp_path: str = "/tmp/datn-cam03-app.sdp"
    width: int = 640
    height: int = 480
    fps: int = 25
    bitrate_kbps: int = 600
    max_frame_age_sec: float = 2.0
    write_timeout_sec: float = 1.0
    ffmpeg: str = "ffmpeg"

    def __post_init__(self):
        ipaddress.IPv4Address(self.destination)
        ipaddress.IPv4Address(self.local_address)
        if not 1024 <= self.port <= 65534:
            raise ValueError("RTP port must be in [1024, 65534]")
        if (self.width < 2 or self.height < 2 or self.width % 2 or self.height % 2
                or not 1 <= self.fps <= 60 or not 1 <= self.bitrate_kbps <= 100000):
            raise ValueError("Invalid RTP resolution, FPS or bitrate")
        if self.max_frame_age_sec <= 0 or self.write_timeout_sec <= 0:
            raise ValueError("RTP age and write timeouts must be positive")

    def command(self):
        return [self.ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostdin",
                "-f", "rawvideo", "-pixel_format", "bgr24",
                "-video_size", f"{self.width}x{self.height}", "-framerate", str(self.fps),
                "-use_wallclock_as_timestamps", "1",
                "-i", "pipe:0", "-an", "-c:v", "libx264", "-preset", "ultrafast",
                "-tune", "zerolatency", "-pix_fmt", "yuv420p", "-threads", "2",
                "-b:v", f"{self.bitrate_kbps}k", "-maxrate", f"{self.bitrate_kbps}k",
                "-bufsize", f"{max(1, self.bitrate_kbps // 2)}k", "-g", str(self.fps),
                "-bf", "0", "-fps_mode", "vfr", "-enc_time_base", "1:90000",
                "-payload_type", "96", "-sdp_file", self.sdp_path,
                "-f", "rtp", f"rtp://{self.destination}:{self.port}?"
                f"localaddr={self.local_address}&pkt_size=1000"]


def verify_lan_route(config):
    """Use the same bound source as FFmpeg; a default Tailscale route is insufficient."""
    result = subprocess.run(
        ["ip", "-j", "-4", "route", "get", config.destination, "from", config.local_address],
        capture_output=True, text=True, timeout=3, check=True)
    routes = json.loads(result.stdout)
    if len(routes) != 1 or routes[0].get("dev") != config.interface:
        raise RuntimeError("RTP source route does not use the configured LAN interface")


class RtpStreamer:
    def __init__(self, config, status_callback=None, *, ready_event=None, route_validator=verify_lan_route,
                 process_factory=subprocess.Popen):
        self.config = config
        self._callback = status_callback or (lambda status, message: None)
        self._verify_route = route_validator
        self._popen = process_factory
        self._ready_event = ready_event
        self._condition = threading.Condition()
        self._stop = threading.Event()
        self._thread = None
        self._pending = None
        self._state = "STOPPED"
        self._error = None
        self._encoder_stderr = ""
        self._counts = dict(submitted=0, overwritten=0, written=0, repeated=0,
                            last_frame_id=None, max_write_ms=0.0, max_lag_ms=0.0,
                            skipped_slots=0)

    def _status(self, state, message=""):
        with self._condition:
            self._state = state
            if state == "ERROR":
                self._error = message
        try:
            if state in {"WAITING", "RUNNING", "ERROR"}:
                print(f"[RTP] {state}: {message}", file=sys.stderr, flush=True)
                if state == "ERROR" and self._encoder_stderr:
                    print(self._encoder_stderr, file=sys.stderr, flush=True)
            self._callback(state, message)
        except Exception:
            pass

    def snapshot(self):
        with self._condition:
            return dict(self._counts, status=self._state, error=self._error,
                        encoder_stderr=self._encoder_stderr,
                        pending_frames=int(self._pending is not None))

    def start(self):
        with self._condition:
            if self._thread is not None:
                raise RuntimeError("Create a new RTP streamer to restart")
            self._state = "WAITING"
            self._thread = threading.Thread(target=self._run, name="cam03-rtp", daemon=True)
            self._thread.start()

    def submit_frame(self, frame, frame_id):
        """Own one BGR copy; never perform network/encoder I/O on the capture thread."""
        with self._condition:
            if self._stop.is_set() or self._state not in {"WAITING", "RUNNING"}:
                return False
        if (getattr(frame, "shape", None) != (self.config.height, self.config.width, 3)
                or str(getattr(frame, "dtype", "")) != "uint8"):
            raise ValueError("Encoder requires uint8 BGR frames at configured resolution")
        payload = frame.tobytes(order="C")
        received_at = time.monotonic()
        with self._condition:
            if self._stop.is_set() or self._state not in {"WAITING", "RUNNING"}:
                return False
            self._counts["submitted"] += 1
            self._counts["overwritten"] += int(self._pending is not None)
            self._pending = (payload, frame_id, received_at)
            self._condition.notify()
        return True

    def request_stop(self):
        self._stop.set()
        with self._condition:
            self._condition.notify_all()

    def close(self, timeout_sec=4.0):
        self.request_stop()
        with self._condition:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout_sec)
        return thread is None or not thread.is_alive()

    def _write_frame(self, proc, payload):
        view = memoryview(payload)
        offset = 0
        deadline = time.monotonic() + self.config.write_timeout_sec
        while offset < len(view):
            if self._stop.is_set():
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("RTP encoder pipe stalled")
            if proc.poll() is not None:
                raise RuntimeError("RTP encoder exited")
            fd = proc.stdin.fileno()
            if not select.select([], [fd], [], min(0.1, remaining))[1]:
                continue
            try:
                count = os.write(fd, view[offset:offset + 65536])
            except BlockingIOError:
                continue
            if count <= 0:
                raise BrokenPipeError("RTP encoder pipe closed")
            offset += count
        return True

    def _run(self):
        proc = None
        log = None
        failed = False
        try:
            self._verify_route(self.config)
            if self._ready_event is not None:
                self._status("WAITING", "Waiting for AI initialization before starting video encoder")
                while not self._stop.is_set():
                    if self._ready_event.wait(.1):
                        break
            if self._stop.is_set():
                return
            self._status("WAITING", "Waiting for fresh camera frames")
            with self._condition:
                self._condition.wait_for(lambda: self._stop.is_set() or (
                    self._pending is not None and
                    time.monotonic()-self._pending[2] <= self.config.max_frame_age_sec))
            if self._stop.is_set():
                return
            # A stale SDP must not be mistaken for a newly running encoder.
            Path(self.config.sdp_path).unlink(missing_ok=True)
            log = tempfile.TemporaryFile()
            proc = self._popen(self.config.command(), stdin=subprocess.PIPE,
                               stdout=subprocess.DEVNULL, stderr=log, bufsize=0)
            os.set_blocking(proc.stdin.fileno(), False)
            period = 1.0 / self.config.fps
            deadline = time.monotonic()
            latest = None
            last_id = None
            running_reported = False
            while not self._stop.is_set():
                if self._stop.wait(max(0.0, deadline - time.monotonic())):
                    break
                with self._condition:
                    if self._pending is not None:
                        latest, self._pending = self._pending, None
                payload, frame_id, received_at = latest
                now = time.monotonic()
                if now - received_at > self.config.max_frame_age_sec:
                    raise TimeoutError("Camera frames stale; stopped RTP rather than repeating old video")
                lag = max(0.0, now - deadline)
                started = time.monotonic()
                if not self._write_frame(proc, payload):
                    break
                finished = time.monotonic()
                # A missed output slot is not queued work. Skip it instead of
                # accumulating schedule debt or bursting old frames to catch up.
                deadline += period
                skipped = max(0, math.ceil((finished - deadline) / period))
                deadline += skipped * period
                with self._condition:
                    self._counts["written"] += 1
                    self._counts["repeated"] += int(frame_id == last_id)
                    self._counts["last_frame_id"] = frame_id
                    self._counts["max_write_ms"] = max(self._counts["max_write_ms"],
                                                        (time.monotonic() - started) * 1000)
                    self._counts["max_lag_ms"] = max(self._counts["max_lag_ms"], lag * 1000)
                    self._counts["skipped_slots"] += skipped
                last_id = frame_id
                if not running_reported and Path(self.config.sdp_path).is_file():
                    self._status("RUNNING", "Encoder active; receiver delivery not yet verified")
                    running_reported = True
        except Exception as error:
            failed = True
            if log is not None:
                # Keep the bounded error tail before the temporary log closes.
                # pread does not move the file offset shared with FFmpeg.
                try:
                    fd = log.fileno()
                    size = os.fstat(fd).st_size
                    tail = os.pread(fd, min(size, 4096), max(0, size - 4096))
                    with self._condition:
                        self._encoder_stderr = tail.decode("utf-8", "replace")
                except (OSError, ValueError):
                    pass
            self._status("ERROR", str(error))
        finally:
            if proc is not None:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=1.5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=1)
                if proc.stdin is not None:
                    proc.stdin.close()
                Path(self.config.sdp_path).unlink(missing_ok=True)
            if log is not None:
                log.close()
            with self._condition:
                self._pending = None
            if not failed:
                self._status("STOPPED")
