"""Demo: rebuild YOUR sample rows as real insurer Excels (no email sent).
Data transcribed from the PDFs you uploaded — verifies formats end-to-end."""
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.city_matcher import CityMatcher
from app.excel_gen import generate_excel
from app.dates_engine import new_dates, extend_dates

M = CityMatcher(Path(__file__).resolve().parent.parent / "data" / "cities.txt")
OUT = Path(__file__).resolve().parent.parent / "output" / "demo"

# 1) The two police-report people as a LOLC NEW batch applied 21 Oct 2026
d = new_dates(date(2026, 10, 21))
lolc_new = [
    dict(salutation="Mr.", gender="Male", surname="WIJELATH PEDIGE",
         given_names="NUWAN PIYANKARA WIJELATH", passport_no="N10199973",
         birthday=date(1989, 1, 18), addr_no="41/A",
         addr_street="SAMAGI MAWATHA KATUKENDA WEST", city=M.match_suffix("DANKOTUWA")["city"], **d),
    dict(salutation="Mr.", gender="Male", surname="RAJAPAKSHA PATHIRANAGE",
         given_names="IRESH LOSHANA WICKRAMASINGHA", passport_no="",
         birthday=date(2007, 10, 27), addr_no="203/C",
         addr_street="ANASLI HERATH MAWATHA, MUDUKATUWA", city=M.match_suffix("MARAWILA")["city"], **d),
]
print(generate_excel(lolc_new, "LOLC", "NEW", M.cities, OUT / "DEMO_LOLC_NEW_2026-10-21.xlsx"))

# 2) Same people as ALLIANZ NEW (MM/DD/YYYY + CONTACT NO + defaults)
allianz = [dict(r, telephone=t) for r, t in zip(lolc_new, ["0771234567", "0765742888"])]
allianz[1]["passport_no"] = "N0000000"  # placeholder: 2nd police report shows NIC, passport comes from passport doc
print(generate_excel(allianz, "ALLIANZ", "NEW", M.cities, OUT / "DEMO_ALLIANZ_NEW_2026-10-21.xlsx"))

# 3) LOLC EXTEND: embassy appointment 15 Oct 2026 -> departure 29 Oct 2026, arrival 29 Apr 2027
e = extend_dates(date(2026, 10, 15), issue=date(2026, 10, 21))
lolc_ext = [dict(salutation="Ms.", gender="Female", surname="RANKOTH PEDIGE",
                 given_names="AYANI NIROSHA", passport_no="N9755246", birthday=date(1984, 4, 14),
                 existing_policy_no="HODTST47813", addr_no="",
                 addr_street="PICHCHAWALA ROAD, PAHALA HATHTHINIYA",
                 city=M.match_suffix("MARAWILA")["city"],
                 issue_date=e["issue_date"], departure_date=e["departure_date"], arrival_date=e["arrival_date"])]
print(generate_excel(lolc_ext, "LOLC", "EXTEND", M.cities, OUT / "DEMO_LOLC_EXTEND_2026-10-21.xlsx"))
print("EXTEND check: appointment 2026-10-15 +14d =", e["departure_date"], "| +6mo =", e["arrival_date"])
