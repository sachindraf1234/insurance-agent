"""CITY dropdown matcher.

Rule from user: take the LAST city-like part of the police-report address
(the part circled in red, just before 'SRI LANKA') and choose that city from
the dropdown list (data/cities.txt, 16,490 entries supplied by user).

Strategy (never silently guess):
 1) exact match on the longest suffix (try last 4,3,2,1 words)
 2) RapidFuzz fuzzy match >= threshold (difflib fallback if RapidFuzz absent)
 3) below threshold -> return best candidates + low score -> UI forces human pick
Returned city keeps the EXACT spelling/case from cities.txt (dropdown-safe).
"""
from __future__ import annotations
import re
from pathlib import Path

def _norm(s: str) -> str:
    s = (s or "").upper()
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

class CityMatcher:
    def __init__(self, cities_file: str | Path):
        raw = Path(cities_file).read_text(encoding="utf-8", errors="ignore").splitlines()
        seen, self.cities = set(), []
        for line in raw:
            c = line.strip()
            if not c: continue
            k = _norm(c)
            if k in seen: continue
            seen.add(k); self.cities.append(c)
        self._exact = {_norm(c): c for c in self.cities}
        self._normed = list(self._exact.keys())

    def best_for_address(self, address: str, threshold: int = 86) -> dict:
        """Universal entry point for ANY police-report address. Tries, in order:
        exact/fuzzy suffix of the whole address -> exact city just before a
        province/postal tail -> exact/fuzzy on each comma-segment from the end.
        Always returns candidates so the Review UI can offer one-click picks."""
        best = self.match_suffix(address, threshold)
        if best.get("city"):
            return best
        # city followed by tail words, e.g. '..., MARAWILA, WESTERN PROVINCE' / 'DANKOTUWA 61130'
        words = [w for w in _norm(address).split() if w not in {"SRI", "LANKA"}]
        while words and words[-1].isdigit(): words.pop()
        for end in range(len(words) - 1, max(0, len(words) - 7), -1):
            for n in (3, 2, 1):
                if end - n + 1 < 0: continue
                cand = " ".join(words[end - n + 1: end + 1])
                if cand in self._exact:
                    return {"city": self._exact[cand], "score": 97, "method": "exact-before-tail",
                            "matched_on": cand, "candidates": [self._exact[cand]] + best.get("candidates", [])}
        for seg in reversed([s for s in (address or "").split(",") if s.strip()]):
            m = self.match_suffix(seg, threshold)
            if m.get("city"):
                m["method"] = str(m.get("method", "")) + "+segment"
                return m
            if m.get("score", 0) > best.get("score", 0):
                best = m
        return best

    def match_suffix(self, address_tail: str, threshold: int = 86) -> dict:
        """address_tail = full address (or its last segment). Tries longest suffix first."""
        words = _norm(address_tail).split()
        words = [w for w in words if w not in {"SRI", "LANKA"}]
        while words and words[-1].isdigit(): words.pop()  # trailing postal code
        if not words:
            return {"city": "", "score": 0, "method": "none", "candidates": []}
        for n in (4, 3, 2, 1):
            if len(words) < n: continue
            cand = " ".join(words[-n:])
            if cand in self._exact:
                return {"city": self._exact[cand], "score": 100, "method": f"exact-{n}w",
                        "matched_on": cand, "candidates": [self._exact[cand]]}
        # fuzzy on last-2 and last-1 word forms
        queries = [" ".join(words[-2:])] if len(words) >= 2 else []
        queries.append(words[-1])
        best = {"city": "", "score": 0, "method": "fuzzy", "candidates": []}
        for q in queries:
            hits = self._fuzzy(q, 5)
            if hits and hits[0][1] > best["score"]:
                best = {"city": hits[0][0] if hits[0][1] >= threshold else "",
                        "score": int(hits[0][1]), "method": "fuzzy", "matched_on": q,
                        "candidates": [h[0] for h in hits]}
        return best

    def _fuzzy(self, query_norm: str, limit: int):
        try:
            from RapidFuzz import process, fuzz
            res = process.extract(query_norm, self._normed, scorer=fuzz.WRatio, limit=limit)
            return [(self._exact[n], float(s)) for n, s, _ in res]
        except Exception:
            import difflib
            close = difflib.get_close_matches(query_norm, self._normed, n=limit, cutoff=0.6)
            return [(self._exact[n], difflib.SequenceMatcher(None, query_norm, n).ratio() * 100)
                    for n in close]

    def search(self, text: str, limit: int = 20):
        """For the review-UI city picker (type-ahead)."""
        q = _norm(text)
        starts = [self._exact[n] for n in self._normed if n.startswith(q)]
        if len(starts) >= limit: return starts[:limit]
        fuzzy = [c for c, _ in self._fuzzy(q, limit)]
        return list(dict.fromkeys(starts + fuzzy))[:limit]
