from datetime import date

from app.constants import COMPANIES
from app.excel_gen import _row_for


def test_column_orders_are_exactly_the_four_uploaded_insurer_formats():
    assert COMPANIES['ALLIANZ']['columns_new'] == [
        'E-TRAVEL POLICY NO', 'ACS POLICY NO', 'ISSUE DATE', 'STATUS',
        'CUSTOMER PIN', 'SALUTATION', 'GENDER', 'FIRST NAME', 'LAST NAME',
        'PASSPORT NO', 'BIRTHDAY', 'CONTACT NO', 'TERRITORIAL AREA',
        'DEPATURE DATE', 'ARRIVAL DATE', 'PRODUCT', 'TOTAL PREMIUM',
        'AGENT NAME', 'NO.', 'STREET', 'CITY', 'ACS AGENT CODE', 'I CODE',
        'INDIVIDUAL CONTRACT',
    ]
    assert COMPANIES['ALLIANZ']['columns_extend'] == COMPANIES['ALLIANZ']['columns_new']
    assert COMPANIES['LOLC']['columns_new'] == [
        'ISSUE DATE', 'SALUTATION', 'GENDER', 'FIRST NAME', 'LAST NAME',
        'PASSPORT NO', 'BIRTHDAY', 'DEPATURE DATE', 'ARRIVAL DATE',
        'NO.', 'STREET', 'CITY',
    ]
    assert COMPANIES['LOLC']['columns_extend'] == [
        'INSURANCE POLICY NO', 'ISSUE DATE', 'SALUTATION', 'GENDER',
        'FIRST NAME', 'LAST NAME', 'PASSPORT NO', 'BIRTHDAY',
        'DEPATURE DATE', 'ARRIVAL DATE', 'NO.', 'STREET', 'CITY',
    ]


def test_sample_date_formats_are_per_company_and_column():
    applicant = {
        'issue_date': date(2026, 7, 10),
        'birthday': date(2003, 8, 2),
        'departure_date': date(2026, 7, 10),
        'arrival_date': date(2027, 1, 10),
        'existing_policy_no': 'HODTST47813',
    }
    allianz = _row_for(applicant, 'ALLIANZ', 'NEW', {})
    assert allianz['ISSUE DATE'] == '7/10/2026'
    assert allianz['BIRTHDAY'] == '02.08.2003'
    assert allianz['DEPATURE DATE'] == '7/10/2026'
    assert allianz['ARRIVAL DATE'] == '1/10/2027'
    lolc_new = _row_for(applicant, 'LOLC', 'NEW', {})
    assert lolc_new['ISSUE DATE'] == '10.07.2026'
    assert lolc_new['BIRTHDAY'] == '02.08.2003'
    lolc_extend = _row_for(applicant, 'LOLC', 'EXTEND', {})
    assert lolc_extend['INSURANCE POLICY NO'] == 'HODTST47813'
    assert lolc_extend['ISSUE DATE'] == ''
    assert lolc_extend['DEPATURE DATE'] == '10.07.2026'
