"""MCP server — exposes this insurance system as tools to any AI assistant
(Claude Desktop, Cursor, etc.) via the Model Context Protocol.

Run:    python mcp_server/server.py            (stdio transport)
Claude Desktop config (claude_desktop_config.json):
  {"mcpServers": {"insurance-agent": {
      "command": "python", "args": ["C:/path/to/insurance-ai-agent/mcp_server/server.py"]}}}

Then you can say things like:
  "Extract this passport and police report, match the city, and draft a LOLC NEW row for today"
Tools wrap the SAME tested pipeline functions as the web UI — no duplicate logic.
Sending email is intentionally NOT an MCP tool: a human approves & sends in the UI.
"""
from __future__ import annotations
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mcp.server.fastmcp import FastMCP  # pip install mcp
from app.city_matcher import CityMatcher
from app.address_parser import parse_address
from app.dates_engine import new_dates, extend_dates, fmt, parse_any
from app.constants import COMPANIES
from app.excel_gen import generate_excel, batch_filename
from app.pipeline import process_documents

mcp = FastMCP("insurance-agent")
_matcher = CityMatcher(ROOT / "data" / "cities.txt")

@mcp.tool()
def extract_documents(passport_path: str = "", police_report_path: str = "",
                      application_path: str = "") -> dict:
    """OCR-extract name/surname/birthday/passport-no (passport, MRZ-verified), address
    NO/STREET/CITY (police report) and telephone (application). Returns per-field confidence."""
    return process_documents(passport_path or None, police_report_path or None,
                             application_path or None, _matcher)


@mcp.tool()
def extract_police_report(police_report_path: str) -> dict:
    """Universal police-report step (same as every web upload): OCR the report,
    extract the address, split NO./STREET, and auto-match CITY to the dropdown
    list. Returns address parts + city score/method/candidates."""
    got = process_documents(police_path=police_report_path, city_matcher=_matcher)
    return {k: got.get(k) for k in ("police_name", "passport_no", "nic", "address_raw",
            "addr_no", "addr_street", "city", "city_score", "city_method",
            "city_candidates", "confidence", "_police_engine")}

@mcp.tool()
def match_city(address: str) -> dict:
    """Match the last city in an address against the insurer CITY dropdown list."""
    return _matcher.match_suffix(address) | {"parsed": parse_address(address, _matcher)}

@mcp.tool()
def search_cities(text: str) -> list[str]:
    """Type-ahead search of the 16,490-entry CITY dropdown list."""
    return _matcher.search(text)

@mcp.tool()
def calculate_dates(kind: str, issue_date: str = "", embassy_appointment_date: str = "",
                    company: str = "LOLC") -> dict:
    """NEW: departure=issue, arrival=+6mo. EXTEND: departure=appointment+14d, arrival=+6mo.
    Returns ISO dates + insurer-formatted strings (Allianz MM/DD/YYYY, others DD/MM/YYYY)."""
    comp = COMPANIES[company]
    if kind.upper() == "EXTEND":
        appt = parse_any(embassy_appointment_date)
        if not appt: return {"error": "embassy_appointment_date required (DD/MM/YYYY)"}
        dd = extend_dates(appt, parse_any(issue_date) or date.today())
    else:
        dd = new_dates(parse_any(issue_date) or date.today())
    return {k: v.isoformat() for k, v in dd.items()} | {
        k + "_formatted": fmt(v, comp["date_order"], comp["date_sep"]) for k, v in dd.items()}

@mcp.tool()
def generate_insurer_excel(applicants: list[dict], company: str, kind: str) -> str:
    """Create the insurer-format .xlsx (with SALUTATION/GENDER/CITY dropdowns).
    applicant keys: surname, given_names, passport_no, birthday(YYYY-MM-DD), salutation,
    gender, telephone, addr_no, addr_street, city, issue_date, departure_date, arrival_date,
    existing_policy_no (EXTEND). Human must review before emailing."""
    for a in applicants:
        for k in ("issue_date", "departure_date", "arrival_date", "birthday"):
            if isinstance(a.get(k), str) and a[k]:
                a[k] = date.fromisoformat(a[k][:10])
    out = ROOT / "output" / batch_filename(company, kind, date.today())
    return str(generate_excel(applicants, company, kind, _matcher.cities, out))

@mcp.tool()
def list_applicants(status: str = "") -> list[dict]:
    """Read applicants from the system database (web UI is the source of truth)."""
    from app import create_app
    from app.models import Applicant
    app = create_app()
    with app.app_context():
        q = Applicant.query
        if status: q = q.filter_by(status=status)
        return [a.as_dict() for a in q.order_by(Applicant.id.desc()).limit(100)]

@mcp.tool()
def record_embassy_appointment(passport_no: str, appointment_date: str) -> dict:
    """Record an embassy appointment and auto-create the EXTEND draft (+14d departure)."""
    from app import create_app
    from app.models import Applicant
    from app.inbox_service import make_extend_draft
    app = create_app()
    with app.app_context():
        src = Applicant.query.filter_by(passport_no=passport_no.upper())\
                .order_by(Applicant.id.desc()).first()
        if not src: return {"error": f"no applicant with passport {passport_no}"}
        d = parse_any(appointment_date)
        if not d: return {"error": "unparseable appointment_date"}
        n = make_extend_draft(src, d)
        return {"extend_draft_id": n.id, "departure_date": str(n.departure_date),
                "arrival_date": str(n.arrival_date)}

@mcp.tool()
def check_inbox_now() -> dict:
    """Fetch unseen email; match insurer policy replies + embassy appointments; notify."""
    from app import create_app
    from app.inbox_service import process_inbox
    app = create_app()
    with app.app_context():
        return process_inbox()

if __name__ == "__main__":
    mcp.run()
