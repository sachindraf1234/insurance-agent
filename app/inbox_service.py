"""Inbox watcher brain: classify new mail -> update DB -> notify -> auto-EXTEND.
Runs from the dashboard button, the background scheduler, and the MCP tool.
"""
from __future__ import annotations
from flask import current_app
from .models import db, Applicant, EmailLog
from .notifications import notify
from .pipeline import apply_date_rules, to_date

def make_extend_draft(src: Applicant, appointment, entry_mode: str | None = None) -> Applicant:
    """Clone a source applicant into an EXTEND draft with departure = appt + 14d."""
    dd = apply_date_rules("EXTEND", appointment=appointment)
    mode = (entry_mode or getattr(src, "entry_mode", "SINGLE") or "SINGLE").upper()
    if mode not in ("SINGLE", "BATCH"): mode = "SINGLE"
    n = Applicant(company=src.company, kind="EXTEND", entry_mode=mode, status="EXTEND_DRAFT",
                  salutation=src.salutation, gender=src.gender, surname=src.surname,
                  given_names=src.given_names, passport_no=src.passport_no,
                  passport_file_path=src.passport_file_path, attach_passport_copy=src.attach_passport_copy,
                  birthday=src.birthday, telephone=src.telephone,
                  addr_no=src.addr_no, addr_street=src.addr_street, city=src.city,
                  address_raw=src.address_raw,
                  existing_policy_no=src.new_policy_no or src.existing_policy_no,
                  embassy_appointment_date=dd["embassy_appointment_date"],
                  departure_date=dd["departure_date"], arrival_date=dd["arrival_date"],
                  issue_date=dd["issue_date"],
                  confidence_json=src.confidence_json, notes=f"Auto-extend from applicant #{src.id}")
    src.embassy_appointment_date = dd["embassy_appointment_date"]
    if src.status in ("POLICY_RECEIVED", "AWAITING_EMBASSY"): src.status = "APPOINTMENT_RECEIVED"
    db.session.add(n); db.session.commit()
    return n

def process_inbox() -> dict:
    cfgall = current_app.config["APP_CFG"]
    ecfg, ncfg = cfgall.get("email", {}), cfgall.get("notify")
    from .email_agent.smtp_imap import fetch_unseen
    from .email_agent.reply_parser import parse_policy_reply, parse_appointment
    msgs = fetch_unseen(ecfg, current_app.config["INBOX_DIR"])
    res = {"emails": len(msgs), "policies": 0, "appointments": 0}
    keywords = cfgall.get("embassy", {}).get("watch_email_keywords", ["appointment"])
    for m in msgs:
        kind = "OTHER"
        mapping = parse_policy_reply(m["attachments"], m["body"])
        if mapping:
            kind = "POLICY_REPLY"
            for passport, policy in mapping.items():
                rows = Applicant.query.filter_by(passport_no=passport)\
                         .filter(Applicant.status.in_(["AWAITING_REPLY", "EMAIL_SENT", "EXCEL_GENERATED"])).all()
                for a in rows:
                    a.new_policy_no = policy
                    a.status = "EXTENDED_POLICY_RECEIVED" if a.kind == "EXTEND" else "POLICY_RECEIVED"
                    res["policies"] += 1
                    notify(f"✅ Policy received: {a.surname} {a.given_names}",
                           f"Passport {passport} → Policy {policy} ({a.company} {a.kind})", "success", ncfg)
        appt = parse_appointment(m["subject"], m["body"], keywords)
        if appt and appt["appointment_date"]:
            kind = "APPOINTMENT" if kind == "OTHER" else kind
            src = None
            if appt["passport_no"]:
                src = Applicant.query.filter_by(passport_no=appt["passport_no"])\
                        .order_by(Applicant.id.desc()).first()
            if src:
                d = to_date(appt["appointment_date"])
                n = make_extend_draft(src, d)
                res["appointments"] += 1
                notify(f"🏛 Embassy appointment: {src.surname} {src.given_names}",
                       f"Appointment {d} → EXTEND departure auto-set to {n.departure_date} (+14 days). "
                       f"Review EXTEND draft #{n.id} and send.", "warning", ncfg)
            else:
                notify("🏛 Embassy appointment email (no applicant matched)",
                       f"Date {appt['appointment_date']}, passport '{appt['passport_no'] or 'not found in email'}'. "
                       "Check the recruitment-system sync and retry the appointment import.", "warning", ncfg)
        db.session.add(EmailLog(direction="IN", frm=m["from"], to=m["to"], subject=m["subject"],
                                body_snippet=(m["body"] or "")[:600], kind=kind,
                                ref=m["message_id"]))
        db.session.commit()
    if msgs and not (res["policies"] or res["appointments"]):
        notify(f"📥 {len(msgs)} new email(s) received", "No policy/appointment data auto-matched — please check your inbox.", "info", ncfg)
    return res
