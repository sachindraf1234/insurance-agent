from __future__ import annotations
import os
import json
from pathlib import Path
import yaml
from dotenv import load_dotenv
from flask import Flask

BASE = Path(__file__).resolve().parent.parent

def load_cfg() -> dict:
    p = BASE / "config.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}

def create_app() -> Flask:
    load_dotenv(BASE / ".env")
    app = Flask(__name__, instance_path=str(BASE / "instance"))
    app.config.update(
        SECRET_KEY=os.getenv("SECRET_KEY", "dev-secret-change-me"),
        MAIN_SYSTEM_API_KEY=os.getenv("MAIN_SYSTEM_API_KEY", ""),
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{BASE / 'instance' / 'insurance.db'}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        MAX_CONTENT_LENGTH=60 * 1024 * 1024,
        APP_CFG=load_cfg(), BASE=BASE,
        UPLOAD_DIR=BASE / "instance" / "uploads",
        OUTPUT_DIR=BASE / "output",
        INBOX_DIR=BASE / "instance" / "inbox_attachments",
    )
    for d in ("UPLOAD_DIR", "OUTPUT_DIR", "INBOX_DIR"):
        Path(app.config[d]).mkdir(parents=True, exist_ok=True)
    from .models import db, Applicant, ApplicantGroup, InsurancePreview, ExtractionJob
    db.init_app(app)
    with app.app_context():
        db.create_all()
        # In-place migrations must run before ORM queries use new applicant fields.
        from sqlalchemy import inspect, text
        with db.engine.begin() as conn:
            tables = inspect(db.engine).get_table_names()
            if "applicant" in tables:
                cols = {c["name"] for c in inspect(db.engine).get_columns("applicant")}
                if "entry_mode" not in cols:
                    conn.execute(text("ALTER TABLE applicant ADD COLUMN entry_mode VARCHAR(10) NOT NULL DEFAULT 'SINGLE'"))
                if "passport_file_path" not in cols:
                    conn.execute(text("ALTER TABLE applicant ADD COLUMN passport_file_path VARCHAR(500) NOT NULL DEFAULT ''"))
                if "attach_passport_copy" not in cols:
                    conn.execute(text("ALTER TABLE applicant ADD COLUMN attach_passport_copy BOOLEAN NOT NULL DEFAULT 0"))
                if "sheet_overrides_json" not in cols:
                    conn.execute(text("ALTER TABLE applicant ADD COLUMN sheet_overrides_json TEXT NOT NULL DEFAULT '{}'"))
                if "applicant_group_id" not in cols:
                    conn.execute(text("ALTER TABLE applicant ADD COLUMN applicant_group_id INTEGER REFERENCES applicant_group(id)"))
            if "batch" in tables:
                cols = {c["name"] for c in inspect(db.engine).get_columns("batch")}
                if "submission_mode" not in cols:
                    conn.execute(text("ALTER TABLE batch ADD COLUMN submission_mode VARCHAR(10) NOT NULL DEFAULT 'BATCH'"))
            if "extraction_job" in tables:
                cols = {c["name"] for c in inspect(db.engine).get_columns("extraction_job")}
                if "file_summary_json" not in cols:
                    conn.execute(text("ALTER TABLE extraction_job ADD COLUMN file_summary_json TEXT NOT NULL DEFAULT '{}'"))
                if "source_root" not in cols:
                    conn.execute(text("ALTER TABLE extraction_job ADD COLUMN source_root VARCHAR(700) NOT NULL DEFAULT ''"))
                if "source_files_cleared" not in cols:
                    conn.execute(text("ALTER TABLE extraction_job ADD COLUMN source_files_cleared BOOLEAN NOT NULL DEFAULT 0"))
        # Older app versions discarded the domestic leading zero. Normalize
        # persisted Sri Lankan mobile values on startup so Review/export also
        # get local 10-digit numbers after an upgrade.
        from .phone import normalize_phone
        for applicant in Applicant.query.filter(Applicant.telephone != "").all():
            local_phone = normalize_phone(applicant.telephone)
            if local_phone and local_phone != applicant.telephone:
                applicant.telephone = local_phone
        # A process restart cannot retain its in-memory worker queue; expose an
        # explicit retryable error rather than leaving the browser polling forever.
        for job in ExtractionJob.query.filter(ExtractionJob.status.in_(["QUEUED", "PROCESSING"])).all():
            job.status = "FAILED"
            job.progress = 100
            job.message = "The server restarted during extraction. Please submit the documents again."
            job.error_message = "The interrupted OCR job was not automatically replayed."
        # Legacy Batch applicants are collected into a carry-over group per
        # insurer/type. Group numbers run sequentially within each insurer.
        legacy = Applicant.query.filter(Applicant.entry_mode == "BATCH",
            Applicant.applicant_group_id.is_(None), ~Applicant.status.like("CANCELLED_%")).all()
        by_group_scope = {}
        for applicant in legacy:
            by_group_scope.setdefault((applicant.company.upper(), applicant.kind.upper()), []).append(applicant)
        for (company, kind), applicants in by_group_scope.items():
            last = ApplicantGroup.query.filter_by(company=company, entry_mode="BATCH")\
                .order_by(ApplicantGroup.group_number.desc()).first()
            group = ApplicantGroup(company=company, kind=kind, entry_mode="BATCH",
                                   group_number=(last.group_number + 1 if last else 1))
            db.session.add(group); db.session.flush()
            for applicant in applicants:
                applicant.applicant_group_id = group.id
        # Carry over currently pending applicant rows into the persistent intake preview.
        # Empty preview rows mark an explicit Clear form and are intentionally preserved.
        active = Applicant.query.filter(Applicant.status.in_(["NEEDS_REVIEW", "EXTEND_DRAFT"])).all()
        by_scope = {}
        for applicant in active:
            key = f"{applicant.company.upper()}:{applicant.kind.upper()}:{applicant.entry_mode.upper()}"
            by_scope.setdefault(key, []).append(applicant.id)
        for key, applicant_ids in by_scope.items():
            if db.session.get(InsurancePreview, key) is None:
                db.session.add(InsurancePreview(scope_key=key, applicant_ids_json=json.dumps(applicant_ids)))
        db.session.commit()
    from .city_matcher import CityMatcher
    app.config["CITY_MATCHER"] = CityMatcher(BASE / "data" / "cities.txt")
    from .routes import bp
    app.register_blueprint(bp)
    return app
