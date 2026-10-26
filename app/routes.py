"""Flask UI + JSON API. Workflow: Upload -> OCR extract -> Review (fix red fields)
-> Approve -> Batch (per day+company+type) -> Excel -> Send email -> auto-watch replies
-> Embassy appointment -> auto EXTEND draft -> send -> done.
"""
from __future__ import annotations
import json, uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from flask import (Blueprint, current_app, flash, jsonify, redirect, render_template,
                   request, send_file, url_for)
from werkzeug.utils import secure_filename

from .constants import COMPANIES, SALUTATIONS, GENDERS
from .excel_gen import generate_excel, batch_filename, _row_for, _sheet_date, _sheet_date_value
from .models import db, Applicant, ApplicantGroup, Batch, EmailLog, Notification, AgentSetting, InsurancePreview, ExtractionJob
from .notifications import notify
from .pipeline import process_documents, apply_date_rules, needs_review, to_date
from .phone import normalize_phone

bp = Blueprint("main", __name__)
_JOB_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="insurance-ocr")
cfg = lambda: current_app.config["APP_CFG"]
matcher = lambda: current_app.config["CITY_MATCHER"]


def _create_extraction_job(company: str, kind: str, mode: str) -> ExtractionJob:
    job = ExtractionJob(job_id=uuid.uuid4().hex, company=company.upper(), kind=kind.upper(),
                        entry_mode=mode.upper(), status="QUEUED", progress=0,
                        message="Queued for local document extraction.")
    db.session.add(job); db.session.commit()
    return job


def _dispatch_extraction(app, job_id: str, runner, *args) -> None:
    # Keep test uploads deterministic; production OCR runs outside the HTTP request
    # so the preview proxy does not time out and reset port 5000.
    if app.testing:
        runner(app, job_id, *args)
    else:
        _JOB_EXECUTOR.submit(runner, app, job_id, *args)


def _job_progress(job: ExtractionJob, progress: int, message: str) -> None:
    job.status, job.progress, job.message = "PROCESSING", progress, message
    db.session.commit()


def _public_file_groups(job: ExtractionJob | None) -> list[dict]:
    if not job: return []
    try:
        data = json.loads(job.file_summary_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    labels = {"passport_files":"Passport photo pages", "police_files":"Police reports",
              "application_files":"Applicant / phone documents", "policy_files":"Existing policy documents"}
    groups = []
    for key, label in labels.items():
        items = data.get(key, []) if isinstance(data, dict) else []
        if not isinstance(items, list) or not items: continue
        groups.append({"key":key,"label":label,"items":[{
            "name":item.get("name", "Uploaded document"),
            "confidence":item.get("confidence", 1.0),
            "reason":item.get("reason", ""),
            "low_confidence":item.get("low_confidence", False),
        } for item in items if isinstance(item, dict)]})
    return groups


def _fail_extraction_job(app, job_id: str, exc: Exception) -> None:
    with app.app_context():
        db.session.rollback()
        job = db.session.get(ExtractionJob, job_id)
        if job:
            job.status = "FAILED"
            job.progress = 100
            job.error_message = f"{type(exc).__name__}: {str(exc)[:350]}"
            job.message = "Document extraction failed. Review the error and try again."
            db.session.commit()
            app.logger.error("Extraction job %s failed: %s", job_id, exc,
                             exc_info=(type(exc), exc, exc.__traceback__))


def _finish_extraction_job(job_id: str, applicant_ids, company: str, kind: str, mode: str, message: str) -> None:
    ids = [int(x) for x in applicant_ids]
    _append_insurance_preview(company, kind, mode, ids)
    job = db.session.get(ExtractionJob, job_id)
    job.applicant_ids_json = json.dumps(ids)
    job.redirect_url = f"/insurance/{kind}/{mode}?" + urlencode({
        "company": company, "preview_ids": ",".join(map(str, ids)), "job_id": job_id})
    job.status, job.progress, job.message = "COMPLETE", 100, message
    db.session.commit()


def _run_single_extraction_job(app, job_id: str, paths: dict, company: str, kind: str,
                               apply_date: str, embassy_date: str, telephone_manual: str) -> None:
    with app.app_context():
        try:
            job = db.session.get(ExtractionJob, job_id)
            if not job: return
            _job_progress(job, 5, "Starting local OCR.")
            got = process_documents(paths.get("passport"), paths.get("police"), paths.get("application"),
                                    matcher(), cfg().get("ocr", {}),
                                    policy_document_path=paths.get("policy_doc"))
            _job_progress(job, 80, "Saving extracted applicant details.")
            got["_passport_file_path"] = str(paths.get("passport") or "")
            applicant = _create_applicant(got, company, kind, apply_date, embassy_date,
                                          telephone_manual, "SINGLE")
            bad = needs_review(json.loads(applicant.confidence_json),
                               float(cfg().get("ocr", {}).get("confidence_threshold", 0.82)))
            messages = ["Extraction complete. Please review" + (" — check: " + ", ".join(bad) if bad else ".")]
            if got.get("_ocr_warnings"):
                messages.append("OCR warnings: " + "; ".join(got["_ocr_warnings"]))
            if kind == "EXTEND" and paths.get("policy_doc") and not applicant.existing_policy_no:
                messages.append("No policy number was confidently extracted; verify it in Review.")
            _finish_extraction_job(job_id, [applicant.id], company, kind, "SINGLE", " ".join(messages))
        except Exception as exc:
            app.logger.error("Single extraction failed (%s): %s", job_id, exc,
                             exc_info=(type(exc), exc, exc.__traceback__))
            _fail_extraction_job(app, job_id, exc)


def _preview_scope(company: str, kind: str, mode: str) -> str:
    return f"{company.upper()}:{kind.upper()}:{mode.upper()}"


def _append_insurance_preview(company: str, kind: str, mode: str, applicant_ids) -> None:
    """Persist extracted applicant IDs until the user clears this intake form."""
    key = _preview_scope(company, kind, mode)
    state = db.session.get(InsurancePreview, key)
    if state is None:
        state = InsurancePreview(scope_key=key, applicant_ids_json="[]")
        db.session.add(state)
    try:
        ids = [int(x) for x in json.loads(state.applicant_ids_json or "[]")]
    except (TypeError, ValueError, json.JSONDecodeError):
        ids = []
    for applicant_id in applicant_ids:
        try:
            applicant_id = int(applicant_id)
        except (TypeError, ValueError):
            continue
        if applicant_id > 0 and applicant_id not in ids:
            ids.append(applicant_id)
    state.applicant_ids_json = json.dumps(ids)
    db.session.commit()


def _create_applicant_group(company: str, kind: str) -> ApplicantGroup:
    """Allocate the next independent Batch group number for one insurer/type."""
    company, kind = company.upper(), kind.upper()
    last = ApplicantGroup.query.filter_by(company=company, entry_mode="BATCH")\
        .order_by(ApplicantGroup.group_number.desc()).first()
    group = ApplicantGroup(company=company, kind=kind, entry_mode="BATCH",
                          group_number=(last.group_number + 1 if last else 1))
    db.session.add(group)
    db.session.commit()
    return group


@bp.context_processor
def _ctx():
    return {"unseen": Notification.query.filter_by(seen=False).count(),
            "registry_count": Applicant.query.filter(~Applicant.status.like("CANCELLED_%")).count(),
            "review_count": Applicant.query.filter(Applicant.status.in_(["NEEDS_REVIEW","EXTEND_DRAFT"])).count(),
            "main_system_connected": bool(current_app.config.get("MAIN_SYSTEM_API_KEY")),
            "SALUTATIONS": SALUTATIONS, "GENDERS": GENDERS, "COMPANIES": COMPANIES}


def _create_applicant(got: dict, company: str, kind: str, apply_date_s: str = "",
                      embassy_s: str = "", telephone_manual: str = "", entry_mode: str = "SINGLE"):
    """Build+save an Applicant from process_documents() output.

    The universal police-report step remains the primary address source. A
    matching application form can only corroborate/fill OCR-damaged address
    components when its passport number matches the passport in this bundle.
    """
    passport_no = (got.get("passport_no") or "").upper()
    if kind == "EXTEND" and passport_no:
        pending = Applicant.query.filter_by(passport_no=passport_no, company=company, kind="EXTEND")\
            .filter(Applicant.embassy_appointment_date.isnot(None))\
            .order_by(Applicant.id.desc()).first()
        if pending and pending.status in ("EXTEND_DRAFT", "NEEDS_REVIEW"):
            for field, key in (("surname","surname"),("given_names","given_names"),("passport_no","passport_no"),
                               ("birthday","birthday"),("gender","gender"),("salutation","salutation"),
                               ("addr_no","addr_no"),("addr_street","addr_street"),("city","city"),
                               ("address_raw","address_raw")):
                val = got.get(key)
                if val: setattr(pending, field, to_date(val) if field=="birthday" else val)
            pending.telephone = normalize_phone(telephone_manual or got.get("telephone") or pending.telephone)
            if got.get("_passport_file_path"): pending.passport_file_path=got["_passport_file_path"]
            if got.get("existing_policy_no"): pending.existing_policy_no=got["existing_policy_no"]
            pending.entry_mode = entry_mode
            pending.confidence_json = json.dumps(got.get("confidence", {}))
            pending.status = "NEEDS_REVIEW"
            dd = apply_date_rules("EXTEND", appointment=pending.embassy_appointment_date)
            pending.departure_date, pending.arrival_date = dd["departure_date"], dd["arrival_date"]
            db.session.commit()
            return pending
    a = Applicant(company=company, kind=kind, entry_mode=entry_mode,
                  salutation=got.get("salutation", ""), gender=got.get("gender", ""),
                  surname=got.get("surname", ""), given_names=got.get("given_names", ""),
                  passport_no=(got.get("passport_no") or "").upper(),
                  passport_file_path=got.get("_passport_file_path", ""),
                  existing_policy_no=got.get("existing_policy_no", ""),
                  birthday=to_date(got.get("birthday")),
                  telephone=normalize_phone(telephone_manual or got.get("telephone", "")),
                  addr_no=got.get("addr_no", ""), addr_street=got.get("addr_street", ""),
                  city=got.get("city", ""), address_raw=got.get("address_raw", ""),
                  confidence_json=json.dumps(got.get("confidence", {})),
                  status="NEEDS_REVIEW")
    if a.kind == "EXTEND" and a.passport_no:
        orig = Applicant.query.filter_by(passport_no=a.passport_no, kind="NEW")\
                 .filter(Applicant.new_policy_no != "").order_by(Applicant.id.desc()).first()
        if orig and not a.existing_policy_no: a.existing_policy_no = orig.new_policy_no
    try:
        if a.kind == "EXTEND":
            appt = to_date(embassy_s or "")
            dd = apply_date_rules("EXTEND", appointment=appt) if appt else {}
        else:
            dd = apply_date_rules("NEW", issue=to_date(apply_date_s or "") or date.today())
        for k in ("issue_date", "departure_date", "arrival_date", "embassy_appointment_date"):
            if dd.get(k): setattr(a, k, dd[k])
    except Exception as e:
        a.notes = f"Date error: {e}"
    db.session.add(a); db.session.commit()
    return a

def _dashboard_recent_applicants():
    # Keep applicant IDs permanently tied to their records while surfacing
    # pending work first. Within each section, newest applicants appear first.
    active = ~Applicant.status.like("CANCELLED_%")
    pending_statuses = ("NEEDS_REVIEW", "EXTEND_DRAFT")
    pending_recent = (Applicant.query.filter(active, Applicant.status.in_(pending_statuses))
                      .order_by(Applicant.id.desc()).limit(15).all())
    remaining_slots = 15 - len(pending_recent)
    other_recent = (Applicant.query.filter(active, ~Applicant.status.in_(pending_statuses))
                    .order_by(Applicant.id.desc()).limit(remaining_slots).all()) if remaining_slots else []
    return pending_recent + other_recent


@bp.route("/")
def dashboard():
    stats = {s: Applicant.query.filter_by(status=s).count()
             for s in ("NEEDS_REVIEW", "APPROVED", "EMAIL_SENT", "AWAITING_REPLY",
                       "POLICY_RECEIVED", "EXTEND_DRAFT")}
    recent = _dashboard_recent_applicants()
    clearable_ids = {a.id for a in recent
                     if a.batch_id is None and a.status in ("NEEDS_REVIEW", "EXTEND_DRAFT", "APPROVED")}
    notes = Notification.query.order_by(Notification.id.desc()).limit(8).all()
    return render_template("dashboard.html", stats=stats, recent=recent, notes=notes,
                           clearable_ids=clearable_ids)


@bp.route("/dashboard/recent/clear", methods=["POST"])
def clear_dashboard_recent_applicants():
    raw_ids = request.form.getlist("applicant_ids")
    if not raw_ids:
        flash("Select one or more clearable applicants first.", "warning")
        return redirect(url_for("main.dashboard"))
    try:
        selected_ids = list(dict.fromkeys(int(value) for value in raw_ids))
    except (TypeError, ValueError):
        flash("Invalid applicant selection; no records were changed.", "error")
        return redirect(url_for("main.dashboard"))

    visible_ids = {a.id for a in _dashboard_recent_applicants()}
    if not set(selected_ids).issubset(visible_ids):
        flash("Selection no longer matches the recent dashboard rows; refresh and try again.", "error")
        return redirect(url_for("main.dashboard"))

    selected = Applicant.query.filter(Applicant.id.in_(selected_ids)).all()
    cleared = 0
    skipped = 0
    for a in selected:
        if a.batch_id is not None or a.status not in ("NEEDS_REVIEW", "EXTEND_DRAFT", "APPROVED"):
            skipped += 1
            continue
        a.status = "CANCELLED_" + a.status
        cleared += 1
    if cleared:
        db.session.commit()
    if cleared:
        flash(f"Cleared {cleared} selected applicant(s) from active tables. Their records are retained in Applicants → Cancelled and can be restored.", "success")
    if skipped:
        flash(f"{skipped} selected applicant(s) could not be cleared because they are attached to a prepared/sent workflow or are no longer eligible.", "warning")
    return redirect(url_for("main.dashboard"))

@bp.route("/insurance")
def insurance_home():
    return render_template("insurance_home.html")

@bp.route("/insurance/<kind>/<mode>")
def insurance_entry(kind, mode):
    kind, mode = kind.upper(), mode.upper()
    if kind not in ("NEW", "EXTEND") or mode not in ("SINGLE", "BATCH"):
        return redirect(url_for("main.insurance_entry", kind="NEW", mode="SINGLE"))
    companies_cfg = cfg().get("companies", {})
    selected_company = (request.args.get("company", "ALLIANZ") or "ALLIANZ").upper()
    if selected_company not in COMPANIES: selected_company = "ALLIANZ"
    format_map = {name: (COMPANIES[name]["columns_new"] if kind == "NEW" else COMPANIES[name]["columns_extend"])
                  for name in COMPANIES}
    default_map = {name: {**COMPANIES[name].get("defaults", {}),
                          **companies_cfg.get(name, {}).get("defaults", {})}
                   for name in COMPANIES}
    scope_key = _preview_scope(selected_company, kind, mode)
    # Accept legacy preview_ids URLs once, then keep that state in SQLite so a
    # refresh, browser navigation, or app restart cannot blank the extracted row.
    if request.args.get("preview_ids"):
        requested_ids = []
        for token in request.args["preview_ids"].split(","):
            try:
                value = int(token)
                if value > 0 and value not in requested_ids: requested_ids.append(value)
            except (TypeError, ValueError):
                continue
        valid = Applicant.query.filter(
            Applicant.id.in_(requested_ids), Applicant.company == selected_company,
            Applicant.kind == kind, Applicant.entry_mode == mode
        ).with_entities(Applicant.id).all() if requested_ids else []
        _append_insurance_preview(selected_company, kind, mode, [row[0] for row in valid])
    preview_state = db.session.get(InsurancePreview, scope_key)
    try:
        preview_ids = [int(x) for x in json.loads(preview_state.applicant_ids_json or "[]")] if preview_state else []
    except (TypeError, ValueError, json.JSONDecodeError):
        preview_ids = []
    preview_applicants = []
    if preview_ids:
        preview_applicants = Applicant.query.filter(
            Applicant.id.in_(preview_ids), Applicant.company == selected_company,
            Applicant.kind == kind, Applicant.entry_mode == mode
        ).order_by(Applicant.id.asc()).all()
        preview_applicants = [a for a in preview_applicants if not a.status.startswith("CANCELLED_")]
    sheet_columns = COMPANIES[selected_company]["columns_new" if kind == "NEW" else "columns_extend"]
    row_defaults = {**companies_cfg.get(selected_company, {}).get("defaults", {})}
    preview_records = [{"applicant": a, "row": _row_for(a.as_dict(), selected_company, kind, row_defaults)}
                       for a in preview_applicants]
    file_job = db.session.get(ExtractionJob, request.args.get("job_id", "")) if request.args.get("job_id") else None
    if file_job and (file_job.company != selected_company or file_job.kind != kind or file_job.entry_mode != mode):
        file_job = None
    source_file_groups = _public_file_groups(file_job)
    return render_template("insurance_entry.html", kind=kind, mode=mode, today=date.today().isoformat(),
                           format_map=format_map, default_map=default_map, companies=COMPANIES,
                           selected_company=selected_company, sheet_columns=sheet_columns,
                           preview_records=preview_records, source_file_groups=source_file_groups,
                           source_file_job=file_job,
                           source_files_cleared=bool(file_job.source_files_cleared) if file_job else False)


@bp.route("/insurance/save-preview-choices", methods=["POST"])
def save_preview_choices():
    company = (request.form.get("company", "") or "").upper()
    kind = (request.form.get("kind", "") or "").upper()
    mode = (request.form.get("mode", "") or "").upper()
    if company not in COMPANIES or kind not in ("NEW", "EXTEND") or mode not in ("SINGLE", "BATCH"):
        flash("The preview choices could not be saved because the form scope was invalid.", "error")
        return redirect(url_for("main.insurance_home"))
    state = db.session.get(InsurancePreview, _preview_scope(company, kind, mode))
    try:
        allowed_ids = {int(value) for value in json.loads(state.applicant_ids_json or "[]")} if state else set()
    except (TypeError, ValueError, json.JSONDecodeError):
        allowed_ids = set()
    saved = 0
    for raw_id in request.form.getlist("applicant_ids"):
        try: aid = int(raw_id)
        except (TypeError, ValueError): continue
        if aid not in allowed_ids: continue
        applicant = Applicant.query.filter_by(id=aid, company=company, kind=kind, entry_mode=mode).first()
        if not applicant or applicant.status not in ("NEEDS_REVIEW", "EXTEND_DRAFT"): continue
        salutation = request.form.get(f"salutation_{aid}", "")
        gender = request.form.get(f"gender_{aid}", "")
        if salutation in SALUTATIONS or not salutation: applicant.salutation = salutation
        if gender in GENDERS or not gender: applicant.gender = gender
        saved += 1
    db.session.commit()
    flash(f"Saved dropdown choices for {saved} applicant(s).", "success")
    params = {"kind": kind, "mode": mode, "company": company}
    job_id = request.form.get("job_id", "")
    job = db.session.get(ExtractionJob, job_id) if job_id else None
    if job and job.status == "COMPLETE" and (job.company, job.kind, job.entry_mode) == (company, kind, mode):
        params["job_id"] = job_id
    return redirect(url_for("main.insurance_entry", **params))


@bp.route("/insurance/clear-preview", methods=["POST"])
def clear_insurance_preview():
    kind = request.form.get("kind", "NEW").upper()
    mode = request.form.get("mode", "SINGLE").upper()
    company = request.form.get("company", "ALLIANZ").upper()
    if kind not in ("NEW", "EXTEND") or mode not in ("SINGLE", "BATCH") or company not in COMPANIES:
        flash("The insurer preview could not be cleared because the selected form was invalid.", "error")
        return redirect(url_for("main.insurance_home"))
    key = _preview_scope(company, kind, mode)
    state = db.session.get(InsurancePreview, key)
    if state is None:
        state = InsurancePreview(scope_key=key, applicant_ids_json="[]")
        db.session.add(state)
    else:
        state.applicant_ids_json = "[]"
    db.session.commit()
    flash("The insurer preview was cleared. Applicant records remain in Review and have not been deleted.", "success")
    return redirect(url_for("main.insurance_entry", kind=kind, mode=mode, company=company))


@bp.route("/extraction/<job_id>")
def extraction_job_page(job_id):
    job = db.get_or_404(ExtractionJob, job_id)
    return render_template("extraction_status.html", job=job,
                           source_file_groups=_public_file_groups(job))


@bp.route("/extraction/<job_id>/status")
def extraction_job_status(job_id):
    job = db.get_or_404(ExtractionJob, job_id)
    response = jsonify({"status": job.status, "progress": job.progress,
                        "message": job.message, "error": job.error_message,
                        "redirect_url": job.redirect_url,
                        "file_groups": _public_file_groups(job),
                        "source_files_cleared": bool(job.source_files_cleared)})
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/extraction/<job_id>/clear-files", methods=["POST"])
def clear_extraction_source_files(job_id):
    """Delete one completed batch's source documents, preserving linked passport copies."""
    import shutil
    job = db.get_or_404(ExtractionJob, job_id)
    destination = job.redirect_url or url_for("main.extraction_job_page", job_id=job_id)
    if job.status != "COMPLETE" or job.entry_mode != "BATCH":
        flash("Source documents can only be cleared after a batch extraction is complete.", "warning")
        return redirect(destination)
    if job.source_files_cleared:
        flash("The batch source documents have already been cleared.", "info")
        return redirect(destination)
    uploads_root = Path(current_app.config["UPLOAD_DIR"]).resolve()
    source_root = Path(job.source_root or "").resolve()
    if not job.source_root or source_root == uploads_root or not source_root.is_relative_to(uploads_root):
        flash("The source folder could not be safely verified, so no files were removed.", "error")
        return redirect(destination)
    try:
        summary = json.loads(job.file_summary_json or "{}")
        applicant_ids = [int(x) for x in json.loads(job.applicant_ids_json or "[]")]
    except (TypeError, ValueError, json.JSONDecodeError):
        flash("The saved source-file list is invalid; no files were removed.", "error")
        return redirect(destination)
    linked = Applicant.query.filter(Applicant.id.in_(applicant_ids)).all() if applicant_ids else []
    passport_items = summary.get("passport_files", []) if isinstance(summary, dict) else []
    copy_root = uploads_root / "passport-copies"
    new_copies = []
    preserved = 0
    try:
        for item in passport_items:
            src = Path(item.get("path", "")).resolve()
            if not src.is_file() or not src.is_relative_to(source_root):
                continue
            applicant = next((a for a in linked if a.passport_file_path and
                              Path(a.passport_file_path).resolve() == src), None)
            if not applicant:
                continue
            copy_root.mkdir(parents=True, exist_ok=True)
            dest = copy_root / f"applicant-{applicant.id}-{uuid.uuid4().hex[:8]}-{secure_filename(src.name)}"
            shutil.copy2(src, dest)
            new_copies.append(dest)
            applicant.passport_file_path = str(dest)
            preserved += 1
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        for path in new_copies:
            try: path.unlink(missing_ok=True)
            except OSError: pass
        flash(f"Could not preserve linked passport copies; source files were not cleared ({type(exc).__name__}).", "error")
        return redirect(destination)
    try:
        if source_root.exists(): shutil.rmtree(source_root)
    except OSError as exc:
        flash(f"Passport copies were preserved, but source cleanup was incomplete: {exc}", "error")
        return redirect(destination)
    job.source_files_cleared = True
    db.session.commit()
    flash(f"Cleared batch source documents. Preserved {preserved} linked passport copy/copies for future email attachments; applicant records and preview remain.", "success")
    return redirect(destination)


@bp.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        kind = request.form.get("kind", "NEW").upper()
        company = (request.form.get("company", "LOLC") or "LOLC").upper()
        if kind not in ("NEW", "EXTEND") or company not in COMPANIES:
            flash("Choose a valid insurance type and company before uploading.", "error")
            return redirect(url_for("main.insurance_entry", kind="NEW", mode="SINGLE"))
        up = Path(current_app.config["UPLOAD_DIR"]) / uuid.uuid4().hex[:10]
        up.mkdir(parents=True, exist_ok=True)
        paths = {}
        keys = ["passport", "police", "application"] + (["policy_doc"] if kind == "EXTEND" else [])
        for key in keys:
            f = request.files.get(key)
            if f and f.filename:
                dest = up / key / secure_filename(f.filename)
                dest.parent.mkdir(parents=True, exist_ok=True)
                f.save(dest); paths[key] = dest
        if not paths.get("passport") or not paths.get("police"):
            flash("Please upload both the passport page and police report.", "error")
            return redirect(url_for("main.insurance_entry", kind=kind, mode="SINGLE", company=company))
        job = _create_extraction_job(company, kind, "SINGLE")
        app = current_app._get_current_object()
        _dispatch_extraction(app, job.job_id, _run_single_extraction_job, paths, company, kind,
                             request.form.get("apply_date", ""), request.form.get("embassy_date", ""),
                             request.form.get("telephone_manual", ""))
        if app.testing:
            db.session.expire_all()
            job = db.session.get(ExtractionJob, job.job_id)
            if job and job.status == "COMPLETE":
                return redirect(job.redirect_url)
        return redirect(url_for("main.extraction_job_page", job_id=job.job_id))
    kind = request.args.get("kind", "NEW").upper()
    return redirect(url_for("main.insurance_entry", kind=kind, mode="SINGLE"))

@bp.route("/applicants")
def applicants():
    from sqlalchemy import or_
    q = Applicant.query.order_by(Applicant.id.desc())
    status_filter = (request.args.get("status") or "ACTIVE").upper()
    if status_filter == "CANCELLED": q = q.filter(Applicant.status.like("CANCELLED_%"))
    elif status_filter not in ("", "ACTIVE", "ALL"): q = q.filter_by(status=status_filter)
    elif status_filter != "ALL": q = q.filter(~Applicant.status.like("CANCELLED_%"))
    if request.args.get("company"): q = q.filter_by(company=request.args["company"])
    term = (request.args.get("q") or "").strip()
    if term:
        like = f"%{term}%"
        q = q.filter(or_(Applicant.surname.ilike(like), Applicant.given_names.ilike(like),
                         Applicant.passport_no.ilike(like), Applicant.telephone.ilike(like),
                         Applicant.city.ilike(like)))
    return render_template("applicants.html", rows=q.limit(300).all(), search_term=term,
                           status_filter=status_filter)

@bp.route("/reviews")
def review_queue():
    kind = request.args.get("kind", "NEW").upper()
    mode = request.args.get("mode", "SINGLE").upper()
    status = request.args.get("status", "PENDING").upper()
    company = request.args.get("company", "").upper()
    if kind not in ("NEW", "EXTEND"): kind = "NEW"
    if mode not in ("SINGLE", "BATCH"): mode = "SINGLE"
    if status not in ("PENDING", "APPROVED"): status = "PENDING"
    if company not in COMPANIES: company = ""
    statuses = ["NEEDS_REVIEW", "EXTEND_DRAFT"] if status == "PENDING" else ["APPROVED"]
    company_counts = {}
    for name in COMPANIES:
        q = Applicant.query.filter_by(company=name, kind=kind, entry_mode=mode).filter(Applicant.status.in_(statuses))
        company_counts[name] = q.count()
    counts = {
        "PENDING": Applicant.query.filter(Applicant.kind == kind, Applicant.entry_mode == mode,
                    Applicant.status.in_(["NEEDS_REVIEW", "EXTEND_DRAFT"]),
                    *(([Applicant.company == company]) if company else [])).count(),
        "APPROVED": Applicant.query.filter(Applicant.kind == kind, Applicant.entry_mode == mode,
                    Applicant.status == "APPROVED", *(([Applicant.company == company]) if company else [])).count(),
    }
    rows, groups = [], []
    if company and mode == "SINGLE":
        rows = Applicant.query.filter_by(company=company, kind=kind, entry_mode=mode)\
            .filter(Applicant.status.in_(statuses)).order_by(Applicant.id.desc()).limit(500).all()
    elif company and mode == "BATCH":
        available_groups = ApplicantGroup.query.filter_by(company=company, kind=kind, entry_mode="BATCH")\
            .order_by(ApplicantGroup.group_number.asc()).all()
        for group in available_groups:
            members = [a for a in group.applicants if a.status in statuses]
            if not members: continue
            groups.append({"group": group, "rows": sorted(members, key=lambda a: a.id),
                           "pending": sum(a.status in ("NEEDS_REVIEW", "EXTEND_DRAFT") for a in group.applicants),
                           "approved": sum(a.status == "APPROVED" for a in group.applicants)})
    return render_template("review_queue.html", rows=rows, groups=groups, company=company,
                           company_counts=company_counts, companies=COMPANIES, kind=kind, mode=mode,
                           status=status, counts=counts)


@bp.route("/review/groups/<int:group_id>")
def review_group(group_id):
    group = db.get_or_404(ApplicantGroup, group_id)
    status = request.args.get("status", "PENDING").upper()
    if status not in ("PENDING", "APPROVED"): status = "PENDING"
    statuses = ["NEEDS_REVIEW", "EXTEND_DRAFT"] if status == "PENDING" else ["APPROVED"]
    members = Applicant.query.filter_by(applicant_group_id=group.id).order_by(Applicant.id.asc()).all()
    rows = [a for a in members if a.status in statuses]
    columns = COMPANIES[group.company]["columns_new" if group.kind == "NEW" else "columns_extend"]
    company_cfg = cfg().get("companies", {}).get(group.company, {})
    row_defaults = {**company_cfg.get("defaults", {})}
    sheet_rows = [{"applicant": a, "row": _row_for(a.as_dict(), group.company, group.kind, row_defaults)} for a in rows]
    return render_template("review_group.html", group=group, status=status,
                           rows=rows, sheet_rows=sheet_rows, sheet_columns=columns)


@bp.route("/review/<int:aid>", methods=["GET", "POST"])
def review(aid):
    a = db.get_or_404(Applicant, aid)
    if a.status.startswith("CANCELLED_"):
        flash("Restore this cancelled applicant before editing or approving it.", "warning")
        return redirect(url_for("main.applicants", status="CANCELLED"))
    if request.method == "POST":
        try:
            sheet_row = json.loads(request.form.get("sheet_row_json", "{}"))
            if not isinstance(sheet_row, dict): raise ValueError("The insurer row must be a JSON object.")
        except (TypeError, ValueError, json.JSONDecodeError):
            flash("The insurer preview edits could not be read. Please re-open Review and try again.", "error")
            return redirect(url_for("main.review", aid=a.id))
        columns = COMPANIES[a.company]["columns_new" if a.kind == "NEW" else "columns_extend"]
        mapped = {
            "ISSUE DATE": "issue_date", "SALUTATION": "salutation", "GENDER": "gender",
            "FIRST NAME": "surname", "LAST NAME": "given_names", "PASSPORT NO": "passport_no",
            "BIRTHDAY": "birthday", "DEPATURE DATE": "departure_date", "ARRIVAL DATE": "arrival_date",
            "NO.": "addr_no", "STREET": "addr_street", "CITY": "city", "CONTACT NO": "telephone",
        }
        overrides = {}
        for column in columns:
            value = str(sheet_row.get(column, "") or "").strip()
            if column == "ISSUE DATE" and a.company == "LOLC" and a.kind == "EXTEND":
                a.issue_date = _sheet_date_value(value, a.company, column)
                overrides[column] = _sheet_date(a.issue_date, a.company, column)
                continue
            if column in mapped:
                attr = mapped[column]
                if attr in {"issue_date", "birthday", "departure_date", "arrival_date"}:
                    setattr(a, attr, _sheet_date_value(value, a.company, column))
                elif attr == "telephone":
                    a.telephone = normalize_phone(value)
                elif attr in {"surname", "given_names", "passport_no"}:
                    setattr(a, attr, value.upper())
                else:
                    setattr(a, attr, value)
            elif column in {"E-TRAVEL POLICY NO", "INSURANCE POLICY NO"}:
                a.existing_policy_no = value
                if a.kind == "NEW": overrides[column] = value
            else:
                overrides[column] = value
        a.sheet_overrides_json = json.dumps(overrides, ensure_ascii=False)
        if "attach_passport_copy" in request.form:
            a.attach_passport_copy = "1" in request.form.getlist("attach_passport_copy")

        missing = [k for k in ("surname", "given_names", "passport_no", "city", "salutation", "gender")
                   if not getattr(a, k)] + ([] if a.birthday else ["birthday"]) \
                  + ([] if a.departure_date else ["departure_date"])
        if a.company == "ALLIANZ" and not a.telephone: missing.append("telephone (Allianz CONTACT NO)")
        if a.kind == "EXTEND" and a.company == "ALLIANZ" and not a.existing_policy_no: missing.append("existing_policy_no (E-TRAVEL POLICY NO)")
        if a.kind == "EXTEND" and a.company == "LOLC" and not a.existing_policy_no: missing.append("existing_policy_no (INSURANCE POLICY NO)")
        approve = request.form.get("approve") == "1"
        final_checked = request.form.get("final_check") == "1"
        approved_now = False
        if approve and not final_checked:
            flash("Tick the final-check box after verifying the editable insurer row.", "error")
        elif approve and missing:
            flash("Cannot approve — missing: " + ", ".join(missing), "error")
        elif approve:
            approved_now = a.status != "APPROVED"
            a.status = "APPROVED"
            flash("Final check passed. Approved ✔ Ready to batch.", "success")
        else:
            flash("Insurer row edits saved.", "success")
        db.session.commit()
        if approved_now:
            if _auto_send_setting():
                day=_batch_group_date(a.kind,a)
                pending=_pending_in_group(a.company,a.kind,a.entry_mode,day) if a.entry_mode=="BATCH" and day else []
                if pending:
                    flash(f"Approved and queued. Auto-send is waiting for {len(pending)} same-date applicant(s) to be reviewed.","warning")
                else:
                    prepared=None
                    try:
                        prepared=_prepare_batch(a.company,a.kind,a.entry_mode,day,[a.id] if a.entry_mode=="SINGLE" else None)
                        _send_batch_now(prepared)
                        flash("Approved, Excel prepared, and email automatically sent to the insurer.","success")
                    except Exception as e:
                        if prepared: _log_send_failure(prepared,e)
                        flash(f"Approved, but automatic email preparation/sending failed: {e}","error")
            else:
                flash("Approved and added to the Email queue.","success")
            if a.applicant_group_id:
                remaining = Applicant.query.filter_by(applicant_group_id=a.applicant_group_id)\
                    .filter(Applicant.status.in_(["NEEDS_REVIEW", "EXTEND_DRAFT"])).count()
                return redirect(url_for("main.review_group", group_id=a.applicant_group_id,
                                        status="PENDING" if remaining else "APPROVED"))
            return redirect(url_for("main.review_queue", kind=a.kind, mode=a.entry_mode,
                                    status="APPROVED", company=a.company))
        return redirect(url_for("main.review", aid=a.id))
    conf = json.loads(a.confidence_json or "{}")
    company_cfg = cfg().get("companies", {})
    preview_columns = COMPANIES[a.company]["columns_new" if a.kind == "NEW" else "columns_extend"]
    preview_cfg_defaults = {company: {
        **company_cfg.get(company, {}).get("defaults", {}),
        "date_sep": company_cfg.get(company, {}).get("date_sep", COMPANIES[company]["date_sep"]),
    } for company in COMPANIES}
    preview_row = _row_for(a.as_dict(), a.company, a.kind, preview_cfg_defaults[a.company])
    raw_fields = {
        "ISSUE DATE": a.issue_date, "SALUTATION": a.salutation, "GENDER": a.gender,
        "FIRST NAME": a.surname, "LAST NAME": a.given_names, "PASSPORT NO": a.passport_no,
        "BIRTHDAY": a.birthday, "DEPATURE DATE": a.departure_date, "ARRIVAL DATE": a.arrival_date,
        "NO.": a.addr_no, "STREET": a.addr_street, "CITY": a.city,
        "CONTACT NO": a.telephone,
    }
    preview_inputs = {column: (value.isoformat() if hasattr(value, "isoformat") else (value or ""))
                      for column, value in preview_row.items()}
    date_columns = {"ISSUE DATE", "BIRTHDAY", "DEPATURE DATE", "ARRIVAL DATE"}
    for column, value in raw_fields.items():
        if column in preview_columns and column not in date_columns:
            preview_inputs[column] = value.isoformat() if hasattr(value, "isoformat") else (value or "")
    preview_formats = {company: {
        "NEW": COMPANIES[company]["columns_new"],
        "EXTEND": COMPANIES[company]["columns_extend"],
        "date_order": COMPANIES[company]["date_order"],
        "date_sep": preview_cfg_defaults[company]["date_sep"],
    } for company in COMPANIES}
    preview_defaults = {company: {**COMPANIES[company].get("defaults", {}),
                                  **company_cfg.get(company, {}).get("defaults", {})}
                        for company in COMPANIES}
    return render_template("review.html", a=a, conf=conf,
                           threshold=float(cfg().get("ocr", {}).get("confidence_threshold", 0.82)),
                           preview_columns=preview_columns, preview_row=preview_row,
                           preview_inputs=preview_inputs, preview_formats=preview_formats,
                           preview_defaults=preview_defaults)


@bp.route("/applicants/<int:aid>/cancel", methods=["POST"])
def cancel_applicant(aid):
    """Soft-remove an applicant from active tables while retaining an audit record."""
    a = db.get_or_404(Applicant, aid)
    if a.status.startswith("CANCELLED_"):
        flash("This applicant is already cancelled.", "warning")
    elif a.batch_id is not None or a.status not in ("NEEDS_REVIEW", "EXTEND_DRAFT", "APPROVED"):
        flash("This applicant cannot be cancelled from here because it is already attached to a prepared/sent workflow.", "error")
    else:
        a.status = "CANCELLED_" + a.status
        db.session.commit()
        flash("Applicant cancelled and removed from active review/email tables. The record remains in Applicants → Cancelled.", "success")
    next_url = request.form.get("next_url", "")
    if next_url.startswith("/insurance/") and not next_url.startswith("//"):
        return redirect(next_url)
    if a.applicant_group_id:
        return redirect(url_for("main.review_group", group_id=a.applicant_group_id,
                                status="APPROVED" if a.status.endswith("APPROVED") else "PENDING"))
    return redirect(url_for("main.review_queue", kind=a.kind, mode=a.entry_mode,
                            company=a.company,
                            status="APPROVED" if a.status.endswith("APPROVED") else "PENDING"))


@bp.route("/applicants/<int:aid>/restore", methods=["POST"])
def restore_applicant(aid):
    a = db.get_or_404(Applicant, aid)
    if a.status.startswith("CANCELLED_") and a.batch_id is None:
        old_status = a.status.removeprefix("CANCELLED_")
        if old_status in ("NEEDS_REVIEW", "EXTEND_DRAFT", "APPROVED"):
            a.status = old_status
            db.session.commit()
            flash("Applicant restored to the previous active status.", "success")
        else:
            flash("The previous applicant status could not be restored safely.", "error")
    else:
        flash("This applicant is not eligible for restore.", "error")
    return redirect(url_for("main.applicants", status="CANCELLED"))


@bp.route("/emails", methods=["GET"])
def emails():
    company_filter = request.args.get("company", "ALL").upper()
    kind_filter = request.args.get("kind", "ALL").upper()
    mode_filter = request.args.get("mode", "ALL").upper()
    if company_filter not in ("ALL", *COMPANIES): company_filter = "ALL"
    if kind_filter not in ("ALL", "NEW", "EXTEND"): kind_filter = "ALL"
    if mode_filter not in ("ALL", "SINGLE", "BATCH"): mode_filter = "ALL"
    query = Applicant.query.filter_by(status="APPROVED", batch_id=None)
    if company_filter != "ALL": query = query.filter_by(company=company_filter)
    if kind_filter != "ALL": query = query.filter_by(kind=kind_filter)
    if mode_filter != "ALL": query = query.filter_by(entry_mode=mode_filter)
    approved = query.order_by(Applicant.id.desc()).all()
    grouped = {}
    for a in approved:
        day = a.issue_date if a.kind == "NEW" else a.embassy_appointment_date
        mode = (a.entry_mode or "SINGLE").upper()
        key = (a.company, a.kind, mode, day, a.id if mode == "SINGLE" else None)
        grouped.setdefault(key, []).append(a)
    pending_groups = [{"company":k[0],"kind":k[1],"mode":k[2],"day":k[3],"rows":v,
                       "waiting_review":len(_pending_in_group(k[0],k[1],k[2],k[3])) if k[2]=="BATCH" and k[3] else 0}
                      for k,v in grouped.items()]
    pending_groups.sort(key=lambda g:(g["kind"],g["mode"],g["company"],g["day"] or date.max))
    batches = Batch.query.order_by(Batch.id.desc()).limit(200).all()
    sent_count = Batch.query.filter_by(status="SENT").count()
    pending_count = len(pending_groups) + Batch.query.filter(Batch.status.in_(["DRAFT","ERROR"])).count()
    failed_count = Batch.query.filter_by(status="ERROR").count()
    setting = db.session.get(AgentSetting,"AUTO_SEND_AFTER_PREPARE")
    auto_send = setting.value == "1" if setting else bool(cfg().get("email",{}).get("auto_send_after_prepare",False))
    return render_template("emails.html", groups=pending_groups, batches=batches,
                           sent_count=sent_count, pending_count=pending_count,
                           failed_count=failed_count, auto_send=auto_send,
                           companies_cfg=cfg().get("companies", {}),
                           company_options=list(COMPANIES), company_filter=company_filter,
                           kind_filter=kind_filter, mode_filter=mode_filter)

@bp.route("/emails/auto-send", methods=["POST"])
def set_auto_send():
    enabled = request.form.get("enabled") == "1"
    row = db.session.get(AgentSetting,"AUTO_SEND_AFTER_PREPARE")
    if row is None:
        row = AgentSetting(key="AUTO_SEND_AFTER_PREPARE", value="1" if enabled else "0")
        db.session.add(row)
    else: row.value = "1" if enabled else "0"
    db.session.commit()
    flash("Automatic sending after approval is " + ("ON" if enabled else "OFF"), "warning" if enabled else "success")
    return redirect(url_for("main.emails"))

def _batch_group_date(kind, applicant):
    return applicant.issue_date if kind == "NEW" else applicant.embassy_appointment_date


def _pending_in_group(company, kind, mode, day):
    if not day: return []
    q=Applicant.query.filter_by(company=company,kind=kind,entry_mode=mode)\
        .filter(Applicant.status.in_(["NEEDS_REVIEW","EXTEND_DRAFT"]))
    if kind=="NEW": q=q.filter(Applicant.issue_date==day)
    else: q=q.filter(Applicant.embassy_appointment_date==day)
    return q.all()


def _prepare_batch(company, kind, mode, day, ids=None):
    if not day: raise ValueError("A valid issue date (NEW) or synced interview date (EXTEND) is required.")
    if mode not in ("SINGLE","BATCH"): raise ValueError("Choose Single or Batch mode.")
    rows=Applicant.query.filter_by(company=company,kind=kind,status="APPROVED",entry_mode=mode,batch_id=None).all()
    rows=[r for r in rows if _batch_group_date(kind,r)==day]
    if ids: rows=[r for r in rows if str(r.id) in set(map(str,ids))]
    if mode=="SINGLE":
        if not ids: raise ValueError("Choose the single approved applicant to prepare.")
        rows=rows[:1]
    if not rows: raise ValueError("No unbatched approved applicants match this insurer, type, mode, and date.")
    if mode=="BATCH":
        remaining=_pending_in_group(company,kind,mode,day)
        if remaining:
            raise ValueError(f"Waiting for review/approval of {len(remaining)} other same-date applicant(s) before preparing the batch.")
    if kind=="EXTEND":
        for r in rows:
            if not r.embassy_appointment_date: raise ValueError(f"Applicant #{r.id} is waiting for an interview-date sync.")
            dd=apply_date_rules("EXTEND",appointment=r.embassy_appointment_date)
            r.departure_date,r.arrival_date=dd["departure_date"],dd["arrival_date"]
    mode_suffix=f"_SINGLE_{rows[0].id}" if mode=="SINGLE" else ""
    filename=f"{company}_{kind}_{day.isoformat()}{mode_suffix}.xlsx"
    b=Batch(company=company,kind=kind,submission_mode=mode,batch_date=day)
    db.session.add(b);db.session.flush()
    for r in rows:r.batch_id=b.id;r.status="EXCEL_GENERATED"
    comp_cfg=cfg().get("companies",{}).get(company,{})
    out=Path(current_app.config["OUTPUT_DIR"])/filename
    b.excel_path=str(generate_excel([r.as_dict() for r in rows],company,kind,matcher().cities,out,
        {**comp_cfg.get("defaults",{}),"date_sep":comp_cfg.get("date_sep","/")}))
    b.email_to=comp_cfg.get("email_to","")
    subj=comp_cfg.get("subject_new" if kind=="NEW" else "subject_extend","Insurance - {date}")
    b.email_subject=subj.format(date=day.isoformat(),count=len(rows))
    db.session.commit()
    return b


def _auto_send_setting():
    setting=db.session.get(AgentSetting,"AUTO_SEND_AFTER_PREPARE")
    return setting.value=="1" if setting else bool(cfg().get("email",{}).get("auto_send_after_prepare",False))


@bp.route("/batches", methods=["GET", "POST"])
def batches():
    if request.method=="GET": return redirect(url_for("main.emails"))
    company=request.form.get("company","LOLC").upper();kind=request.form.get("kind","NEW").upper()
    mode=request.form.get("submission_mode","BATCH").upper();day=to_date(request.form.get("batch_date",""))
    ids=request.form.getlist("ids")
    try:
        b=_prepare_batch(company,kind,mode,day,ids)
    except Exception as e:
        flash(str(e),"warning");return redirect(url_for("main.emails"))
    if _auto_send_setting():
        import shutil
        temp_dir=Path(current_app.config["BASE"])/"instance"/("auto-send-"+uuid.uuid4().hex[:10])
        temp_dir.mkdir(parents=True,exist_ok=True);copies=[]
        try:
            for i,f in enumerate(request.files.getlist("passport_attachments"),1):
                if f and f.filename:
                    dest=temp_dir/f"{i:03d}-{secure_filename(f.filename) or f'passport-{i}.pdf'}";f.save(dest);copies.append(str(dest))
            _send_batch_now(b,copies)
            flash(f"Prepared and automatically sent {Path(b.excel_path).name} to {b.email_to}.","success")
        except Exception as e:
            b.status="ERROR";_log_send_failure(b,e)
            flash(f"Excel prepared, but automatic sending failed: {e}","error")
        finally: shutil.rmtree(temp_dir,ignore_errors=True)
    else:
        flash(f"Email job prepared: {Path(b.excel_path).name} ({len(b.applicants)} applicants).", "success")
    return redirect(url_for("main.emails"))

@bp.route("/download/<int:bid>")
def download(bid):
    b = db.get_or_404(Batch, bid)
    return send_file(b.excel_path, as_attachment=True)

def _batch_email_body(b, applicants, passports_attached=False):
    names_new = "\n".join(f"{i:02d}. {(a.surname + ' ' + a.given_names).strip()}" for i,a in enumerate(applicants,1))
    names_lolc = "\n".join(f"{i:02d}.{(a.surname + ' ' + a.given_names).strip()}" for i,a in enumerate(applicants,1))
    if b.company == "ALLIANZ" and b.kind == "NEW":
        return ("Good Day,\n\n\nHere We have sent the Insurance Application for applying for new insurances with this email. "
                "please find the attachment below\n\n" + names_new + "\n\nSincerely,\n\nY.L.Pradeep Nishantha\n"
                "(Managing Director & CEO)\nTrans Arabian (PVT) Ltd\nTP:0094777713945\n"
                "Email:transarabian@yahoo.com\nwww.transarabian.lk")
    if b.company == "LOLC" and b.kind == "NEW":
        clause=("Here We have attached the excel and passport copies of below mentioned candidates for applying for new insurance policies with this email. please find the attachments below."
                if passports_attached else "Here We have attached the excel of below mentioned candidates for applying for new insurance policies with this email. please find the attachment below.")
        return ("Good Day,\n\n\n"+clause+"\n\n"+names_lolc+"\n\nSincerely,\n\nY.L.Pradeep Nishantha\n"
                "(Managing Director & CEO)\nTrans Arabian (PVT) Ltd\nTP:0094777713945\n"
                "Email:transarabian@yahoo.com\nwww.transarabian.lk")
    if b.kind == "EXTEND" and b.company in ("ALLIANZ","LOLC"):
        departure=applicants[0].departure_date
        if not departure: raise RuntimeError("EXTEND departure date is missing.")
        departure_date=departure.strftime("%B %d, %Y").upper()
        greeting="Dear Allianz Insurance Team," if b.company=="ALLIANZ" else "Dear Team,"
        names=("" if b.company=="ALLIANZ" else names_lolc+"\n\n")
        receipt=("Please confirm receipt of this email and let us know if any further steps, documentation, or payments are required on our end to finalize the issuance of the new policies.\n\n"
                 if b.company=="ALLIANZ" else "")
        return (f"{greeting}\n\nI am writing to request to amend our travel insurance policies. For your convenience, "
                "the document with the full details of all applicants and policy numbers is attached again to this email.\n\n"
                f"We kindly request you to issue new insurance policies for all applicants listed in the attachment, with the departure date set as {departure_date}.\n\n"
                +names+"The timely issuance of these policies is critical for our visa application process. Therefore, we would be extremely grateful if you could process this request at your earliest possible convenience.\n\n"
                "We have attached an excel of whose insurance policy need to be issued. Please find the Attachment below.\n\n"
                +receipt+"Thank you for your prompt attention to this important matter.\n\nSincerely,\n\nY.L.Pradeep Nishantha\n"
                "(Managing Director & CEO)\nTrans Arabian (PVT) Ltd\nTP:0094777713945\n"
                "Email:transarabian@yahoo.com\nwww.transarabian.lk")
    action="new travel insurance applications" if b.kind=="NEW" else "travel insurance extension requests"
    return (f"Good Day,\n\nPlease find attached the Excel file for our {action}.\n\nSincerely,\n"
            "Y.L.Pradeep Nishantha\n(Managing Director & CEO)\nTrans Arabian (PVT) Ltd\nTP:0094777713945\n"
            "Email:transarabian@yahoo.com\nwww.transarabian.lk")


def _send_batch_now(b, passport_paths=()):
    comp_cfg=cfg().get("companies",{}).get(b.company,{})
    ecfg=cfg().get("email",{})
    applicants=sorted(b.applicants,key=lambda a:(a.surname or "",a.given_names or ""))
    if not applicants: raise RuntimeError("Email job has no applicants.")
    if b.kind=="EXTEND":
        appt=applicants[0].embassy_appointment_date
        dep=applicants[0].departure_date
        arrival=applicants[0].arrival_date
        if (not appt or not dep or not arrival
                or any(a.embassy_appointment_date!=appt or a.departure_date!=dep
                       or a.arrival_date!=arrival for a in applicants)):
            raise RuntimeError("EXTEND email job must contain one shared interview date and matching calculated departure/arrival dates.")
    saved_paths=[a.passport_file_path for a in applicants if a.attach_passport_copy and a.passport_file_path and Path(a.passport_file_path).is_file()]
    all_passports=list(dict.fromkeys([*saved_paths,*passport_paths]))
    body=_batch_email_body(b,applicants,bool(all_passports))
    to=b.email_to or comp_cfg.get("email_to","")
    attachments=[b.excel_path,*all_passports]
    if ecfg.get("mode")=="playwright":
        from playwright_agent.webmail_agent import send_via_webmail
        send_via_webmail(ecfg.get("webmail","gmail"),to,b.email_subject,body,attachments)
    else:
        from .email_agent.smtp_imap import send_email
        send_email(ecfg,to,b.email_subject,body,attachments,cc=comp_cfg.get("email_cc",""))
    from datetime import datetime
    b.sent_at=datetime.now(); b.status="SENT"
    for a in b.applicants: a.status="AWAITING_REPLY"
    db.session.add(EmailLog(direction="OUT",to=to,subject=b.email_subject,kind="SEND",body_snippet=body[:500]))
    db.session.commit()
    notify(f"📧 Sent {b.company} {b.kind} email #{b.id}",f"{len(applicants)} applicant(s) → {to}","success",cfg().get("notify"))
    return body


def _log_send_failure(b, error):
    b.status="ERROR"
    to=b.email_to or cfg().get("companies",{}).get(b.company,{}).get("email_to","")
    db.session.add(EmailLog(direction="OUT",to=to,subject=b.email_subject,kind="SEND_FAILED",body_snippet=str(error)[:500]))
    db.session.commit()
    notify(f"⚠ Email send failed: {b.company} {b.kind} job #{b.id}",str(error),"error",cfg().get("notify"))


@bp.route("/send/<int:bid>", methods=["POST"])
def send_batch(bid):
    """Send a prepared email job and optionally selected passport-copy files."""
    import shutil
    b=db.get_or_404(Batch, bid)
    if b.status=="SENT":
        flash("This email job is already marked sent.","warning"); return redirect(url_for("main.emails"))
    temp_dir=Path(current_app.config["BASE"])/"instance"/("send-"+uuid.uuid4().hex[:12])
    temp_dir.mkdir(parents=True,exist_ok=True); passport_paths=[]
    for i,f in enumerate(request.files.getlist("passport_attachments"),1):
        if f and f.filename:
            dest=temp_dir/f"{i:03d}-{secure_filename(f.filename) or f'passport-{i}.pdf'}"
            f.save(dest); passport_paths.append(str(dest))
    try:
        _send_batch_now(b,passport_paths)
        flash("Email sent ✔ The inbox watcher will notify you when the insurer replies.","success")
    except Exception as e:
        _log_send_failure(b,e)
        flash(f"Send failed: {e}","error")
    finally:
        shutil.rmtree(temp_dir,ignore_errors=True)
    return redirect(url_for("main.emails"))

@bp.route("/inbox/check", methods=["POST"])
def inbox_check():
    from .inbox_service import process_inbox
    res = process_inbox()
    flash(f"Inbox checked: {res['emails']} new email(s), {res['policies']} polic(ies) matched, "
          f"{res['appointments']} appointment(s).", "success")
    return redirect(url_for("main.dashboard"))

@bp.route("/appointments", methods=["GET", "POST"])
def appointments():
    flash("Interview dates are managed in your recruitment system and synced here to calculate EXTEND dates.", "info")
    return redirect(url_for("main.dashboard"))


@bp.route("/notifications")
def notifications():
    rows = Notification.query.order_by(Notification.id.desc()).limit(100).all()
    Notification.query.filter_by(seen=False).update({"seen": True}); db.session.commit()
    return render_template("notifications.html", rows=rows)


@bp.route("/notifications/clear", methods=["POST"])
def clear_notifications():
    """Clear notification alerts only; applicant and email records are untouched."""
    cleared = Notification.query.delete(synchronize_session=False)
    db.session.commit()
    flash(f"Cleared {cleared} notification(s).", "success")
    return redirect(url_for("main.notifications"))


@bp.route("/settings")
def settings():
    import yaml as _y
    return render_template("settings.html", cfg_text=_y.safe_dump(cfg(), sort_keys=False, allow_unicode=True))


def _apply_policy_documents(applicants, policy_docs):
    """Attach extracted old policy numbers by passport number, falling back to
    upload order only when there is an unambiguous one-to-one pairing."""
    assigned_ids, used_doc_indexes = set(), set()
    extracted = [(i, d) for i, d in enumerate(policy_docs) if d.get("existing_policy_no")]
    for i, doc in extracted:
        passport_no = (doc.get("_policy_passport_no") or "").upper().replace(" ", "")
        if not passport_no:
            continue
        target = next((a for a in applicants
                       if (a.passport_no or "").upper().replace(" ", "") == passport_no
                       and a.id not in assigned_ids and not a.existing_policy_no), None)
        if target:
            target.existing_policy_no = doc["existing_policy_no"]
            assigned_ids.add(target.id); used_doc_indexes.add(i)
    # Only order-pair documents which did not contain a passport identifier.
    # A mismatching explicit passport number is never silently assigned.
    unnumbered = [(i, d) for i, d in extracted
                  if i not in used_doc_indexes and not d.get("_policy_passport_no")]
    available = [a for a in applicants if a.id not in assigned_ids and not a.existing_policy_no]
    if unnumbered and len(unnumbered) == len(available):
        for (i, doc), applicant in zip(unnumbered, available):
            applicant.existing_policy_no = doc["existing_policy_no"]
            assigned_ids.add(applicant.id); used_doc_indexes.add(i)
    if assigned_ids:
        db.session.commit()
    return len(assigned_ids), len(extracted)


def _run_bulk_extraction_job(app, job_id: str, file_paths: dict, company: str, kind: str,
                             apply_date: str, embassy_date: str) -> None:
    with app.app_context():
        try:
            job = db.session.get(ExtractionJob, job_id)
            if not job: return
            if file_paths.get("combined_files"):
                _job_progress(job, 3, "Classifying the selected documents with local OCR.")
                from .batch_documents import CATEGORIES, classify_combined_files
                grouped = classify_combined_files(file_paths["combined_files"], kind)
                job.file_summary_json = json.dumps(grouped, ensure_ascii=False)
                for category in CATEGORIES:
                    file_paths[category] = [item["path"] for item in grouped.get(category, [])]
                db.session.commit()
            passport_paths = file_paths.get("passport_files", [])
            police_paths = file_paths.get("police_files", [])
            application_paths = file_paths.get("application_files", [])
            policy_paths = file_paths.get("policy_files", []) if kind == "EXTEND" else []
            if not passport_paths and not police_paths:
                raise RuntimeError("No passport photo page or police report was identified. Rename the files clearly and submit again; the saved source files were kept.")
            _job_progress(job, 5, "Reading passport documents locally.")
            passports = []
            for index, passport_path in enumerate(passport_paths):
                pg = process_documents(passport_path=passport_path, city_matcher=matcher(),
                                       ocr_cfg=cfg().get("ocr", {}))
                pg["_passport_file_path"] = str(passport_path)
                passports.append(pg)
                _job_progress(job, min(25, 8 + int(17 * (index + 1) / max(len(passport_paths), 1))),
                              f"Read passport {index + 1} of {len(passport_paths)}.")
            applications = []
            for index, application_path in enumerate(application_paths):
                applications.append(process_documents(application_path=application_path,
                    city_matcher=matcher(), ocr_cfg=cfg().get("ocr", {})))
                _job_progress(job, min(38, 26 + int(12 * (index + 1) / max(len(application_paths), 1))),
                              f"Read applicant document {index + 1} of {len(application_paths)}.")
            policy_docs = []
            for index, policy_path in enumerate(policy_paths):
                try:
                    policy_docs.append(process_documents(policy_document_path=policy_path,
                        city_matcher=matcher(), ocr_cfg=cfg().get("ocr", {})))
                except Exception:
                    app.logger.exception("Batch policy document extraction failed")
                    policy_docs.append({})
                _job_progress(job, min(48, 39 + int(8 * (index + 1) / max(len(policy_paths), 1))),
                              f"Read policy document {index + 1} of {len(policy_paths)}.")
            _job_progress(job, 50, "Extracting police-report addresses and matching CITY values.")
            police_records = []
            for index, police_path in enumerate(police_paths):
                police_records.append(process_documents(police_path=police_path, city_matcher=matcher(),
                                                        ocr_cfg=cfg().get("ocr", {})))
                _job_progress(job, min(75, 50 + int(25 * (index + 1) / max(len(police_paths), 1))),
                              f"Read police report {index + 1} of {len(police_paths)}.")

            by_ppno = {(g.get("passport_no") or "").upper().replace(" ", ""): g
                       for g in passports if g.get("passport_no")}
            app_by_ppno = {(g.get("_application_passport_no") or "").upper().replace(" ", ""): g
                           for g in applications if g.get("_application_passport_no")}
            queue = list(passports)
            records = []
            for pi, got in enumerate(police_records):
                got_ppno = (got.get("passport_no") or "").upper().replace(" ", "")
                pg = by_ppno.get(got_ppno)
                if pg is None and len(passport_paths) == len(police_paths) and queue:
                    pg = queue[0]
                app_ppno = got_ppno or ((pg.get("passport_no") or "").upper().replace(" ", "") if pg else "")
                application = app_by_ppno.get(app_ppno)
                if (application is None and pi < len(applications) and len(applications) == len(police_paths)
                        and not applications[pi].get("_application_passport_no")):
                    application = applications[pi]
                if application and application.get("telephone"):
                    got["telephone"] = application["telephone"]
                    got.setdefault("confidence", {})["telephone"] = application.get("confidence", {}).get("telephone", 0.8)
                if pg is not None:
                    if pg in queue: queue.remove(pg)
                    # Passport identity fields supplement the police row, but
                    # Salutation/Gender remain sourced only from the police report.
                    for field in ("surname", "given_names", "passport_no", "birthday"):
                        if pg.get(field): got[field] = pg[field]
                    got["_passport_file_path"] = pg.get("_passport_file_path", "")
                    got.setdefault("confidence", {}).update(pg.get("confidence", {}))
                records.append(got)
            for pg in queue:
                # A passport without a paired police report has no approved source
                # for these fields under the current extraction rule.
                pg["salutation"] = ""
                pg["gender"] = ""
                records.append(pg)

            applicant_group = _create_applicant_group(company, kind)
            created = []
            for got in records:
                got["_passport_file_path"] = got.get("_passport_file_path", "")
                applicant = _create_applicant(got, company, kind, apply_date, embassy_date,
                                              entry_mode="BATCH")
                applicant.applicant_group_id = applicant_group.id
                created.append(applicant)
            db.session.commit()
            policy_matched, policy_extracted = _apply_policy_documents(created, policy_docs)
            message = f"Extraction complete. {len(created)} applicant(s) added to {company} {kind} Group {applicant_group.group_number:02d}."
            try:
                file_summary = json.loads(job.file_summary_json or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                file_summary = {}
            counts = [f"{len(file_summary.get(category, []))} {label.lower()}"
                      for category, label in (("passport_files", "passport pages"),
                                               ("police_files", "police reports"),
                                               ("application_files", "applicant/phone documents"),
                                               ("policy_files", "policy documents"))
                      if file_summary.get(category)]
            if counts:
                message += " Auto-classified: " + ", ".join(counts) + "."
            low_confidence = [item.get("name", "document") for entries in file_summary.values()
                              if isinstance(entries, list) for item in entries
                              if isinstance(item, dict) and item.get("low_confidence")]
            if low_confidence:
                message += f" {len(low_confidence)} file(s) had low-confidence category detection; verify the grouped document list and Review fields."
            if policy_paths:
                if policy_extracted:
                    message += f" Read {policy_extracted} policy document(s); matched {policy_matched}. Verify policy numbers in Review."
                else:
                    message += " No policy numbers were confidently detected; verify them in Review."
            _finish_extraction_job(job_id, [a.id for a in created], company, kind, "BATCH", message)
        except Exception as exc:
            app.logger.error("Batch extraction failed (%s): %s", job_id, exc,
                             exc_info=(type(exc), exc, exc.__traceback__))
            _fail_extraction_job(app, job_id, exc)


@bp.route("/upload/bulk", methods=["GET", "POST"])
def upload_bulk():
    """Save a batch upload immediately; perform OCR in a background worker."""
    import shutil
    if request.method == "POST":
        kind = request.form.get("kind", "NEW").upper()
        company = (request.form.get("company", "LOLC") or "LOLC").upper()
        if kind not in ("NEW", "EXTEND") or company not in COMPANIES:
            flash("Choose a valid insurance type and company before uploading.", "error")
            return redirect(url_for("main.insurance_entry", kind="NEW", mode="BATCH"))
        up = Path(current_app.config["UPLOAD_DIR"]) / ("bulk-" + uuid.uuid4().hex[:10])
        up.mkdir(parents=True, exist_ok=True)
        file_paths = {"source_root": str(up)}
        def save_all(key):
            saved = []
            for index, f in enumerate(request.files.getlist(key), start=1):
                if f and f.filename:
                    safe_name = secure_filename(f.filename) or f"document-{index}.pdf"
                    dest = up / key / f"{index:03d}" / safe_name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    f.save(dest); saved.append(str(dest))
            return saved
        # New UI submits one mixed-document selection. Keep legacy category fields
        # accepted for bookmarked forms and existing integration clients.
        file_paths["combined_files"] = save_all("batch_files")
        file_paths["police_files"] = save_all("police_files")
        file_paths["passport_files"] = save_all("passport_files")
        file_paths["application_files"] = save_all("application_files")
        file_paths["policy_files"] = save_all("policy_files") if kind == "EXTEND" else []
        if not any(file_paths.get(key) for key in ("combined_files", "police_files", "passport_files")):
            shutil.rmtree(up, ignore_errors=True)
            flash("Select at least one document to classify (passport, police, applicant/phone, or EXTEND policy).", "error")
            return redirect(url_for("main.insurance_entry", kind=kind, mode="BATCH", company=company))
        job = _create_extraction_job(company, kind, "BATCH")
        job.source_root = str(up)
        if not file_paths["combined_files"]:
            from .batch_documents import CATEGORIES
            mapping = {"passport_files":"passport_files", "police_files":"police_files",
                       "application_files":"application_files", "policy_files":"policy_files"}
            summary = {category: [{"name": Path(p).name, "path": p, "confidence": 1.0,
                                   "score": 10, "reason": "Selected in this category", "low_confidence": False}
                                  for p in file_paths.get(field, [])]
                       for category, field in mapping.items()}
            job.file_summary_json = json.dumps(summary)
        db.session.commit()
        app = current_app._get_current_object()
        _dispatch_extraction(app, job.job_id, _run_bulk_extraction_job, file_paths, company, kind,
                             request.form.get("apply_date", ""), request.form.get("embassy_date", ""))
        if app.testing:
            db.session.expire_all()
            job = db.session.get(ExtractionJob, job.job_id)
            if job and job.status == "COMPLETE":
                return redirect(job.redirect_url)
        return redirect(url_for("main.extraction_job_page", job_id=job.job_id))
    kind = request.args.get("kind", "NEW").upper()
    return redirect(url_for("main.insurance_entry", kind=kind, mode="BATCH"))

@bp.route("/review/<int:aid>/police", methods=["POST"])
def review_police(aid):
    """(Re-)upload a police report for an existing applicant: re-runs the universal
    address extraction + CITY matching and overwrites NO./STREET/CITY."""
    a = db.get_or_404(Applicant, aid)
    f = request.files.get("police")
    if not f or not f.filename:
        flash("Choose a police report file first.", "error"); return redirect(url_for("main.review", aid=a.id))
    up = Path(current_app.config["UPLOAD_DIR"]) / ("re-" + uuid.uuid4().hex[:10])
    up.mkdir(parents=True, exist_ok=True)
    dest = up / secure_filename(f.filename); f.save(dest)
    got = process_documents(police_path=dest, city_matcher=matcher(), ocr_cfg=cfg().get("ocr", {}))
    a.address_raw, a.addr_no, a.addr_street, a.city = (got.get("address_raw", ""),
        got.get("addr_no", ""), got.get("addr_street", ""), got.get("city", ""))
    # Re-uploaded police documents are authoritative: a missing/unclear title
    # clears both fields rather than retaining earlier passport-derived guesses.
    a.salutation, a.gender = got.get("salutation", ""), got.get("gender", "")
    if not a.passport_no and got.get("passport_no"): a.passport_no = got["passport_no"].upper()
    conf = json.loads(a.confidence_json or "{}"); conf.update(got.get("confidence", {}))
    a.confidence_json = json.dumps(conf)
    if a.status in ("APPROVED", "EXCEL_GENERATED"):
        a.status = "NEEDS_REVIEW"
        flash("Address changed - applicant sent back to NEEDS_REVIEW. Re-approve + regenerate the batch Excel.", "warning")
    db.session.commit()
    flash(f"Police report re-extracted: NO. {a.addr_no} | STREET {a.addr_street} | CITY {a.city or 'NOT MATCHED - pick from candidates'}", "success")
    return redirect(url_for("main.review", aid=a.id))

@bp.route("/review/<int:aid>/readdress", methods=["POST"])
def review_readdress(aid):
    """Paste/type the full address -> auto-split NO./STREET and auto-put CITY."""
    from .address_parser import parse_address
    a = db.get_or_404(Applicant, aid)
    raw = (request.form.get("address_raw") or "").strip().upper()
    parsed = parse_address(raw, matcher())
    a.address_raw, a.addr_no, a.addr_street, a.city = raw, parsed["no"], parsed["street"], parsed["city"]
    conf = json.loads(a.confidence_json or "{}")
    conf.update({"city": parsed["city_score"] / 100, "city_candidates": parsed["city_candidates"][:5],
                 "city_method": parsed.get("city_method", "")})
    a.confidence_json = json.dumps(conf); db.session.commit()
    flash(f"Address re-split: NO. {a.addr_no} | STREET {a.addr_street} | CITY {a.city or 'NOT MATCHED - pick from candidates'}", "success")
    return redirect(url_for("main.review", aid=a.id))

@bp.route("/api/integrations/embassy-appointment", methods=["POST"])
def import_main_system_appointment():
    """Receive an interview date from the recruitment system and create an EXTEND draft.

    Requires Authorization: Bearer <MAIN_SYSTEM_API_KEY>. JSON: passport_no,
    appointment_date (ISO YYYY-MM-DD; date-only is enough). Safe to retry: the
    same applicant + interview date returns the existing draft instead of duplicating it.
    """
    import hmac
    expected = current_app.config.get("MAIN_SYSTEM_API_KEY", "")
    auth = request.headers.get("Authorization", "")
    provided = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not expected:
        return jsonify({"error": "Main-system integration is not enabled; set MAIN_SYSTEM_API_KEY in the local .env."}), 503
    if not provided or not hmac.compare_digest(provided, expected):
        return jsonify({"error": "Unauthorized integration request."}), 401
    data = request.get_json(silent=True) or {}
    passport = (data.get("passport_no") or "").strip().upper()
    appt = to_date(data.get("appointment_date") or "")
    if not passport or not appt:
        return jsonify({"error": "passport_no and a valid appointment_date are required."}), 400
    src = Applicant.query.filter_by(passport_no=passport, kind="NEW").order_by(Applicant.id.desc()).first()
    if not src:
        src = Applicant.query.filter_by(passport_no=passport).order_by(Applicant.id.desc()).first()
    if not src:
        return jsonify({"error": f"No insurance applicant found for passport {passport}."}), 404
    entry_mode = (data.get("entry_mode") or data.get("mode") or getattr(src, "entry_mode", "SINGLE")).upper()
    if entry_mode not in ("SINGLE", "BATCH"): entry_mode = "SINGLE"
    existing = Applicant.query.filter_by(passport_no=passport, company=src.company, kind="EXTEND",
                                         entry_mode=entry_mode, embassy_appointment_date=appt).order_by(Applicant.id.desc()).first()
    if existing:
        return jsonify({"ok": True, "already_exists": True, "extend_draft_id": existing.id,
                        "departure_date": existing.departure_date.isoformat() if existing.departure_date else None,
                        "arrival_date": existing.arrival_date.isoformat() if existing.arrival_date else None}), 200
    from .inbox_service import make_extend_draft
    draft = make_extend_draft(src, appt, entry_mode=entry_mode)
    return jsonify({"ok": True, "already_exists": False, "extend_draft_id": draft.id,
                    "passport_no": passport, "appointment_date": appt.isoformat(),
                    "departure_date": draft.departure_date.isoformat(),
                    "arrival_date": draft.arrival_date.isoformat(), "status": draft.status,
                    "entry_mode": draft.entry_mode}), 201

# ---------- JSON API (also used by MCP / external agents) ----------
@bp.route("/api/cities")
def api_cities(): return jsonify(matcher().search(request.args.get("q", ""), 20))

@bp.route("/api/applicants")
def api_applicants(): return jsonify([a.as_dict() for a in Applicant.query.order_by(Applicant.id.desc()).limit(200)])
