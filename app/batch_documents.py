"""Local, best-effort classification for a mixed batch of source documents.

The classifier only reads OCR text and filenames. It never uploads documents to
cloud OCR. Low-confidence assignments are exposed on the extraction preview so
staff can verify the resulting applicant rows before approval.
"""
from __future__ import annotations

import re
from pathlib import Path

from .ocr.engine import extract_text
from .ocr.extractors import (extract_application, extract_passport,
                             extract_police_report, extract_policy_document)

CATEGORIES = {
    "passport_files": "Passport photo pages",
    "police_files": "Police reports",
    "application_files": "Applicant / phone documents",
    "policy_files": "Existing policy documents",
}


def classify_document_text(text: str, filename: str = "", kind: str = "NEW") -> dict:
    """Return a best-effort category using OCR and filename evidence."""
    text = text or ""
    upper = text.upper()
    fname = Path(filename or "").stem.upper().replace("_", " ").replace("-", " ")
    scores = {key: 0 for key in CATEGORIES}
    reasons = {key: [] for key in CATEGORIES}

    def add(category, points, reason):
        scores[category] += points
        reasons[category].append(reason)

    # Strong passport signals: ICAO MRZ lines, or labelled passport data and
    # personal fields. A passport number alone is not enough to classify a file.
    lines = [re.sub(r"\s+", "", line.upper()) for line in upper.splitlines()]
    has_mrz_header = any(line.startswith("P<") and len(line) >= 25 for line in lines)
    has_mrz_data = any(re.fullmatch(r"[A-Z0-9<]{25,}", line) for line in lines)
    passport = extract_passport(text, 0.85)
    if has_mrz_header and has_mrz_data:
        add("passport_files", 12, "ICAO passport MRZ pattern")
    if "PASSPORT" in upper and passport.get("passport_no"):
        add("passport_files", 4, "passport label and document number")
    if passport.get("surname") and passport.get("given_names") and passport.get("birthday"):
        add("passport_files", 3, "passport-like name and birth-date fields")

    # Police report signals prioritize the police boilerplate plus an address.
    police = extract_police_report(text, 0.85)
    for marker, points in (("POLICE CLEARANCE", 7), ("POLICE STATION", 5),
                           ("HAS NOT COME", 4), ("RESIDING AT", 4),
                           ("CERTIFY THAT", 2)):
        if marker in upper:
            add("police_files", points, f"{marker.lower()} text")
    if police.get("address_raw"):
        add("police_files", 2, "address extracted in police-report layout")

    # Applicant forms commonly include a mobile/telephone label and/or an
    # insurance/application heading; the extracted phone is supporting evidence.
    application = extract_application(text)
    app_markers = ("APPLICATION FORM", "INSURANCE APPLICATION", "PERSONAL DETAILS",
                   "MOBILE", "TELEPHONE", "CONTACT NO", "PHONE NUMBER", "WHATSAPP")
    for marker in app_markers:
        if marker in upper:
            add("application_files", 2 if "APPLICATION" in marker or "DETAILS" in marker else 1,
                f"{marker.lower()} text")
    if application.get("telephone"):
        add("application_files", 3, "Sri Lankan mobile number extracted")
    if application.get("passport_no") and application.get("telephone"):
        add("application_files", 2, "passport key and phone number extracted")

    # Existing policies are used for EXTEND jobs; their numbers are not treated
    # as applicant passport numbers.
    policy = extract_policy_document(text, 0.85)
    policy_markers = ("E-TRAVEL", "POLICY NO", "POLICY NUMBER", "CERTIFICATE NO", "INSURANCE POLICY")
    for marker in policy_markers:
        if marker in upper:
            add("policy_files", 2, f"{marker.lower()} text")
    if policy.get("policy_no"):
        add("policy_files", 7, "labelled existing policy number extracted")
    if policy.get("passport_no"):
        add("policy_files", 2, "passport key found in policy document")

    # Filename hints help when a scan has little readable text, but do not
    # override stronger document-content evidence.
    filename_hints = {
        "passport_files": (r"\bPASSPORT\b|\bPP\b", 5),
        "police_files": (r"\bPOLICE\b|\bCLEARANCE\b", 5),
        "application_files": (r"\bAPPLICATION\b|\bPHONE\b|\bMOBILE\b|\bCONTACT\b", 4),
        "policy_files": (r"\bPOLICY\b|\bCERTIFICATE\b|\bE TRAVEL\b", 5),
    }
    for category, (pattern, points) in filename_hints.items():
        if re.search(pattern, fname):
            add(category, points, "filename hint")

    # Deterministic tie-breaking favors the distinctive page types before the
    # general applicant form. Scores remain visible for human review.
    order = ["policy_files", "passport_files", "police_files", "application_files"] if kind.upper() == "EXTEND" \
        else ["passport_files", "police_files", "application_files", "policy_files"]
    ranked = sorted(order, key=lambda category: (scores[category], -order.index(category)), reverse=True)
    best = ranked[0]
    best_score = scores[best]
    second_score = scores[ranked[1]]
    if best_score == 0:
        best = "application_files"  # best-effort fallback; visibly marked low confidence
        confidence = 0.05
        reason = "No reliable OCR or filename cues; best-effort fallback, verify manually"
    else:
        margin = best_score - second_score
        confidence = min(0.98, max(0.15, 0.45 + 0.05 * best_score + 0.035 * margin))
        reason = "; ".join(reasons[best]) or "Best available category score"
    return {"category": best, "confidence": round(confidence, 2),
            "score": best_score, "reason": reason,
            "low_confidence": confidence < 0.65}


def classify_combined_files(paths, kind: str = "NEW") -> dict:
    """OCR each combined upload locally and group it for existing bulk extractors."""
    grouped = {category: [] for category in CATEGORIES}
    for path in paths:
        path = Path(path)
        try:
            result = extract_text(path, force_ocr=True)
            text = result.get("text", "")
            ocr_engine = result.get("engine", "none")
        except Exception as exc:
            text, ocr_engine = "", "none"
            ocr_error = type(exc).__name__
        else:
            ocr_error = ""
        classified = classify_document_text(text, path.name, kind)
        entry = {"name": path.name, "path": str(path), "confidence": classified["confidence"],
                 "score": classified["score"], "reason": classified["reason"],
                 "low_confidence": classified["low_confidence"], "ocr_engine": ocr_engine}
        if ocr_error:
            entry["reason"] += f"; OCR error: {ocr_error}"
        grouped[classified["category"]].append(entry)
    return grouped
