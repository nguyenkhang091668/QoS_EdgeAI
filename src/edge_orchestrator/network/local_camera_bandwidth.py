import time

import cv2

from PyQt5.QtCore import (
    QThread,
    pyqtSignal,
)

from edge_orchestrator.Edge.layer1_motion import (
    Camera,
)


class EncodedBandwidthMeter:
    """Tính Mbps từ số byte JPEG thực tế được tạo."""

    def __init__(
        self,
        interval_sec=2.0,
        monotonic_clock=time.monotonic,
        wall_clock=time.time,
    ):
        if interval_sec <= 0:
            raise ValueError(
                "interval_sec must be positive"
            )

        self.interval_sec = float(
            interval_sec
        )
        self._monotonic_clock = (
            monotonic_clock
        )
        self._wall_clock = wall_clock

        self._window_started_at = None
        self._encoded_bytes = 0

    def observe(self, encoded_size):
        if (
            not isinstance(encoded_size, int)
            or isinstance(encoded_size, bool)
            or encoded_size < 0
        ):
            raise ValueError(
                "encoded_size must be "
                "a non-negative integer"
            )

        now = float(
            self._monotonic_clock()
        )

        if self._window_started_at is None:
            self._window_started_at = now

        self._encoded_bytes += encoded_size

        elapsed = (
            now
            - self._window_started_at
        )

        if elapsed < self.interval_sec:
            return None

        value_mbps = (
            self._encoded_bytes
            * 8
            / elapsed
            / 1_000_000
        )

        sampled_at = float(
            self._wall_clock()
        )

        self._window_started_at = now
        self._encoded_bytes = 0

        return (
            value_mbps,
            sampled_at,
        )


class LocalCameraBandwidthWorker(QThread):
    """Đo output JPEG của camera cục bộ."""

    bandwidth_signal = pyqtSignal(
        str,
        float,
        float,
    )

    error_signal = pyqtSignal(
        str,
        str,
    )

    status_signal = pyqtSignal(
        str,
        str,
    )

    def __init__(
        self,
        source_id,
        source,
        device=0,
        interval_sec=2.0,
        jpeg_quality=80,
        parent=None,
    ):
        super().__init__(parent)

        if not source_id:
            raise ValueError(
                "source_id must not be empty"
            )

        if source not in {
            "picamera2",
            "usb",
            "webcam",
        }:
            raise ValueError(
                f"Unsupported camera source: "
                f"{source}"
            )

        if not 0 <= jpeg_quality <= 100:
            raise ValueError(
                "jpeg_quality must be "
                "between 0 and 100"
            )

        self.source_id = source_id
        self.source = source
        self.device = device
        self.interval_sec = float(
            interval_sec
        )
        self.jpeg_quality = int(
            jpeg_quality
        )

        self._running = False

    def run(self):
        self._running = True
        camera = None

        try:
            camera = Camera(
                self.source,
                self.device,
            )

            meter = EncodedBandwidthMeter(
                interval_sec=self.interval_sec
            )

            self.status_signal.emit(
                self.source_id,
                "RUNNING",
            )

            encode_parameters = [
                cv2.IMWRITE_JPEG_QUALITY,
                self.jpeg_quality,
            ]

            while self._running:
                frame = camera.read()

                encoded_ok, encoded = (
                    cv2.imencode(
                        ".jpg",
                        frame,
                        encode_parameters,
                    )
                )

                if not encoded_ok:
                    raise RuntimeError(
                        "JPEG encoding failed"
                    )

                measurement = meter.observe(
                    int(encoded.nbytes)
                )

                if measurement is None:
                    continue

                value_mbps, sampled_at = (
                    measurement
                )

                self.bandwidth_signal.emit(
                    self.source_id,
                    value_mbps,
                    sampled_at,
                )

        except Exception as error:
            self.error_signal.emit(
                self.source_id,
                str(error),
            )

            self.status_signal.emit(
                self.source_id,
                "ERROR",
            )

        finally:
            if camera is not None:
                camera.close()

            self.status_signal.emit(
                self.source_id,
                "STOPPED",
            )

    def stop(self):
        self._running = False
        self.requestInterruption()