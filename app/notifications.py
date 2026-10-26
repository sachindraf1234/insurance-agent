"""Notify the user: dashboard bell (DB) + optional free Telegram push."""
from __future__ import annotations
import os

def notify(title: str, body: str = "", level: str = "info", cfg: dict | None = None):
    from .models import db, Notification
    try:
        db.session.add(Notification(title=title, body=body, level=level))
        db.session.commit()
    except Exception:
        db.session.rollback()
    token = os.getenv("TELEGRAM_BOT_TOKEN") or (cfg or {}).get("telegram_bot_token", "")
    chat = os.getenv("TELEGRAM_CHAT_ID") or (cfg or {}).get("telegram_chat_id", "")
    if token and chat:
        try:
            import requests
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20,
                          json={"chat_id": chat, "text": f"🛡 {title}\n{body}"[:3900]})
        except Exception:
            pass
