"""Telegram delivery: a daily digest, urgent pushes, and watchlist commands.

Token and chat id come from the environment (.env), never from the repo.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import watchlist as wl                                    # noqa: E402

API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4000                      # Telegram's limit is 4096


def _creds() -> tuple[str, str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
    return token, chat


def send(text: str) -> bool:
    token, chat = _creds()
    ok = True
    for i in range(0, len(text), MAX_LEN):
        r = requests.post(API.format(token=token, method="sendMessage"),
                          json={"chat_id": chat, "text": text[i:i + MAX_LEN],
                                "disable_web_page_preview": True}, timeout=20)
        ok = ok and r.status_code == 200
    return ok


def handle_command(text: str) -> str:
    """Watchlist commands. Deliberately no trading commands: this bot cannot
    place, modify or cancel an order, and should never be able to."""
    parts = text.strip().split()
    if not parts:
        return "Commands: /list, /add SYMBOL, /remove SYMBOL, /pause SYMBOL"
    cmd, arg = parts[0].lower(), (parts[1].upper() if len(parts) > 1 else "")
    if cmd == "/list":
        entries = wl.load()
        active = [e.symbol for e in entries if e.is_active]
        return f"{len(active)} active:\n" + ", ".join(sorted(active))
    if cmd == "/add" and arg:
        from data.sources import universe
        _, unknown = universe.validate([arg])
        if unknown:
            return f"{arg} is not a listed NSE symbol."
        return wl.add(arg)[1]
    if cmd == "/remove" and arg:
        return wl.set_active(arg, False)[1]
    if cmd == "/pause" and arg:
        return wl.set_active(arg, False)[1]
    if cmd == "/resume" and arg:
        return wl.set_active(arg, True)[1]
    return "Commands: /list, /add SYMBOL, /remove SYMBOL, /pause SYMBOL"


def poll(timeout: int = 30) -> None:
    """Long-poll for commands. Run alongside the scheduled job if you want the
    /add and /remove commands to work from your phone."""
    token, chat = _creds()
    offset = None
    while True:
        r = requests.get(API.format(token=token, method="getUpdates"),
                         params={"timeout": timeout, "offset": offset}, timeout=timeout + 10)
        for upd in r.json().get("result", []):
            offset = upd["update_id"] + 1
            msg = upd.get("message", {})
            if str(msg.get("chat", {}).get("id")) != str(chat):
                continue        # only ever answer your own chat
            text = msg.get("text", "")
            if text.startswith("/"):
                send(handle_command(text))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "poll":
        poll()
    else:
        print(handle_command(" ".join(sys.argv[1:]) or "/list"))
