import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.dates_engine import add_months, new_dates, extend_dates, fmt
from app.city_matcher import CityMatcher
from app.address_parser import parse_address
from app.ocr.mrz import parse_td3
from app.phone import normalize_phone, find_phones

M = CityMatcher(Path(__file__).resolve().parent.parent / "data" / "cities.txt")

def test_dates():
    assert add_months(date(2026, 8, 31), 6) == date(2027, 2, 28)
    n = new_dates(date(2026, 7, 10)); assert n["arrival_date"] == date(2027, 1, 10)
    e = extend_dates(date(2026, 10, 15)); assert e["departure_date"] == date(2026, 10, 29)
    assert e["arrival_date"] == date(2027, 4, 29)
    assert fmt(date(2026, 7, 10), "MDY") == "07/10/2026"
    assert fmt(date(2026, 7, 10), "DMY") == "10/07/2026"

def test_city_and_address():
    p = parse_address("NO,41/A,SAMAGI MAWATHA KATUKENDA WEST DANKOTUWA, SRI LANKA", M)
    assert p["no"] == "41/A" and p["city"] == "Dankotuwa", p
    assert "SAMAGI" in p["street"] and "DANKOTUWA" not in p["street"], p
    p = parse_address("NO 203/C, ANASLI HERATH MAWATHA, MUDUKATUWA, MARAWILA, SRI LANKA.", M)
    assert p["no"] == "203/C" and p["city"] == "Marawila", p
    p = parse_address("TEMPLE ROAD,KOSWADIYA, MAHAWEWA", M)
    assert p["no"] == "" and p["city"], p

def test_mrz():
    # synthetic TD3 with correct ICAO check digits (LKA example-style)
    from app.ocr.mrz import check_digit
    l1 = "P<LKAWIJELATH<PEDIGE<<NUWAN<PIYANKARA<WIJELATH".ljust(44, "<")[:44]
    body = "N10199973" + str(check_digit("N10199973")) + "LKA" + "890118" + str(check_digit("890118")) \
           + "M" + "340117" + str(check_digit("340117")) + "<<<<<<<<<<<<<<"
    r = parse_td3(l1, (body + "0")[:44].ljust(44, "<"))
    assert r["passport_no"] == "N10199973" and r["checks"]["passport_no"], r
    assert r["birthday"] == "1989-01-18" and r["gender"] == "Male" and r["surname"].startswith("WIJELATH"), r

def test_phone():
    assert normalize_phone("+94 77 123 4567") == "0771234567"
    assert normalize_phone("0771234567") == "0771234567"
    assert normalize_phone("0094 77 123 4567") == "0771234567"
    assert normalize_phone("not a phone") == ""
    assert find_phones("Call me 076-904-7717 please") == ["0769047717"]
    assert find_phones("Telephone: +94 (77) 123 4567") == ["0771234567"]
    assert find_phones("Mobile 077 12O 4567") == ["0771204567"]
    assert normalize_phone("0701821637") == "0701821637"


def test_passport_visual_names_and_ocr_broken_mrz():
    from app.ocr.extractors import extract_passport, extract_application
    from app.ocr.mrz import check_digit
    got = extract_passport("PASSPORT\nSurname:\nPERERA\nGiven Names: KASUN NIMAL\nPassport No: N12345678", 0.91)
    assert got["surname"] == "PERERA" and got["given_names"] == "KASUN NIMAL", got
    # OCR reads the leading MRZ chevron as B and the visual label as “Other Naes”.
    mrz = "PBLKANANAYAKKARA<WARNAKULA<PATABADIGE<<<<<<<\nN8579238<3LKA9004241F2910312906152720V<<<<50"
    realish = ("/ Surname\nNANAYAKKARA WARNAKULA PATABADIGE\na ug / Other Naes\n"
               "NIMESHA PRIYASANI PERERA\n" + mrz)
    got = extract_passport(realish, 0.88)
    assert got["surname"] == "NANAYAKKARA WARNAKULA PATABADIGE", got
    assert got["given_names"] == "NIMESHA PRIYASANI PERERA", got
    assert got["passport_no"] == "N8579238" and got["birthday"] == "1990-04-24", got
    assert all(got["_mrz_checks"].values()), got


def test_application_phone_prefers_labeled_mobile_and_passport_number():
    from app.ocr.extractors import extract_application
    text = "Applicant ref 777906905\nOther contact 768584292\nMobile No: 077 583 7494\nPassport/Passport cod/code Nr./NO.:N9983784"
    app = extract_application(text)
    assert app["telephone"] == "0775837494" and app["passport_no"] == "N9983784", app
    ambiguous = extract_application("Other numbers: 777906905 and 768584292")
    assert ambiguous["telephone"] == ""  # never guess among unlabelled numbers

def test_passport_mrz_name_visual_conflict_is_flagged():
    from app.ocr.extractors import extract_passport
    # MRZ names have no ICAO check digit; cross-check against visible name text.
    text = ("Surname: WIJELATH\nGiven Names: PEDIGE NUWAN PIYANKARA\n"
            "P<LKAWIELATH<<PEDIGE<NUWAN<PIYANKARA\n"
            "N101999734LKA8901181M3401170<<<<<<<<<<<<<<<<")
    got = extract_passport(text)
    assert got["passport_no"] == "N10199973"
    assert got["_conf"]["surname"] < 0.82  # Review screen must flag for human verification.
    assert got["_conf"]["given_names"] >= 0.82

def test_passport_uses_close_labeled_name_to_fix_mrz_ocr_typos():
    from app.ocr.extractors import extract_passport
    text = ("Surname: TEST\nGiven Names: TESTNAME SMITH\n"
            "PBLKATEST<<T0STNAME<SM1TH<<<<<<<<<<<\n"
            "N123456789LKA9004241M2910312906152720V<<<<50")
    got = extract_passport(text, 0.86)
    assert got["surname"] == "TEST", got
    assert got["given_names"] == "TESTNAME SMITH", got
    assert got["_conf"]["given_names"] >= 0.82

def test_application_address_extraction_and_verified_corroboration():
    from app.ocr.extractors import extract_application
    from app.pipeline import _corroborate_application_address
    app = extract_application("""APPLICATION FORM
Address
NO: 122/1 BEDUM PARA DIKWELA YOGIYANA
Mobile
0771234567
Passport No
N12345678
""")
    assert app["address_raw"] == "NO: 122/1 BEDUM PARA DIKWELA YOGIYANA", app
    out = {"passport_no": "N12345678", "addr_no": "122/1",
           "addr_street": "BEDUMPAVA, DIKWELA", "city": "Yogiyana",
           "address_raw": "POLICE REPORT ADDRESS", "confidence": {"city": 1.0}}
    _corroborate_application_address(out, app, M)
    assert out["addr_street"] == "BEDUM PARA DIKWELA", out
    assert out["address_raw"] == "POLICE REPORT ADDRESS", out  # police stays primary
    unverified = dict(out, addr_street="BEDUMPAVA, DIKWELA")
    wrong_app = dict(app, passport_no="N87654321")
    _corroborate_application_address(unverified, wrong_app, M)
    assert unverified["addr_street"] == "BEDUMPAVA, DIKWELA", unverified


def test_application_address_before_label_fills_missing_police_number_and_street():
    from app.ocr.extractors import extract_application
    from app.pipeline import _corroborate_application_address
    app = extract_application("""Civil Status
SINGLE
NO:111/4, ATHGALA, KOCHCHIKADE, SR LANKA
Address
Tel (Home)
0764522626
Passport No
N9823751
""")
    assert app["address_raw"] == "NO:111/4, ATHGALA, KOCHCHIKADE, SR LANKA", app
    out = {"passport_no": "N9823751", "addr_no": "", "addr_street": "",
           "city": "Kochchikade", "address_raw": "KOCHCHIKADE", "confidence": {"city": 1.0}}
    _corroborate_application_address(out, app, M)
    assert (out["addr_no"], out["addr_street"], out["city"]) == (
        "111/4", "ATHGALA", "Kochchikade"), out
    assert out["address_raw"] == app["address_raw"], out
    assert "police address OCR incomplete" in out["_address_source"], out


def test_mixed_pdf_ocr_keeps_digital_page_and_reads_image_only_page(tmp_path, monkeypatch):
    import pymupdf as fitz
    from PIL import Image
    from io import BytesIO
    from app.ocr import engine
    pdf=fitz.open()
    digital=pdf.new_page()
    digital.insert_textbox(fitz.Rect(40,40,550,780),('DIGITAL_PAGE_TEXT ' * 40))
    scanned=pdf.new_page()
    image=Image.new('RGB',(300,120),'white')
    buffer=BytesIO();image.save(buffer,format='PNG')
    scanned.insert_image(fitz.Rect(40,40,340,160),stream=buffer.getvalue())
    path=tmp_path/'mixed.pdf';pdf.save(path);pdf.close()
    calls=[]
    def fake_ocr(_png):
        calls.append(1)
        return {'text':'Mobile No: 077 123 4567','confidence':0.9,'engine':'mock-ocr'}
    monkeypatch.setattr(engine,'ocr_image',fake_ocr)
    result=engine.extract_text(path)
    assert 'DIGITAL_PAGE_TEXT' in result['text']
    assert 'Mobile No: 077 123 4567' in result['text']
    assert len(calls)==1 and result['engine']=='mixed'
