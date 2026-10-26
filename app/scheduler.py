"""Background inbox watcher thread (started by run.py with --watch)."""
from __future__ import annotations
import threading, time

def start_watcher(app):
    poll = int(app.config["APP_CFG"].get("email", {}).get("poll_seconds", 120))
    def loop():
        while True:
            time.sleep(poll)
            try:
                with app.app_context():
                    from .inbox_service import process_inbox
                    process_inbox()
            except Exception as e:
                app.logger.warning(f"inbox watcher: {e}")
    t = threading.Thread(target=loop, daemon=True, name="inbox-watcher")
    t.start()
    return t
