"""Date rules — the heart of NEW vs EXTEND.

NEW:    ISSUE = application (batch) date. DEPARTURE = ISSUE. ARRIVAL = DEPARTURE + 6 months.
EXTEND: DEPARTURE = embassy appointment date + 14 days.   ARRIVAL = DEPARTURE + 6 months.
        ISSUE = the day you send the extend sheet.

Internal storage is always ISO date (YYYY-MM-DD). Formatting happens ONLY at Excel export:
  Allianz -> MM/DD/YYYY | LOLC (and others) -> DD/MM/YYYY  (separator configurable)
"""
from __future__ import annotations
import calendar
from datetime import date, timedelta

def add_months(d: date, months: int) -> date:
    """Same day after N months, clamped to month-end (31 Aug +6 -> 28/29 Feb)."""
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day)

def new_dates(issue: date) -> dict:
    dep = issue
    return {"issue_date": issue, "departure_date": dep, "arrival_date": add_months(dep, 6)}

def extend_dates(appointment: date, issue: date | None = None) -> dict:
    dep = appointment + timedelta(days=14)   # two weeks after embassy appointment
    return {"issue_date": issue or date.today(),
            "embassy_appointment_date": appointment,
            "departure_date": dep, "arrival_date": add_months(dep, 6)}

def fmt(d: date | None, order: str = "DMY", sep: str = "/") -> str:
    if d is None: return ""
    if isinstance(d, str):  # allow ISO strings
        d = date.fromisoformat(d[:10])
    if order == "MDY": return f"{d.month:02d}{sep}{d.day:02d}{sep}{d.year}"
    return f"{d.day:02d}{sep}{d.month:02d}{sep}{d.year}"

def parse_any(s: str) -> date | None:
    """Parse dates found in documents/emails. Ambiguous slash dates -> DD/MM (Sri Lanka)."""
    if not s: return None
    s = str(s).strip().replace(".", "/").replace("-", "/")
    from datetime import datetime
    for f in ("%d/%m/%Y", "%Y/%m/%d", "%d/%m/%y", "%m/%d/%Y"):
        try: return datetime.strptime(s, f).date()
        except ValueError: pass
    for f in ("%d %B %Y", "%d %b %Y", "%B %d %Y", "%B %d, %Y", "%d-%B-%Y"):
        try: return datetime.strptime(str(s).replace("/", " "), f).date()
        except ValueError: pass
    return None
