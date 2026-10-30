#!/usr/bin/env python3
"""Pair a new Telegram bot to one private chat without printing its token."""
import getpass
import json
import os
from pathlib import Path
import re
import secrets
from urllib import request


def main():
    print("Tạo bot bằng /newbot tại https://t.me/BotFather trước khi tiếp tục.")
    token = getpass.getpass("Bot token (ẩn khi nhập): ").strip()
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token):
        raise SystemExit("Token không đúng định dạng.")
    pairing = "/start " + secrets.token_hex(6)
    print("Mở chat riêng với bot vừa tạo và gửi chính xác: " + pairing)
    input("Nhấn Enter sau khi đã gửi: ")
    offset = 0
    chat_id = None
    try:
        for _ in range(20):
            payload = json.dumps({"offset": offset, "limit": 100, "timeout": 0}).encode()
            req = request.Request(
                f"https://api.telegram.org/bot{token}/getUpdates", data=payload,
                headers={"Content-Type": "application/json"}, method="POST")
            with request.urlopen(req, timeout=10) as response:
                result = json.load(response)
            if not result.get("ok"):
                raise ValueError("API response not OK")
            updates = result.get("result", [])
            for update in updates:
                offset = max(offset, update["update_id"] + 1)
                message = update.get("message", {})
                chat = message.get("chat", {})
                if chat.get("type") == "private" and message.get("text") == pairing:
                    chat_id = chat["id"]
                    break
            if chat_id is not None or len(updates) < 100:
                break
    except Exception:
        raise SystemExit("Không đọc được Telegram. Kiểm tra token, mạng, hoặc webhook đang dùng; chưa lưu cấu hình.") from None
    if chat_id is None:
        raise SystemExit("Chưa tìm thấy tin nhắn ghép nối. Kiểm tra đúng chat bot và chạy lại.")
    output = Path(__file__).resolve().parents[1] / ".env.telegram"
    if output.exists():
        raise SystemExit(".env.telegram đã có. Đổi tên bản cũ trước nếu muốn cấu hình lại.")
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(f"TELEGRAM_BOT_TOKEN={token}\nTELEGRAM_CHAT_ID={chat_id}\n")
    print("Đã lưu .env.telegram (quyền 600). Chat cá nhân đã được xác nhận.")
    print("Nạp trước khi chạy dashboard: set -a; source .env.telegram; set +a")


if __name__ == "__main__":
    main()
