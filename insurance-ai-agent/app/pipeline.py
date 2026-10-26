"""End-to-end applicant pipeline: documents -> fields -> dates -> review-ready record.
Shared by the Flask UI, the MCP server and CLI scripts (single implementation).
"""
from __future__ import annotations
import json
import re
from difflib import SequenceMatcher
from datetime import date
from pathlib import Path

from .address_parser import parse_address
from .dates_engine import new_dates, extend_dates, parse_any
from .phone import normalize_phone
from .ocr.engine import extract_text, render_pages, ocr_mrz_region
from .ocr.extractors import (extract_passport, extract_police_report,
                             extract_application, extract_policy_document)

def _vlm_fill(kind: str, path: Path, fields: dict, cfg: dict) -> dict:
    """If classic OCR was weak, ask the local VLM and fill blanks only."""
    if not cfg.get("use_ollama_vlm"): return fields
    try:
        from .ocr.vlm_ollama import vlm_extract
        pages = render_pages(path)
        got = vlm_extract(pages[0], kind, cfg.get("ollama_host"), cfg.get("ollama_model")) if pages else None
        if not got: return fields
        for k, v in got.items():
            if v and not fields.get(k): fields[k] = v
        if kind == "passport" and got.get("sex") and not fields.get("gender"):
            fields["gender"] = "Male" if str(got["sex"]).upper().startswith("M") else "Female"
    except Exception:
        pass
    return fields

def _score_police(text: str, conf: float, city_matcher):
    f = extract_police_report(text, conf)
    parsed = parse_address(f.get("address_raw", ""), city_matcher)
    key = (1 if parsed["city"] else 0, parsed["city_score"],
           1 if f.get("salutation") else 0,
           1 if "LANKA" in (f.get("address_raw") or "").upper() else 0,
           len((f.get("address_raw") or "").split()), round(conf, 3))
    return key, f, parsed

def _best_police_read(path: Path, city_matcher):
    """First OCR pass; if it gives no dropdown CITY, retry every engine x
    {raw, OpenCV-processed} variant and keep the best CITY-scoring read.
    This is what makes the police->city step reliable on ANY new report."""
    r = extract_text(path, force_ocr=True)
    best_key, f, parsed = _score_police(r["text"], r["confidence"], city_matcher)
    best = (f, parsed, r)
    if parsed["city"] and parsed["city_score"] >= 86:
        return best
    try:
        from .ocr.engine import ENGINES, preprocess as _pre
        pages = render_pages(path)
        groups: dict[str, list] = {}
        for name, fn in ENGINES:
            for variant in ("raw", "processed"):
                texts, confs = [], []
                for png in pages:
                    try:
                        t, c = fn(png if variant == "raw" else _pre(png))
                    except Exception:
                        texts = None; break
                    texts.append(t); confs.append(c)
                if texts:
                    groups[f"{name}/{variant}"] = ("\n".join(texts),
                                                    sum(confs) / len(confs) if confs else 0.0)
        for eng, (text, conf) in groups.items():
            key, f2, p2 = _score_police(text, conf, city_matcher)
            if key > best_key:
                best_key, best = key, (f2, p2, {"text": text, "confidence": conf, "engine": eng})
                if p2["city"] and p2["city_score"] >= 100: break
    except Exception:
        pass  # first-pass result stands; Review UI will flag the city
    return best

def _corroborate_application_address(out: dict, app_fields: dict, city_matcher) -> None:
    """Use a matching applicant form to validate/fill OCR-damaged police address.

    Police remains the primary address source. The application's address is only
    eligible when its passport number exactly matches the passport extracted in
    this same bundle; street corrections additionally require matching city and
    house number, then strong textual overlap.
    """
    passport_no = re.sub(r"\s+", "", str(out.get("passport_no", "")).upper())
    application_no = re.sub(r"\s+", "", str(app_fields.get("passport_no", "")).upper())
    raw = str(app_fields.get("address_raw", "")).strip()
    if not passport_no or passport_no != application_no or not raw:
        return
    app = parse_address(raw, city_matcher)
    if not app.get("city") or app.get("city_score", 0) < 86:
        return

    police_city = str(out.get("city", "")).strip()
    police_address_complete = bool(out.get("addr_no") and out.get("addr_street"))
    city_agrees = bool(police_city and police_city.casefold() == app["city"].casefold())
    if not police_city:
        out["city"] = app["city"]
        out["city_score"] = app["city_score"]
        out["city_candidates"] = app.get("city_candidates", [])
        out["city_method"] = "matching-application-fallback"
        out["confidence"]["city"] = min(app["city_score"] / 100, 0.9)
        city_agrees = True

    police_no = str(out.get("addr_no", "")).strip().upper()
    app_no = str(app.get("no", "")).strip().upper()
    if not police_no and app_no and city_agrees:
        out["addr_no"] = app_no
        out["confidence"]["addr_no"] = 0.85
        police_no = app_no
    house_agrees = bool(police_no and app_no and police_no == app_no)
    if not out.get("addr_street") and app.get("street") and city_agrees and house_agrees:
        out["addr_street"] = app["street"]
        out["confidence"]["addr_street"] = 0.85
    elif out.get("addr_street") and app.get("street") and city_agrees and house_agrees:
        norm = lambda s: re.sub(r"[^A-Z0-9]", "", str(s).upper())
        current, alternative = norm(out["addr_street"]), norm(app["street"])
        if current and alternative and SequenceMatcher(None, current, alternative).ratio() >= 0.72:
            # Same ID, house number, and city; the printed application text is
            # clearer than the handwritten police address for small OCR errors.
            out["addr_street"] = app["street"]
            out["confidence"]["addr_street"] = 0.9
    if house_agrees and city_agrees:
        if police_address_complete:
            out["_address_source"] = "police report, corroborated by matching application"
        else:
            # The report yielded only a city/partial address; expose the full,
            # passport-matched application address as the reviewable raw source.
            out["address_raw"] = raw
            out["_address_source"] = "matching application fallback (police address OCR incomplete)"
    elif not out.get("address_raw"):
        out["address_raw"] = raw

def process_documents(passport_path=None, police_path=None, application_path=None,
                      city_matcher=None, ocr_cfg: dict | None = None,
                      policy_document_path=None) -> dict:
    ocr_cfg = ocr_cfg or {}
    out: dict = {"confidence": {}}
    if passport_path:
        r = extract_text(passport_path, force_ocr=True)
        if r.get("engine") == "none": out.setdefault("_ocr_warnings", []).append("Passport: no OCR engine could read this file")
        f = extract_passport(r["text"], r["confidence"])
        if any(not f.get(k) or f.get("_conf", {}).get(k, 0) < 0.82
               for k in ("surname", "given_names")):
            focused = ocr_mrz_region(passport_path)
            if focused.get("text"):
                f2 = extract_passport(focused["text"], focused.get("confidence", 0.0))
                for key in ("surname", "given_names", "passport_no", "birthday", "gender"):
                    value = f2.get(key)
                    if value and (not f.get(key) or f2.get("_conf", {}).get(key, 0) > f.get("_conf", {}).get(key, 0)):
                        f[key] = value
                        f.setdefault("_conf", {})[key] = f2.get("_conf", {}).get(key, 0.0)
                if f2.get("_mrz_checks"):
                    f["_mrz_checks"] = f2["_mrz_checks"]
                    f["_source"] = "MRZ-focused OCR"
        if r["confidence"] < 0.82: f = _vlm_fill("passport", Path(passport_path), f, ocr_cfg)
        out.update({k: v for k, v in f.items() if not k.startswith("_")})
        out["confidence"].update(f.get("_conf", {}))
        out["_passport_engine"] = r["engine"]; out["_mrz_checks"] = f.get("_mrz_checks")
    if police_path:
        # UNIVERSAL police-report step - runs for EVERY upload (single, bulk,
        # re-upload; NEW and EXTEND). If the first OCR pass doesn't yield a
        # dropdown CITY, all other engines/image-variants are tried automatically
        # and the read with the best CITY match wins. See _best_police_read().
        f, parsed, r = _best_police_read(Path(police_path), city_matcher)
        if r.get("engine") == "none": out.setdefault("_ocr_warnings", []).append("Police report: no OCR engine could read this file")
        if r["confidence"] < 0.82 or not f.get("address_raw") or not parsed.get("city"):
            f2 = _vlm_fill("police", Path(police_path), dict(f), ocr_cfg)
            if f2.get("address"):
                alt = parse_address(str(f2["address"]).upper(), city_matcher)
                if alt["city_score"] > parsed["city_score"]:
                    f["address_raw"], parsed = str(f2["address"]).upper(), alt
        # Police-report salutation is the source of truth for these insurer fields.
        # Blank/unreadable titles clear any passport-derived gender/salutation; do not
        # guess either value from the applicant's name.
        out["salutation"] = f.get("salutation", "")
        out["gender"] = f.get("gender", "")
        out["address_raw"] = f.get("address_raw", "")
        out["police_name"] = f.get("full_name", "")
        out["nic"] = f.get("nic", "")
        if not out.get("passport_no") and f.get("passport_no"):
            out["passport_no"] = f["passport_no"]; out["confidence"]["passport_no"] = 0.7
        out.update({"addr_no": parsed["no"], "addr_street": parsed["street"],
                    "city": parsed["city"], "city_candidates": parsed["city_candidates"],
                    "city_score": parsed["city_score"], "city_method": parsed.get("city_method", "")})
        out["confidence"]["city"] = parsed["city_score"] / 100
        out["confidence"]["city_candidates"] = parsed["city_candidates"][:5]  # shown in Review UI
        out["confidence"]["city_method"] = parsed.get("city_method", "")
        out["confidence"]["address_raw"] = f.get("_conf", {}).get("address_raw", r["confidence"])
        out["_police_engine"] = r["engine"]
    if application_path:
        r = extract_text(application_path, force_ocr=True)
        if r.get("engine") == "none": out.setdefault("_ocr_warnings", []).append("Application: no OCR engine could read this file")
        f = extract_application(r["text"])
        if not f.get("telephone"):
            f2 = _vlm_fill("application", Path(application_path), dict(f), ocr_cfg)
            f["telephone"] = f2.get("telephone", "")
        out["telephone"] = normalize_phone(f.get("telephone", ""))
        out["confidence"]["telephone"] = 0.8 if out["telephone"] else 0.0
        out["_application_passport_no"] = f.get("passport_no", "")
        out["_application_telephones_all"] = f.get("telephones_all", [])
        _corroborate_application_address(out, f, city_matcher)
    if policy_document_path:
        r = extract_text(policy_document_path, force_ocr=True)
        if r.get("engine") == "none": out.setdefault("_ocr_warnings", []).append("Policy document: no OCR engine could read this file")
        f = extract_policy_document(r["text"], r.get("confidence", 0.0))
        out["existing_policy_no"] = f.get("policy_no", "")
        out["_policy_passport_no"] = f.get("passport_no", "")
        out["confidence"]["existing_policy_no"] = f.get("_conf", {}).get("policy_no", 0.0)
        out["_policy_engine"] = r.get("engine", "none")
    if not police_path:
        # Gender/salutation now come only from a readable police-report title.
        out["salutation"] = ""
        out["gender"] = ""
    return out

def apply_date_rules(kind: str, issue: date | None = None,
                     appointment: date | None = None) -> dict:
    if kind == "EXTEND":
        if appointment is None:
            raise ValueError("EXTEND needs the embassy appointment date.")
        return extend_dates(appointment, issue)
    return new_dates(issue or date.today())

def needs_review(conf: dict, threshold: float = 0.82) -> list[str]:
    return sorted(k for k, v in (conf or {}).items()
                  if isinstance(v, (int, float)) and v < threshold)

def to_date(v):
    if not v: return None
    if isinstance(v, date): return v
    return parse_any(str(v)) or (date.fromisoformat(str(v)[:10]) if str(v)[:4].isdigit() else None)
