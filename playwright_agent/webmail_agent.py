"""Playwright WEBMAIL automation (alternative to SMTP/IMAP).

Sends via Gmail/Outlook in a real browser using a SAVED login session
(no password stored in code). One-time setup:
    python playwright_agent/webmail_agent.py --login gmail
Log in manually in the opened browser (handles 2FA), press Enter — the session
is saved under instance/browser_profile/ and reused headlessly afterwards.
Use when SMTP is blocked or the office policy requires webmail. Selectors for
webmail UIs change over time; if a click fails, run with headless=False to watch.
"""
from __future__ import annotations
import sys, time
from pathlib import Path

PROFILE = Path(__file__).resolve().parent.parent / "instance" / "browser_profile"
URLS = {"gmail": "https://mail.google.com/mail/u/0/#inbox",
        "outlook": "https://outlook.live.com/mail/0/inbox/"}

def _ctx(pw, headless=True):
    PROFILE.mkdir(parents=True, exist_ok=True)
    return pw.chromium.launch_persistent_context(str(PROFILE), headless=headless)

def login(webmail: str = "gmail"):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx = _ctx(pw, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(URLS.get(webmail, URLS["gmail"]))
        input(">>> Log in to webmail in the browser, open the inbox, then press Enter here… ")
        ctx.close()
    print("Session saved ✔")

def send_via_webmail(webmail: str, to: str, subject: str, body: str,
                     attachments: list[str], headless: bool = True):
    if not to: raise RuntimeError("Recipient email is empty — set insurer email in config.yaml.")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx = _ctx(pw, headless=headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(URLS.get(webmail, URLS["gmail"]), wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        if webmail == "outlook":
            page.get_by_role("button", name=lambda n: n and "new mail" in n.lower()).first.click()
        else:
            page.get_by_role("button", name=lambda n: n and "compose" in n.lower()).first.click()
        page.wait_for_timeout(1500)
        page.locator("input[name='to'], input[placeholder*='To' i]").first.click()
        page.keyboard.type(to); page.keyboard.press("Tab")
        page.locator("input[name='subjectbox'], input[placeholder*='subject' i]").first.fill(subject)
        page.locator("div[aria-label='Message Body'], div[contenteditable='true']").first.click()
        page.keyboard.type(body)
        for ap in attachments:
            with page.expect_file_chooser() as fc:
                page.locator("div[command='Files'], input[type='file']").first.click()
            fc.value.set_files(str(ap))
            page.wait_for_timeout(2500)
        page.keyboard.press("Control+Enter")  # Gmail/Outlook send shortcut
        page.wait_for_timeout(3000)
        ctx.close()

if __name__ == "__main__":
    if "--login" in sys.argv:
        login(sys.argv[sys.argv.index("--login") + 1] if len(sys.argv) > 2 else "gmail")
    else:
        print(__doc__)
