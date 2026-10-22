"""Photo alert delivery contract; no live Telegram calls."""
import json
from io import BytesIO
import threading
import unittest
from unittest.mock import patch

from edge_orchestrator.network.telegram_alerts import CameraAlert, TelegramAlerts, send_photo


def sample():
    return CameraAlert("CAM03", "Kho tài liệu bảo mật tầng 8", 2,
                       1792567938, b"\xff\xd8photo\xff\xd9", 8, "R2")


class TelegramAlertTests(unittest.TestCase):
    def test_upload_contains_photo_and_all_metadata(self):
        class Response(BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
        with patch("urllib.request.urlopen", return_value=Response(b'{"ok":true}')) as upload:
            send_photo("test-token", "123", sample())
        req = upload.call_args.args[0]
        self.assertTrue(req.full_url.endswith("/sendPhoto"))
        self.assertIn(sample().photo, req.data)
        for value in ("CAM03", "Kho tài liệu bảo mật tầng 8", "People: 2",
                      "NORMAL → ALERT", "8 Mbps", "Active router: R2",
                      "Time: 2026-10-21 14:32:18"):
            self.assertIn(value.encode(), req.data)

    def test_transient_failure_retries_same_photo_then_succeeds(self):
        calls = []
        done = threading.Event()
        logs = []
        def sender(token, chat, alert):
            calls.append(alert)
            if len(calls) < 4:
                raise OSError("network unavailable")
        def log(text, failed):
            logs.append((text, failed))
            done.set()
        service = TelegramAlerts("secret", "123", log, sender, retry_delays=(0, 0, 0))
        try:
            alert = sample()
            self.assertTrue(service.submit(alert))
            self.assertTrue(done.wait(2))
            self.assertEqual(calls, [alert] * 4)
            self.assertFalse(logs[-1][1])
        finally:
            service.close()

    def test_permanent_failure_stops_after_four_attempts_and_redacts_token(self):
        done = threading.Event()
        calls = []
        logs = []
        def sender(*args):
            calls.append(args)
            raise OSError("https://api.telegram.org/botSECRET/sendPhoto")
        def log(text, failed):
            logs.append((text, failed))
            done.set()
        service = TelegramAlerts("SECRET", "123", log, sender, retry_delays=(0, 0, 0))
        try:
            service.submit(sample())
            self.assertTrue(done.wait(2))
            self.assertEqual(len(calls), 4)
            self.assertTrue(logs[-1][1])
            self.assertNotIn("SECRET", str(logs))
        finally:
            service.close()

    def test_missing_credentials_or_photo_never_sends(self):
        calls = []
        sender = lambda *args: calls.append(args)
        service = TelegramAlerts("", "", sender=sender)
        self.assertFalse(service.submit(sample()))
        service = TelegramAlerts("secret", "123", sender=sender)
        no_photo = CameraAlert("CAM03", "Location", 2, 0, b"", 8, "R2")
        self.assertFalse(service.submit(no_photo))
        service.close()
        self.assertFalse(service.submit(sample()))
        self.assertEqual(calls, [])

    def test_submit_does_not_block_on_network(self):
        entered, release = threading.Event(), threading.Event()
        def sender(*args):
            entered.set()
            release.wait(2)
        service = TelegramAlerts("secret", "123", sender=sender)
        try:
            self.assertTrue(service.submit(sample()))
            self.assertTrue(entered.wait(1))
            self.assertTrue(service.submit(sample()))
        finally:
            release.set()
            service.close(1)


if __name__ == "__main__":
    unittest.main()
