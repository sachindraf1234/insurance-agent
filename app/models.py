"""SQLite models (SQLAlchemy). One file DB: instance/insurance.db — back it up!"""
from __future__ import annotations
from datetime import datetime, date
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class ApplicantGroup(db.Model):
    """One uploaded Batch intake, reviewed as a separate company/type group."""
    id = db.Column(db.Integer, primary_key=True)
    company = db.Column(db.String(20), nullable=False, index=True)
    kind = db.Column(db.String(10), nullable=False, index=True)
    entry_mode = db.Column(db.String(10), default="BATCH", nullable=False)
    group_number = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)


class Applicant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    company = db.Column(db.String(20), default="LOLC")        # ALLIANZ | LOLC
    kind = db.Column(db.String(10), default="NEW")            # NEW | EXTEND
    entry_mode = db.Column(db.String(10), default="SINGLE", index=True)  # SINGLE | BATCH
    applicant_group_id = db.Column(db.Integer, db.ForeignKey("applicant_group.id"), nullable=True, index=True)
    applicant_group = db.relationship("ApplicantGroup", backref="applicants")
    status = db.Column(db.String(30), default="EXTRACTED", index=True)
    salutation = db.Column(db.String(20), default="")
    gender = db.Column(db.String(10), default="")
    surname = db.Column(db.String(160), default="")           # -> FIRST NAME column
    given_names = db.Column(db.String(160), default="")       # -> LAST NAME column
    passport_no = db.Column(db.String(20), default="", index=True)
    passport_file_path = db.Column(db.String(500), default="")
    attach_passport_copy = db.Column(db.Boolean, default=False)
    birthday = db.Column(db.Date, nullable=True)
    telephone = db.Column(db.String(20), default="")
    addr_no = db.Column(db.String(40), default="")
    addr_street = db.Column(db.String(240), default="")
    city = db.Column(db.String(120), default="")
    address_raw = db.Column(db.Text, default="")
    issue_date = db.Column(db.Date, nullable=True)
    departure_date = db.Column(db.Date, nullable=True)
    arrival_date = db.Column(db.Date, nullable=True)
    embassy_appointment_date = db.Column(db.Date, nullable=True)
    existing_policy_no = db.Column(db.String(40), default="")  # for EXTEND (e.g. HODTSTxxxxx)
    new_policy_no = db.Column(db.String(40), default="")       # filled from insurer reply
    confidence_json = db.Column(db.Text, default="{}")
    sheet_overrides_json = db.Column(db.Text, default="{}")  # per-insurer cells edited during final Review
    notes = db.Column(db.Text, default="")
    batch_id = db.Column(db.Integer, db.ForeignKey("batch.id"), nullable=True)
    def as_dict(self):
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        for k, v in d.items():
            if isinstance(v, (date, datetime)): d[k] = v.isoformat()
        return d

class InsurancePreview(db.Model):
    """Persistent intake preview rows, retained across page refreshes/restarts
    until the user explicitly clicks Clear form. Applicant records remain in
    their normal workflow queues when this preview state is cleared.
    """
    scope_key = db.Column(db.String(80), primary_key=True)  # COMPANY:KIND:MODE
    applicant_ids_json = db.Column(db.Text, default="[]", nullable=False)


class ExtractionJob(db.Model):
    """Durable status for long-running OCR jobs; browser requests only enqueue and poll."""
    job_id = db.Column(db.String(32), primary_key=True)
    company = db.Column(db.String(20), nullable=False)
    kind = db.Column(db.String(10), nullable=False)
    entry_mode = db.Column(db.String(10), nullable=False)
    status = db.Column(db.String(20), default="QUEUED", nullable=False)  # QUEUED|PROCESSING|COMPLETE|FAILED
    progress = db.Column(db.Integer, default=0, nullable=False)
    message = db.Column(db.String(400), default="", nullable=False)
    error_message = db.Column(db.String(500), default="", nullable=False)
    applicant_ids_json = db.Column(db.Text, default="[]", nullable=False)
    redirect_url = db.Column(db.String(1000), default="", nullable=False)
    file_summary_json = db.Column(db.Text, default="{}", nullable=False)
    source_root = db.Column(db.String(700), default="", nullable=False)
    source_files_cleared = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


class Batch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    company = db.Column(db.String(20)); kind = db.Column(db.String(10))
    submission_mode = db.Column(db.String(10), default="BATCH")  # SINGLE | BATCH
    batch_date = db.Column(db.Date)  # NEW issue day / EXTEND interview day
    excel_path = db.Column(db.String(400), default="")
    email_to = db.Column(db.String(300), default="")
    email_subject = db.Column(db.String(300), default="")
    sent_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(30), default="DRAFT")  # DRAFT|SENT|REPLIED|PARTIAL
    applicants = db.relationship("Applicant", backref="batch")

class EmailLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    at = db.Column(db.DateTime, default=datetime.now)
    direction = db.Column(db.String(5))  # IN | OUT
    frm = db.Column(db.String(300), default=""); to = db.Column(db.String(300), default="")
    subject = db.Column(db.String(400), default="")
    body_snippet = db.Column(db.Text, default="")
    kind = db.Column(db.String(30), default="OTHER")  # SEND|POLICY_REPLY|APPOINTMENT|OTHER
    ref = db.Column(db.String(300), default="")       # message-id / filename

class AgentSetting(db.Model):
    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.String(200), default="")

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    at = db.Column(db.DateTime, default=datetime.now)
    title = db.Column(db.String(300)); body = db.Column(db.Text, default="")
    level = db.Column(db.String(10), default="info")  # info|success|warning|error
    seen = db.Column(db.Boolean, default=False)
