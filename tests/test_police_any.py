"""Police-report -> NO/STREET/CITY must work for ANY layout variant, not just the 2 samples.
Inputs below simulate OCR text of Sri Lanka Police Clearance variants."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.city_matcher import CityMatcher
from app.address_parser import parse_address
from app.ocr.extractors import extract_police_report

M = CityMatcher(Path(__file__).resolve().parent.parent / "data" / "cities.txt")

def run(ocr_text):
    f = extract_police_report(ocr_text, 0.85)
    return parse_address(f["address_raw"], M), f

BASE = """POLICE HEADQUARTERS, COLOMBO 02, SRI LANKA.
Police Clearance Certificate
This is to certify that:
Mr. TESTGE SAMPLE NAME PERERA
{ident}
{addr}
Has not come to the adverse notice of the Sri Lanka Police during the period from
01/01/1990 to 01/01/2026 according to available records.
"""

def test_passport_variant():
    p, f = run(BASE.format(ident="Passport number: N1234567",
                           addr="Address: NO,41/A,SAMAGI MAWATHA KATUKENDA WEST DANKOTUWA, SRI LANKA"))
    assert (p["no"], p["city"]) == ("41/A", "Dankotuwa") and "SAMAGI" in p["street"], p
    assert f["passport_no"] == "N1234567"
    assert f["salutation"] == "Mr." and f["gender"] == "Male"


def test_police_titles_map_gender_and_neutral_or_missing_titles_stay_blank():
    for title, expected_gender in (("Mrs.", "Female"), ("Miss", "Female"),
                                   ("Ms.", "Female"), ("Master", "Male"),
                                   ("Dr.", ""), ("Pastor", "")):
        text = BASE.format(ident="Passport number: N1234567",
                           addr="Address: 4, MAIN ROAD, DANKOTUWA, SRI LANKA").replace(
                               "Mr. TESTGE SAMPLE NAME PERERA", f"{title} TESTGE SAMPLE NAME PERERA")
        f = extract_police_report(text, 0.9)
        assert f["salutation"] == title and f["gender"] == expected_gender, (title, f)

    no_title = BASE.format(ident="Passport number: N1234567",
                           addr="Address: 4, MAIN ROAD, DANKOTUWA, SRI LANKA").replace(
                               "Mr. TESTGE SAMPLE NAME PERERA", "TESTGE SAMPLE NAME PERERA")
    f = extract_police_report(no_title, 0.9)
    assert f["salutation"] == "" and f["gender"] == ""


def test_process_documents_uses_police_title_and_clears_passport_gender_when_missing(monkeypatch):
    from app import pipeline
    passport_result = {"text":"mock passport", "confidence":0.99, "engine":"mock"}
    passport_fields = {"surname":"SAMPLE", "given_names":"APPLICANT", "passport_no":"N12345678",
                       "birthday":"1990-01-01", "gender":"Female", "salutation":"Ms.",
                       "_conf":{"surname":0.99, "given_names":0.99}}
    monkeypatch.setattr(pipeline, "extract_text", lambda *_args, **_kwargs: passport_result)
    monkeypatch.setattr(pipeline, "extract_passport", lambda *_args, **_kwargs: dict(passport_fields))
    parsed = {"no":"4", "street":"MAIN ROAD", "city":"Colombo", "city_candidates":[],
              "city_score":100, "city_method":"exact"}
    result_text = {"confidence":0.99, "engine":"mock"}

    def use_police_title(title, gender):
        police = {"address_raw":"4 MAIN ROAD COLOMBO SRI LANKA", "full_name":"", "passport_no":"", "nic":"",
                  "salutation":title, "gender":gender, "_conf":{}}
        monkeypatch.setattr(pipeline, "_best_police_read", lambda *_args, **_kwargs: (police, parsed, result_text))
        return pipeline.process_documents("passport.pdf", "police.pdf", city_matcher=M)

    assert use_police_title("Mr.", "Male")["gender"] == "Male"
    missing = use_police_title("", "")
    assert missing["salutation"] == "" and missing["gender"] == ""
    passport_only = pipeline.process_documents("passport.pdf", city_matcher=M)
    assert passport_only["salutation"] == "" and passport_only["gender"] == ""

def test_nic_variant_city_on_own_line_before_address():
    # circled CITY OCR'd as orphan lines BEFORE the Address line (like sample 2)
    txt = BASE.format(ident="National Identity card number: 200730103190",
                      addr="SRI LANKA.\nMARAWILA\nAddress: NO 203/C, ANASLI HERATH MAWATHA, MUDUKATUWA")
    p, f = run(txt)
    assert (p["no"], p["city"]) == ("203/C", "Marawila"), p
    assert p["street"] == "ANASLI HERATH MAWATHA, MUDUKATUWA", p

def test_label_misread_structural_fallback():
    p, _ = run(BASE.format(ident="Passport number:\nP7654321",
                           addr="Adclress; 12/B, TEMPLE ROAD, KOSWADIYA, MAHAWEWA, SRI LANKA"))
    assert p["city"] == "Mahawewa" and "TEMPLE" in p["street"], p

def test_no_house_number_and_multiword_city():
    p, _ = run(BASE.format(ident="NIC: 901234567V",
                           addr="Address: WEWEGAMA ROAD, Suriyawewa Town, SRI LANKA"))
    assert p["no"] == "" and p["city"] == "Suriyawewa Town", p

def test_province_and_postal_tails():
    p, _ = run(BASE.format(ident="Passport number: N9999999",
                           addr="Address: 5, LAKE ROAD, MARAWILA, NORTH WESTERN PROVINCE, SRI LANKA"))
    assert p["city"] == "Marawila" and p["street"] == "LAKE ROAD", p
    p, _ = run(BASE.format(ident="Passport number: N9999999",
                           addr="Address: 5, LAKE ROAD, DANKOTUWA 61130 SRI LANKA"))
    assert p["city"] == "Dankotuwa" and "61130" not in p["street"], p

def test_ocr_confusions_in_city_and_house_no():
    p, _ = run(BASE.format(ident="Passport number: N9999999",
                           addr="Address: NO 2O3/C, MAIN STREET, DANK0TUWA, SRI LANKA"))
    assert (p["no"], p["city"]) == ("203/C", "Dankotuwa"), p
    p, _ = run(BASE.format(ident="Passport number: N9999999",
                           addr="Address: 8, MAIN STREET, MARAVILA, SRI LANKA"))  # W dropped by OCR
    assert p["city"] == "Marawila" or "Marawila" in p["city_candidates"], p

def test_regional_report_residing_at_layout_and_slash_number_ocr():
    text = """Police Report
This is to certify the applicant holding National Identity Card bearing
number 123456789V who has been residing at, No: 122/!, BedumPava
- Dikwela, Yogiyana.
. in myPolice Station
area from 2015. has not been involved in any criminal activities.
"""
    f = extract_police_report(text, 0.92)
    p = parse_address(f["address_raw"], M)
    assert p["no"] == "122/1", (f, p)
    assert p["street"] == "BEDUMPAVA, DIKWELA", (f, p)
    assert p["city"] == "Yogiyana", (f, p)

def test_single_line_application_address_does_not_become_a_house_number():
    p = parse_address("NO: 122/ I BEDUM PARA DIKWELA YOGIYANA", M)
    assert (p["no"], p["street"], p["city"]) == ("122/1", "BEDUM PARA DIKWELA", "Yogiyana"), p

def test_house_number_anywhere_goes_to_no_and_other_details_stay_in_street():
    p = parse_address("BEDUM PARA, NO: 122/1, DIKWELA ROAD, YOGIYANA, SRI LANKA", M)
    assert p["no"] == "122/1", p
    assert p["street"] == "BEDUM PARA, DIKWELA ROAD", p
    assert p["city"] == "Yogiyana", p

    p = parse_address("STATION ROAD, HOUSE 8/2, KATUKENDA, DANKOTUWA, SRI LANKA", M)
    assert p["no"] == "8/2", p
    assert p["street"] == "STATION ROAD, HOUSE, KATUKENDA", p
    assert p["city"] == "Dankotuwa", p
