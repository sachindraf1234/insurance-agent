"""Email SEND (SMTP) + RECEIVE/NOTIFY (IMAP watcher). Works with Yahoo / Gmail / other IMAP+SMTP.

Configure the provider credentials in local .env. The watcher searches unread mail
only for configured, allowlisted senders, then classifies approved messages, parses
attachments, updates the DB and creates a Notification (dashboard + Telegram).
Playwright webmail alternative lives in smtp_imap's sibling playwright Mail.py /
playwright_agent/webmail_agent.py and shares the same reply-parsing code.
"""
from __future__ import annotations
import email, imaplib, os, smtplib, ssl
from email.header import decode_header
from email.message import EmailMessage
from email.utils import getaddresses
from pathlib import Path

def _dec(v) -> str:
    if not v: return ""
    parts = decode_header(str(v))
    return "".join(p.decode(enc or "utf-8", "ignore") if isinstance(p, bytes) else p
                   for p, enc in parts)

def send_email(cfg: dict, to: str, subject: str, body: str,
               attachments: list[str | Path], cc: str = "") -> str:
    addr, pw = os.getenv("MAIL_ADDRESS", ""), os.getenv("MAIL_PASSWORD", "")
    if not addr or not pw:
        raise RuntimeError("MAIL_ADDRESS / MAIL_PASSWORD missing in .env — see docs/EMAIL_SETUP_GMAIL_OUTLOOK.md")
    if not to:
        raise RuntimeError("Recipient email is empty — set the insurer email in config.yaml / Settings.")
    # This address is authorized for inbound reply checks only, never outbound.
    receive_only = "warunic@allianz.lk"
    outgoing = {address.lower() for _name, address in getaddresses([to, cc]) if address}
    if receive_only in outgoing:
        raise RuntimeError(f"{receive_only} is receive-only and is blocked as an outbound recipient.")
    msg = EmailMessage()
    msg["From"] = f"{os.getenv('MAIL_FROM_NAME','Insurance Desk')} <{addr}>"
    msg["To"] = to
    if cc: msg["Cc"] = cc
    msg["Subject"] = subject
    msg.set_content(body)
    maintypes = {".xlsx": ("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                 ".pdf": ("application", "pdf"), ".jpg": ("image", "jpeg"),
                 ".jpeg": ("image", "jpeg"), ".png": ("image", "png")}
    for ap in attachments:
        p = Path(ap)
        mt, st = maintypes.get(p.suffix.lower(), ("application", "octet-stream"))
        msg.add_attachment(p.read_bytes(), maintype=mt, subtype=st, filename=p.name)
    ctx = ssl.create_default_context()
    with smtplib.SMTP(cfg["smtp_host"], int(cfg["smtp_port"]), timeout=60) as s:
        s.starttls(context=ctx); s.login(addr, pw); s.send_message(msg)
    return str(msg["Message-ID"] or subject)

def fetch_unseen(cfg: dict, save_dir: str | Path, mark_seen: bool = True) -> list[dict]:
    """Fetch only unread messages whose From address is explicitly allowlisted.

    Search is restricted server-side first, then the exact parsed From address
    is checked before fetching the full message body or attachments. When the
    allowlist is absent/empty, the inbox is left untouched.
    """
    addr, pw = os.getenv("MAIL_ADDRESS", ""), os.getenv("MAIL_PASSWORD", "")
    if not addr or not pw: return []
    allowed = {str(x).strip().lower() for x in (cfg.get("reply_from") or []) if str(x).strip()}
    if not allowed: return []
    save_dir = Path(save_dir); save_dir.mkdir(parents=True, exist_ok=True)
    out = []
    with imaplib.IMAP4_SSL(cfg["imap_host"], int(cfg["imap_port"])) as M:
        M.login(addr, pw); M.select("INBOX")
        matched = set()
        # IMAP FROM search is performed only for approved insurer addresses.
        for sender in sorted(allowed):
            _typ, data = M.search(None, "UNSEEN", "FROM", f'"{sender}"')
            if data and data[0]: matched.update(data[0].split())
        for num in sorted(matched, key=lambda x: int(x)):
            # Inspect headers first and use PEEK so even a non-matching message
            # stays unread. Fetch body/attachments only after exact allowlist check.
            _typ, header_raw = M.fetch(num, "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])")
            if not header_raw or not header_raw[0] or not isinstance(header_raw[0][1], bytes):
                continue
            header = email.message_from_bytes(header_raw[0][1])
            from_header = _dec(header.get("From"))
            senders = {address.lower() for _name, address in getaddresses([from_header]) if address}
            if not (senders & allowed):
                continue
            _typ, raw = M.fetch(num, "(BODY.PEEK[])")
            if not raw or not raw[0] or not isinstance(raw[0][1], bytes):
                continue
            msg = email.message_from_bytes(raw[0][1])
            body, atts = "", []
            for part in msg.walk():
                ct, cd = part.get_content_type(), str(part.get("Content-Disposition") or "")
                if ct == "text/plain" and "attachment" not in cd and not body:
                    body = part.get_payload(decode=True).decode("utf-8", "ignore")
                elif "attachment" in cd and part.get_filename():
                    dest = save_dir / Path(_dec(part.get_filename())).name
                    dest.write_bytes(part.get_payload(decode=True))
                    atts.append(str(dest))
            out.append({"from": from_header, "to": _dec(msg.get("To")),
                        "subject": _dec(msg.get("Subject")), "body": body,
                        "message_id": str(msg.get("Message-ID") or ""), "attachments": atts})
            if mark_seen: M.store(num, "+FLAGS", "\\Seen")
    return out
