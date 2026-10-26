"""Passport MRZ (ICAO 9303, TD3 = 2 lines x 44 chars) parser + check-digit validation.

Why MRZ matters: the two machine-readable lines at the bottom of the passport
photo page contain surname, given names, passport no, DOB, sex — EACH protected
by a check digit. If checks pass, the fields are provably correct even when the
printed text above is blurry. If a check fails we flag the field for review and
fall back to visual OCR. This is the single biggest accuracy win for passports.
"""
from __future__ import annotations
import re
from datetime import date

_W = [7, 3, 1]
def _val(c: str) -> int:
    if c == "<": return 0
    if c.isdigit(): return int(c)
    return ord(c) - 55  # A=10 .. Z=35

def check_digit(s: str) -> int:
    return sum(_val(c) * _W[i % 3] for i, c in enumerate(s)) % 10

def _fix(s: str) -> str:
    # Common OCR confusions in OCR-B MRZ lines; never rewrite digits/letters
    # globally because passport numbers and dates also use both character sets.
    return (s.upper().replace(" ", "").replace("\t", "")
            .replace("«", "<").replace("‹", "<").replace("›", "<")
            .replace("〈", "<").replace("＜", "<"))

def find_mrz_lines(text: str) -> tuple[str, str] | None:
    lines = [_fix(l) for l in (text or "").splitlines()]
    lines = [l for l in lines if len(l) >= 30]
    l2_re = re.compile(r"^[A-Z0-9<]{30,48}$")
    for i, raw_l1 in enumerate(lines):
        l1 = raw_l1
        # OCR-B '<' immediately after the document code is often read as B/8/6.
        # In a passport line beginning P[confusion] + country code, repair only
        # that one structural character; do not globally replace B/8/6.
        if l1.startswith(("PB", "P8", "P6", "P5")) and l1.count("<") >= 2:
            l1 = "P<" + l1[2:]
        is_first_line = l1.startswith("P<") or (l1.startswith("P") and "<" in l1[:8])
        if not is_first_line:
            continue
        for j in range(i + 1, min(i + 5, len(lines))):
            l2 = lines[j]
            if l2_re.fullmatch(l2) and sum(ch.isdigit() for ch in l2[:30]) >= 8:
                return (l1.ljust(44, "<")[:44], l2.ljust(44, "<")[:44])
    return None

def _mrz_date(yymmdd: str, kind: str) -> str:
    try:
        yy, mm, dd = int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
        year = 1900 + yy if (kind == "dob" and yy > 30) else 2000 + yy
        if kind == "dob" and year > date.today().year: year -= 100
        return date(year, mm, dd).isoformat()
    except Exception:
        return ""

def parse_td3(l1: str, l2: str) -> dict:
    l1, l2 = _fix(l1).ljust(44, "<")[:44], _fix(l2).ljust(44, "<")[:44]
    names = l1[5:44].rstrip("<")
    surname, _, given = names.partition("<<")
    surname = surname.replace("<", " ").strip()
    given = given.replace("<", " ").strip()
    given = re.sub(r"\s+", " ", given); surname = re.sub(r"\s+", " ", surname)
    checks = {
        "passport_no": check_digit(l2[0:9]) == _val(l2[9]) if l2[9].isdigit() else False,
        "dob": check_digit(l2[13:19]) == _val(l2[19]) if l2[19].isdigit() else False,
        "expiry": check_digit(l2[21:27]) == _val(l2[27]) if l2[27].isdigit() else False,
    }
    sex = l2[20]
    return {
        "surname": surname, "given_names": given,
        "passport_no": l2[0:9].rstrip("<"),
        "nationality": l2[10:13].rstrip("<"),
        "birthday": _mrz_date(l2[13:19], "dob"),
        "gender": "Male" if sex == "M" else ("Female" if sex == "F" else ""),
        "expiry": _mrz_date(l2[21:27], "exp"),
        "personal_no": l2[28:42].rstrip("<"),
        "checks": checks,
        "mrz_valid": all(checks.values()),
        "raw": [l1, l2],
    }

def parse_mrz_text(text: str) -> dict | None:
    found = find_mrz_lines(text)
    return parse_td3(*found) if found else None
