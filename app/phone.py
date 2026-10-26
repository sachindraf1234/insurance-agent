"""Sri Lankan mobile-phone extraction with the domestic leading zero retained.

Numbers are stored in local 10-digit form (07XXXXXXXX). OCR candidates can be
printed locally, with separators, or in +94/0094 format; separators are removed,
OCR digit confusions are repaired inside the phone span, and international
country prefixes are converted to the local leading-zero form.
"""
from __future__ import annotations
import re

_CANDIDATE = re.compile(
    r"(?<![A-Z0-9])(?:\+?\s*94[\s()./-]*|00\s*94[\s()./-]*|0[\s()./-]*)?"
    r"7(?:[\s()./-]*[0-9OQIL|]){8}(?![A-Z0-9])", re.I
)
_OCR_DIGITS = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "|": "1"})


def normalize_phone(raw: str) -> str:
    """Return a 10-digit Sri Lankan mobile beginning with 0, or empty string."""
    if not raw:
        return ""
    text = str(raw).strip().upper().translate(_OCR_DIGITS)
    digits = re.sub(r"\D", "", text)
    if digits.startswith("0094"):
        digits = digits[4:]
    elif digits.startswith("94"):
        digits = digits[2:]
    # Keep an already-local number exactly as entered after separator cleanup.
    if len(digits) == 10 and digits.startswith("0") and digits[1] == "7":
        return digits
    # Convert either a nine-digit mobile or +94/0094 country-code form into
    # Sri Lankan domestic form without changing any of the subscriber digits.
    if len(digits) == 9 and digits.startswith("7"):
        return "0" + digits
    return ""


def find_phones(text: str) -> list[str]:
    """Extract distinct Sri Lankan mobile numbers in local 0-prefixed form."""
    if not text:
        return []
    hits = []
    for match in _CANDIDATE.finditer(str(text).upper()):
        number = normalize_phone(match.group(0))
        if number:
            hits.append(number)
    return list(dict.fromkeys(hits))
