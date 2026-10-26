"""Playwright EMBASSY-PORTAL watcher (optional 3rd appointment channel).

Appointment dates reach the system 3 ways — use whichever you actually have:
 1) Email  -> handled automatically by the inbox watcher (default ON)
 2) Recruitment-system backend -> POST /api/integrations/embassy-appointment
 3) Portal -> THIS file: set embassy.portal_url in config.yaml, then adapt the
    two TODO selectors to that portal (every embassy/VFS site differs; login
    session is saved the same way as webmail — run webmail_agent --login first
    is NOT enough: run this file once with --login to sign in to the portal).
It extracts (passport no, appointment date) pairs and POSTs the same effect as
the manual page by writing through the Flask app context.
"""
from __future__ import annotations
import re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

def scan_portal(url: str, headless: bool = True) -> list[dict]:
    from playwright.sync_api import sync_playwright
    from playwright_agent.webmail_agent import _ctx
    found = []
    with sync_playwright() as pw:
        ctx = _ctx(pw, headless=headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url, wait_until="domcontentloaded"); page.wait_for_timeout(4000)
        text = page.inner_text("body")
        # generic scrape — tighten with real selectors once the portal is known:
        # rows = page.locator("TODO: table row selector").all_inner_texts()
        for passport, dstr in re.findall(r"\b([NP]\d{7,8})\b[^\n]{0,80}?(\d{1,2}[./\-]\d{1,2}[./\-]\d{4})", text.upper()):
            found.append({"passport_no": passport, "date_str": dstr})
        ctx.close()
    return found

if __name__ == "__main__":
    print("Set embassy.portal_url in config.yaml and call scan_portal(url) from the scheduler.")
    print("Until a portal is configured, appointments flow via Email + Manual entry (both live).")
