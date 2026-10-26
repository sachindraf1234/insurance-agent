"""E2E proof: police->CITY auto-put works through EVERY upload path (real files, real routes).
Run: python scripts/e2e_police_upload.py  (adds test rows to instance/insurance.db)"""
import sys, io
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import create_app
from app.models import Applicant

app = create_app(); c = app.test_client()
UP = Path("/home/user/uploads")
F1, F2 = "NUWAN PRIYANKARA POLICE REPORT - Copy.pdf", "IRESH LOCHANA POLICE REPORT - Copy.pdf"

def upload_one(fname, kind="NEW"):
    data = {"company": "LOLC", "kind": kind, "apply_date": "2026-10-21",
            "police": ((UP / fname).open("rb"), fname)}
    r = c.post("/upload", data=data, content_type="multipart/form-data", follow_redirects=False)
    assert r.status_code in (302, 303), r.status_code
    with app.app_context():
        a = Applicant.query.order_by(Applicant.id.desc()).first()
        print(f"[single {kind}] #{a.id} NO={a.addr_no} STREET={a.addr_street} CITY={a.city}")
        return a.id, a.city

with app.app_context():
    before = Applicant.query.count()

_id1, city1 = upload_one(F1)                 # expect Dankotuwa (circled-city sample 1)
assert city1 == "Dankotuwa", city1
_id2, city2 = upload_one(F2, kind="EXTEND")  # EXTEND path, orphan-city sample 2
assert city2 == "Marawila", city2

# bulk: both reports at once
data = {"company": "ALLIANZ", "kind": "NEW", "apply_date": "2026-10-21",
        "police_files": [((UP / F1).open("rb"), F1), ((UP / F2).open("rb"), F2)]}
r = c.post("/upload/bulk", data=data, content_type="multipart/form-data", follow_redirects=False)
assert r.status_code in (302, 303), r.status_code
with app.app_context():
    rows = Applicant.query.order_by(Applicant.id.desc()).limit(2).all()[::-1]
    print("[bulk]", [(a.id, a.addr_no, a.city) for a in rows])
    assert [a.city for a in rows] == ["Dankotuwa", "Marawila"], rows
    target = rows[0].id
    # break the city, then fix it via Review re-upload + via paste-address
    a = Applicant.query.get(target); a.city = ""; a.addr_street = ""
    from app.models import db; db.session.commit()

r = c.post(f"/review/{target}/readdress",
           data={"address_raw": "NO 41/A, SAMAGI MAWATHA KATUKENDA WEST, DANKOTUWA, SRI LANKA"},
           follow_redirects=False)
assert r.status_code in (302, 303)
with app.app_context():
    a = Applicant.query.get(target)
    print(f"[readdress] #{a.id} NO={a.addr_no} CITY={a.city}")
    assert a.city == "Dankotuwa" and a.addr_no == "41/A"

r = c.post(f"/review/{target}/police", data={"police": ((UP / F2).open("rb"), F2)},
           content_type="multipart/form-data", follow_redirects=False)
assert r.status_code in (302, 303)
with app.app_context():
    a = Applicant.query.get(target)
    print(f"[re-upload police] #{a.id} NO={a.addr_no} STREET={a.addr_street} CITY={a.city}")
    assert a.city == "Marawila" and a.addr_no == "203/C"
print("E2E POLICE->CITY: ALL UPLOAD PATHS PASS ✔")
