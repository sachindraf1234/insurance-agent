"""Split a Sri Lankan address into NO. / STREET / CITY.

The city is matched first. From the text before that city, the first explicit
NO-labelled number (or first plausible number if there is no NO label) is moved
to NO.; every other address detail is retained in STREET. This also handles a
house number appearing in the middle of an address rather than only at its start.
"""
from __future__ import annotations
import re

_HOUSE = re.compile(r"^(RP\s*)?[\d][\w/\-]*([A-Za-z])?$|^(RP\s*)?[\d]+[A-Za-z]?([/\-][\w]+)+$|^\d+,\s*[A-Za-z]/\d+$")


def _clean(s: str) -> str:
    s = (s or "").strip().strip(".")
    s = re.sub(r",?\s*(?:SRI|SR)\s*LANKA\.?\s*,?$", "", s.strip().rstrip(","), flags=re.I).strip().rstrip(",")
    return re.sub(r"\s+", " ", s)


def _fix_house(token: str) -> str:
    """Fix OCR digit confusions inside a house-number token only."""
    t = re.sub(r"\s+", "", (token or "").upper())
    if not any(c.isdigit() for c in t) or t.startswith("RP"):
        return t
    # Handwritten slash-numbers such as 122/1 may be read as 122/! or 122/I.
    t = t.replace("!", "1").replace("|", "1")
    t = re.sub(r"(?<=\d)S(?=\d)", "5", t)
    t = re.sub(r"(?<=\d)[IL](?=[\d/])", "1", t)
    t = re.sub(r"(?<=/)\s*[IL]$", "1", t)
    t = re.sub(r"(?<=\d)O(?=[\d/])", "0", t)
    t = re.sub(r"^O(?=\d)", "0", t)
    return t


def _extract_house(address: str) -> tuple[str, str]:
    """Return (house number, remaining street text), locating numbers anywhere."""
    text = address or ""
    # `NO`, `NO.`, `NO:` and `NO,` are labels, not street text.
    text = re.sub(r"^\s*NO\.?\s*[, :\-]?\s*", "", text, flags=re.I)
    # Prefer an explicit label wherever it occurs, including mid-address.
    number = r"((?:[0-9O])[0-9A-Z!|]*(?:\s*[/\-]\s*[0-9A-Z!|]+)?)"
    match = re.search(r"\bNO\.?\s*[:#\-]?\s*" + number, text, flags=re.I)
    if match:
        raw = match.group(1)
    else:
        # The address block has already been cut at CITY, so this selects a
        # house/building number from the remaining address, wherever it appears.
        match = re.search(r"(?<![A-Z0-9])" + number + r"(?![A-Z0-9])", text, flags=re.I)
        raw = match.group(1) if match else ""
    if match:
        text = text[:match.start()] + " " + text[match.end():]
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"(?:,\s*){2,}", ", ", text)
    text = re.sub(r"\s+", " ", text).strip(" ,.:;-.")
    return (_fix_house(raw) if raw else ""), text


def parse_address(raw: str, city_matcher=None) -> dict:
    s = _clean(raw)
    result = {"no": "", "street": "", "city": "", "city_score": 0,
              "city_method": "", "city_candidates": [], "full": s}
    if not s:
        return result

    # Remove only the leading house-number label before matching the city. A
    # labelled number elsewhere is handled later by _extract_house().
    body = re.sub(r"^\s*NO\.?\s*[, :\-]?\s*", "", s, flags=re.I)
    segments = [seg.strip() for seg in body.split(",") if seg.strip()]

    city_original_tail = ""
    if city_matcher is not None:
        m = city_matcher.best_for_address(body) if hasattr(city_matcher, "best_for_address") \
            else city_matcher.match_suffix(body)
        result["city"], result["city_score"] = m["city"], m["score"]
        result["city_candidates"] = m.get("candidates", [])
        result["city_method"] = m.get("method", "")
        city_original_tail = m.get("matched_on", "")

    # Keep the text before the selected city; remove the city and any province,
    # postal-code, or country tail after it. Normalize punctuation for OCR'd CITY.
    rest = segments[:]
    if city_original_tail and rest:
        city_words = city_original_tail.split()
        for i in range(len(rest) - 1, -1, -1):
            words = [re.sub(r"[^A-Z0-9]", "", w.upper()).translate(
                     str.maketrans({"0": "O", "1": "I"})) for w in rest[i].split()]
            hit = next((j for j in range(len(words) - len(city_words), -1, -1)
                        if words[j:j + len(city_words)] == city_words), None)
            if hit is not None:
                kept = rest[i].split()[:hit]
                rest = rest[:i] + ([" ".join(kept)] if kept else [])
                break
    elif result["city"] and rest and rest[-1].upper().strip(" .") == result["city"].upper():
        rest.pop()

    before_city = ", ".join(x for x in rest if x.strip(" .,"))
    result["no"], result["street"] = _extract_house(before_city)
    result["street"] = result["street"].strip().rstrip(",")
    return result
