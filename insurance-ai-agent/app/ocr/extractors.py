"""Field extraction from OCR text: Passport | Police Report | Application form.

Insurer column convention (from samples — IMPORTANT):
  FIRST NAME column  <- passport SURNAME
  LAST NAME  column  <- passport GIVEN NAMES
"""
from __future__ import annotations
import re
from difflib import SequenceMatcher
from ..dates_engine import parse_any
from ..phone import find_phones, normalize_phone
from .mrz import parse_mrz_text

_PASSPORT_NO = re.compile(r"\b[NP]\d{7,8}\b|\b[A-Z]\d{7,8}\b")


def _get(pattern, text, group=1, flags=re.I):
    m = re.search(pattern, text or "", flags)
    return m.group(group).strip() if m else ""


def _clean_name(value: str) -> str:
    value = re.sub(r"^(?:MR|MRS|MS|MISS|DR)\.?\s+", "", value, flags=re.I)
    value = re.sub(r"[^A-Z'&\- .]", " ", value.upper())
    return re.sub(r"\s+", " ", value).strip(" .-'")


def _visual_passport_name(text: str, field: str) -> str:
    """Read visible name labels, tolerating nearby OCR noise and split lines."""
    if field == "surname":
        label_re = re.compile(r"\b(?:sur\s*name|family\s*name)\b", re.I)
    else:
        # OCR on the provided passport read “Other Naes”; allow small omissions.
        label_re = re.compile(r"\b(?:other\s+na(?:mes?|es|me)?|given\s+names?|first\s+names?)\b", re.I)
    stop_labels = re.compile(
        r"\s+(?:other\s+names?|given\s+names?|first\s+names?|surname|family\s+name|"
        r"passport\s*(?:no|number)|date\s*of\s*birth|nationality|sex)\s*[:\-]", re.I
    )
    lines = [re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines()]
    for i, line in enumerate(lines):
        match = label_re.search(line)
        if not match:
            continue
        inline = line[match.end():].strip(" :/-")
        candidates = [inline] if inline else []
        candidates.extend(lines[i + 1:i + 4])
        for candidate in candidates:
            candidate = stop_labels.split(candidate, maxsplit=1)[0]
            candidate = _clean_name(candidate)
            alpha_words = [w for w in candidate.split() if sum(ch.isalpha() for ch in w) >= 2]
            if len(alpha_words) >= 1 and sum(ch.isalpha() for ch in candidate) >= 3:
                # Reject OCR labels/headings accidentally captured as values.
                normalized = re.sub(r"[^A-Z]", "", candidate)
                if normalized not in {"GIVENNAMES", "OTHERNAMES", "SURNAME", "NATIONALITY", "DATEOFBIRTH"}:
                    return candidate
    return ""


def extract_passport(text: str, ocr_conf: float = 0.0) -> dict:
    f = {"surname": "", "given_names": "", "passport_no": "", "birthday": "",
         "gender": "", "_conf": {}, "_source": "visual"}
    mrz = parse_mrz_text(text)
    if mrz and (mrz["passport_no"] or mrz["surname"]):
        f.update({"surname": mrz["surname"], "given_names": mrz["given_names"],
                  "passport_no": mrz["passport_no"], "birthday": mrz["birthday"],
                  "gender": mrz["gender"], "_source": "MRZ", "_mrz_checks": mrz["checks"]})
        f["_conf"] = {
            "surname": 0.99 if mrz["mrz_valid"] else 0.75,
            "given_names": 0.99 if mrz["mrz_valid"] else 0.75,
            "passport_no": 0.99 if mrz["checks"]["passport_no"] else 0.5,
            "birthday": 0.99 if mrz["checks"]["dob"] else 0.5,
            "gender": 0.95 if mrz["gender"] else 0.0,
        }
    t = text or ""
    visual_surname = _visual_passport_name(t, "surname")
    visual_given = _visual_passport_name(t, "given_names")
    if mrz:
        for field, visual in (("surname", visual_surname), ("given_names", visual_given)):
            if visual and f.get(field):
                norm = lambda x: re.sub(r"[^A-Z]", "", x.upper())
                similarity = SequenceMatcher(None, norm(visual), norm(f[field])).ratio()
                if similarity < 0.97:
                    # MRZ name fields have no check digits. When the passport's
                    # explicit printed label gives a close alternative, use that
                    # human-readable spelling (e.g. OCR 0/O or CHAHINDA/CHAMINDA)
                    # and keep it in Review. A distant disagreement remains a
                    # low-confidence MRZ value rather than silently switching IDs.
                    if similarity >= 0.80 and ocr_conf >= 0.82:
                        f[field] = visual
                        f["_conf"][field] = min(0.94, max(0.82, ocr_conf))
                    else:
                        f["_conf"][field] = min(f["_conf"].get(field, 0.99), 0.65)
    for field, visual in (("surname", visual_surname), ("given_names", visual_given)):
        if visual and not f[field]:
            f[field] = visual
            f["_conf"][field] = min(0.90, max(0.55, ocr_conf))
        elif visual and f["_conf"].get(field, 0) < 0.5:
            f[field] = visual
            f["_conf"][field] = min(0.90, max(0.55, ocr_conf))
    if not f["passport_no"]:
        m = _PASSPORT_NO.search(t.upper())
        f["passport_no"] = m.group(0) if m else ""
        f["_conf"]["passport_no"] = max(ocr_conf - 0.1, 0.4) if m else 0.0
    if not f["birthday"]:
        raw = _get(r"(?:Date of [Bb]irth|DOB)[:\s]*([\d]{1,2}[./\- ][\d]{1,2}[./\- ]\d{2,4})", t)
        d = parse_any(raw)
        f["birthday"] = d.isoformat() if d else ""
        f["_conf"]["birthday"] = max(ocr_conf - 0.1, 0.4) if d else 0.0
    if not f["gender"]:
        sx = _get(r"\bSex[:\s]*([MF])\b", t)
        f["gender"] = "Male" if sx.upper() == "M" else ("Female" if sx.upper() == "F" else "")
        f["_conf"]["gender"] = 0.8 if f["gender"] else 0.0
    for k in ("surname", "given_names"):
        f.setdefault("_conf", {}).setdefault(k, 0.7 if f[k] else 0.0)
        f[k] = re.sub(r"\s+", " ", f[k]).upper().strip()
    return f
def _police_salutation_and_gender(text: str) -> tuple[str, str]:
    """Read the applicant's honorific immediately after the police certificate wording.

    Gender is derived only from unambiguous honorifics. Missing or ambiguous
    titles leave gender blank; names are never used to guess it.
    """
    # OCR sometimes glues the word to preceding text; don't require a word boundary.
    anchor = re.search(r"certif\w*", text or "", re.I)
    if not anchor:
        return "", ""
    subject = (text or "")[anchor.end():anchor.end() + 420]
    subject = re.split(r"\b(?:passport\s+(?:number|no)|national identity|NIC\b|address|has not)\b",
                       subject, maxsplit=1, flags=re.I)[0]
    titles = [
        ("His Eminence", r"\bHis\s+Eminence\b"),
        ("Rev. Father", r"\bRev\.?\s+Father\b"),
        ("Master", r"\bMaster\.?\b"),
        ("Mrs.", r"\bMrs\.?\b"),
        ("Miss", r"\bMiss\.?\b"),
        ("Ms.", r"\bMs\.?\b"),
        ("Mr.", r"\bMr\.?\b"),
        ("Capt.", r"\bCapt\.?\b"),
        ("Dr.", r"\bDr\.?\b"),
        ("M/S", r"\bM\s*/\s*S\b"),
        ("Pastor", r"\bPastor\b"),
        ("Prof.", r"\bProf\.?\b"),
        ("Rev.", r"\bRev\.?\b"),
        ("Ven.", r"\bVen\.?\b"),
    ]
    found = []
    for canonical, pattern in titles:
        match = re.search(pattern, subject, re.I)
        if match:
            found.append((match.start(), canonical))
    if not found:
        return "", ""
    salutation = min(found, key=lambda item: item[0])[1]
    male_titles = {"Mr.", "Master", "His Eminence", "Rev. Father", "Ven."}
    female_titles = {"Mrs.", "Miss", "Ms."}
    gender = "Male" if salutation in male_titles else ("Female" if salutation in female_titles else "")
    return salutation, gender


def extract_police_report(text: str, ocr_conf: float = 0.0) -> dict:
    t = re.sub(r"[ \t]+", " ", text or "")
    salutation, gender = _police_salutation_and_gender(t)
    # name: line after "certify that:" (OCR often glues the title: "Mr.WIJELATH ...")
    name = _get(r"certify that:?\s*\n?\s*(?:Mr\.?|Ms\.?|Mrs\.?|Miss\.?|Master\.?)?\s*([A-Z][A-Z .]{5,})", t)
    if not name:
        name = _get(r"\b(?:Mr|Ms|Mrs|Miss)\.?\s*([A-Z][A-Z .]{8,})\n", t)
    # address: everything from the "Address:" label up to "Has not come", newlines
    # flattened. Label regex tolerates OCR variants (Adress/Addres/Address -).
    # (The red-circled CITY is often OCR'd as its OWN line — a single-line regex loses it.)
    m = re.search(r"Addr\w*\s*[:\-]\s*(.+?)\bHas not come\b", t, flags=re.I | re.S)
    address = m.group(1) if m else _get(r"Addr\w*\s*[:\-]\s*(.+)", t)
    # Regional Sri Lankan reports commonly phrase the handwritten address as
    # “has been residing at … in my Police Station area from …”, with no
    # literal Address label. Stop before the station/residence boilerplate.
    residing = re.search(
        r"\bresiding\s+at\b\s*[,;:\-]?\s*(.+?)"
        r"(?=\b(?:in\s*my\s*police\s*station|area\s+from|has\s+not\s+been\s+involved)\b)",
        t, flags=re.I | re.S)
    if residing:
        address = residing.group(1)
    address = re.sub(r"(?m)^\s*[-–—]\s*", "", address)
    address = re.sub(r"\s*\n\s*[-–—]\s*", ", ", address)
    address = re.sub(r"\s*\n\s*", ", ", address).strip()  # line-break -> comma segment
    address = re.sub(r"(?:,\s*){2,}", ", ", address).strip(" ,")
    address = ", ".join(part.strip(" .") for part in address.split(",") if part.strip(" ."))
    # STRUCTURAL fallback for ANY layout where the label itself was misread: on a Sri
    # Lanka Police Clearance the address block sits between the Passport/NIC block and
    # the "Has not come" sentence — take those lines, minus ID numbers standing alone.
    _lines_all = [l.strip() for l in t.splitlines()]
    _end = next((i for i, l in enumerate(_lines_all) if "HAS NOT" in l.upper()), len(_lines_all))
    _id = next((i for i, l in enumerate(_lines_all[:_end])
                if re.search(r"passport|identity|nic\b", l, re.I)), -1)
    _start = _id if _id >= 0 else next(
        (i for i, l in enumerate(_lines_all[:_end]) if "CERTIFY" in l.upper()), -1)
    if 0 <= _start < _end:
        block = []
        for l in _lines_all[_start + 1:_end]:
            u = l.upper().strip(" .:")
            if not l or re.fullmatch(r"[A-Z]\d{7,8}|\d{9,12}[VX]?", u): continue
            if re.search(r"passport|identity|certify", l, re.I): continue
            if _id < 0 and name and name.split()[0] in u and len(block) == 0: continue  # name line
            block.append(re.sub(r"^Addr\w*\s*[:\-]\s*", "", l, flags=re.I))
        # short fragments BEFORE the main street line are the tail (circled CITY /
        # 'SRI LANKA') that OCR mis-ordered — move them to the end, Lanka last.
        main_i = next((i for i, x in enumerate(block)
                       if "," in x or re.match(r"^(NO\b|\d)", x.strip(), re.I)), 0)
        head_frags = [x for x in block[:main_i] if len(x.split()) <= 3]
        ordered = block[main_i:] + head_frags
        ordered.sort(key=lambda x: "LANKA" in x.upper() and len(x.split()) <= 3)
        structural = ", ".join(x for x in ordered if x.strip(" .,"))
        # use structural only when the label-based read is incomplete (no SRI LANKA tail)
        if structural and not residing and "LANKA" not in address.upper():
            if "LANKA" in structural.upper() or len(structural) > len(address):
                address = structural
    if "LANKA" not in address.upper():  # tail fragments (circled CITY, 'SRI LANKA.') may be
        lines = [l.strip() for l in t.splitlines()]          # orphan lines NEAR the Address line
        idx = next((i for i, l in enumerate(lines) if re.match(r"Addr\w*\s*[:\-]", l, re.I)), -1)
        if idx >= 0:
            skip = ("PASSPORT", "NATIONAL", "CERTIFY", "HAS NOT", "AMBASSAD", "HEADQUARTERS",
                    "FOR", "INSPECTOR", "ACCORDING", "PERIOD")
            end = next((i for i in range(idx, len(lines)) if "HAS NOT" in lines[i].upper()), idx + 4)
            frags = []
            for l in lines[max(0, idx - 3): idx] + lines[idx + 1: end]:
                u = l.upper().strip(" .:")
                if l and len(l.split()) <= 4 and not l.rstrip().endswith(":") \
                   and not any(k in u for k in skip) and sum(c.isalpha() for c in l) >= 3 and l not in address:
                    frags.append(l.strip(" ."))
            frags.sort(key=lambda x: "LANKA" in x.upper())  # 'SRI LANKA' always goes last
            if frags: address = address.rstrip(", ") + ", " + ", ".join(frags)
    passport_no = _get(r"Passport number:?\s*([A-Z]\d{7,8})", t)
    nic = _get(r"(?:National Identity card number|NIC):?\s*(\d{9,12}[VX]?)", t)
    return {"full_name": re.sub(r"\s+", " ", name).upper().strip(),
            "address_raw": address.upper().strip(),
            "passport_no": passport_no.upper(), "nic": nic,
            "salutation": salutation, "gender": gender,
            "_conf": {"address_raw": ocr_conf if address else 0.0,
                      "full_name": ocr_conf if name else 0.0,
                      "salutation": ocr_conf if salutation else 0.0,
                      "gender": ocr_conf if gender else 0.0}}

def _application_address(lines: list[str]) -> str:
    """Read the value immediately following an application's Address label."""
    for i, line in enumerate(lines):
        match = re.match(r"^\s*address\b\s*[:\-]?\s*(.*)$", line, re.I)
        if not match:
            continue
        value = match.group(1).strip(" :.-")
        if not value:
            for candidate in lines[i + 1:i + 4]:
                if not candidate:
                    continue
                if re.match(r"^(?:mobile|telephone|phone|tel\b|passport|date of birth)\b", candidate, re.I):
                    break
                value = candidate
                break
        # On tabular forms, OCR sometimes emits the cell value first and the
        # left-column label on the next line (e.g. “NO:111/4, ATHGALA, …” then
        # “Address”). Recover that immediately preceding address-like row.
        if not value:
            for candidate in reversed(lines[max(0, i - 3):i]):
                if (candidate and re.search(r"\d", candidate)
                        and not re.match(r"^(?:mobile|telephone|phone|tel\b|date of birth|passport)\b", candidate, re.I)):
                    value = candidate
                    break
        value = re.split(r"\b(?:mobile|telephone|phone|tel\s*\(|passport\s*(?:no|number))\b",
                         value, maxsplit=1, flags=re.I)[0]
        value = re.sub(r"\s+", " ", value).strip(" :.,;-)")
        if value and sum(ch.isalpha() for ch in value) >= 4:
            return value.upper()
    return ""


def extract_application(text: str) -> dict:
    """Extract phone/passport keys and labelled address from an application form.

    Labelled phone candidates outrank arbitrary digit strings (which can be
    account/NIC/date artifacts). If a document has several unlabelled mobile-
    shaped numbers, leave the value blank for the reviewer rather than guessing.
    The application address is retained as a corroborating/fallback source when
    the police report's handwritten address is not legible.
    """
    t = text or ""
    phones = find_phones(t)
    lines = [re.sub(r"\s+", " ", x).strip() for x in t.splitlines()]
    address_raw = _application_address(lines)
    labels = [(0, re.compile(r"\b(?:mobile|telephone|phone|whatsapp|cell)\b", re.I)),
              (1, re.compile(r"\bcontact\b", re.I))]
    labelled = []
    for i, line in enumerate(lines):
        priorities = [priority for priority, pattern in labels if pattern.search(line)]
        if not priorities:
            continue
        priority = min(priorities)
        # Prefer a number on the same line, then the immediately following line.
        for candidate_line in (line, lines[i + 1] if i + 1 < len(lines) else ""):
            found = find_phones(candidate_line)
            if found:
                labelled.extend((priority, number) for number in found)
                break
    labelled.sort(key=lambda item: item[0])
    labelled_numbers = [number for _, number in labelled]
    telephone = (labelled_numbers[0] if labelled_numbers else (phones[0] if len(phones) == 1 else ""))
    passport_no = ""
    for line in lines:
        if re.search(r"passport|travel\s*document", line, re.I):
            match = re.search(r"\b([A-Z]\d{7,8})\b", line.upper())
            if match:
                passport_no = match.group(1)
                break
    if not passport_no:
        passport_no = _get(r"\b([NP]\d{7,8})\b", t).upper()
    return {"telephone": normalize_phone(telephone), "telephones_all": phones,
            "telephone_candidates_labelled": list(dict.fromkeys(labelled_numbers)),
            "passport_no": passport_no, "address_raw": address_raw}


def extract_policy_document(text: str, ocr_conf: float = 0.0) -> dict:
    """Extract an existing insurer policy number from a certificate/schedule.

    Explicit E-TRAVEL labels are preferred (Allianz EXTEND); generic POLICY NO
    and CERTIFICATE NO labels cover other insurer documents. All matching is
    performed on OCR text and the result is still checked on the Review page.
    """
    t = re.sub(r"[ \t]+", " ", (text or "").upper())
    value = r"([A-Z0-9][A-Z0-9/-]{4,39})"
    patterns = (
        r"\bE[\s-]*TRAVEL\s+(?:INSURANCE\s+)?POLICY\s*(?:NO\.?|N[O0]\.?|NUMBER|#)\s*[:.\-]?\s*" + value,
        r"\b(?:INSURANCE\s+)?POLICY\s*(?:NO\.?|N[O0]\.?|NUMBER|#)\s*[:.\-]?\s*" + value,
        r"\bCERTIFICATE\s*(?:NO\.?|N[O0]\.?|NUMBER|#)\s*[:.\-]?\s*" + value,
    )
    policy_no = ""
    for pattern in patterns:
        m = re.search(pattern, t, flags=re.I | re.S)
        if m:
            policy_no = m.group(1).strip(".-/")
            break
    passport_no = _get(r"\bPASSPORT\s*(?:NO\.?|N[O0]\.?|NUMBER)?\s*[:#.]?\s*([A-Z][A-Z0-9]{6,11})\b", t)
    return {"policy_no": policy_no, "passport_no": passport_no.upper(),
            "_conf": {"policy_no": min(max(float(ocr_conf), 0.0), 0.99) if policy_no else 0.0}}
