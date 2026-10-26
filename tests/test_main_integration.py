from datetime import date
from pathlib import Path
import yaml
from flask import Flask
import pytest

ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture
def integration_app(tmp_path):
    import sys
    sys.path.insert(0, str(ROOT))
    from app.models import db
    from app.city_matcher import CityMatcher
    from app.routes import bp
    app = Flask('integration-test', template_folder=str(ROOT/'app'/'templates'))
    app.config.update(SECRET_KEY='test-only', TESTING=True,
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path/'integration.sqlite'}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False, APP_CFG=yaml.safe_load((ROOT/'config.yaml').read_text()),
        BASE=ROOT, UPLOAD_DIR=tmp_path/'uploads', OUTPUT_DIR=tmp_path/'output',
        INBOX_DIR=tmp_path/'inbox', MAIN_SYSTEM_API_KEY='unit-test-shared-secret')
    for key in ('UPLOAD_DIR','OUTPUT_DIR','INBOX_DIR'):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)
    db.init_app(app)
    app.config['CITY_MATCHER'] = CityMatcher(ROOT/'data'/'cities.txt')
    app.register_blueprint(bp)
    with app.app_context(): db.create_all()
    yield app
    with app.app_context(): db.drop_all()


def test_main_system_webhook_auth_and_idempotent_extend(integration_app):
    from app.models import db, Applicant
    app = integration_app
    with app.app_context():
        src = Applicant(company='LOLC', kind='NEW', status='POLICY_RECEIVED',
                        surname='TEST', given_names='APPLICANT', passport_no='N12345678',
                        issue_date=date(2026, 1, 1), departure_date=date(2026, 1, 1),
                        arrival_date=date(2026, 7, 1), new_policy_no='HODTST12345')
        db.session.add(src); db.session.commit()
    client = app.test_client()
    url = '/api/integrations/embassy-appointment'
    payload = {'passport_no':'N12345678','appointment_date':'2026-10-16'}
    assert client.post(url, json=payload).status_code == 401
    assert client.post(url, json=payload, headers={'Authorization':'Bearer wrong'}).status_code == 401
    headers={'Authorization':'Bearer unit-test-shared-secret'}
    created = client.post(url, json=payload, headers=headers)
    assert created.status_code == 201
    data = created.get_json()
    assert data['departure_date']=='2026-10-30' and data['arrival_date']=='2027-04-30'
    duplicate = client.post(url, json=payload, headers=headers)
    assert duplicate.status_code == 200 and duplicate.get_json()['already_exists'] is True
    with app.app_context():
        assert Applicant.query.filter_by(passport_no='N12345678',kind='EXTEND').count()==1


def test_dashboard_is_summary_only_and_insurance_has_nested_choices(integration_app):
    c = integration_app.test_client()
    response = c.get('/')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'Embassy Interviews' not in html
    assert 'Recruitment system sync' in html
    assert 'New · Single' not in html and 'New · Batch' not in html
    assert 'Workflow at a glance' not in html
    details_start=html.index('<details class="insurance-nav"')
    details_tag=html[details_start:html.index('>',details_start)]
    assert 'open' not in details_tag
    assert c.get('/appointments').status_code == 302

    insurance = c.get('/insurance')
    assert insurance.status_code == 200
    page = insurance.get_data(as_text=True)
    assert 'New insurance applications' in page and 'Extend existing insurance' in page
    assert page.count('class="insurance-kind-title') == 2
    assert '>Single</a>' not in page and '>Batch</a>' not in page
    child=c.get('/insurance/NEW/SINGLE').get_data(as_text=True)
    child_start=child.index('<details class="insurance-nav"')
    child_tag=child[child_start:child.index('>',child_start)]
    assert 'open' in child_tag


def test_dashboard_clear_soft_cancels_only_selected_recent_applicants(integration_app):
    from app.models import db, Applicant
    app = integration_app
    with app.app_context():
        selected = Applicant(company='LOLC', kind='NEW', status='NEEDS_REVIEW', surname='CLEAR', given_names='ME')
        untouched = Applicant(company='ALLIANZ', kind='NEW', status='APPROVED', surname='KEEP', given_names='ME')
        ineligible = Applicant(company='LOLC', kind='EXTEND', status='AWAITING_REPLY', surname='SENT', given_names='ROW')
        db.session.add_all([selected, untouched, ineligible]); db.session.commit()
        selected_id, untouched_id, ineligible_id = selected.id, untouched.id, ineligible.id
    client = app.test_client()
    dashboard = client.get('/').get_data(as_text=True)
    assert 'Clear selected' in dashboard and 'select-all-recent' in dashboard
    assert f'name="applicant_ids" value="{selected_id}"' in dashboard
    assert client.post('/dashboard/recent/clear', data={'applicant_ids': str(selected_id)}).status_code == 302
    with app.app_context():
        assert Applicant.query.count() == 3
        assert db.session.get(Applicant, selected_id).status == 'CANCELLED_NEEDS_REVIEW'
        assert db.session.get(Applicant, untouched_id).status == 'APPROVED'
        assert db.session.get(Applicant, ineligible_id).status == 'AWAITING_REPLY'
    updated = client.get('/').get_data(as_text=True)
    assert 'CLEAR ME' not in updated and 'KEEP ME' in updated and 'SENT ROW' in updated


def test_dashboard_clear_rejects_applicants_outside_recent_table(integration_app):
    from app.models import db, Applicant
    app = integration_app
    with app.app_context():
        people = [Applicant(company='LOLC', kind='NEW', status='NEEDS_REVIEW',
                            surname=f'PERSON{i:02d}', given_names='OUTSIDE TEST') for i in range(16)]
        db.session.add_all(people); db.session.commit()
        hidden_id = people[0].id
    response = app.test_client().post('/dashboard/recent/clear', data={'applicant_ids': str(hidden_id)})
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Applicant, hidden_id).status == 'NEEDS_REVIEW'


def test_dashboard_lists_new_pending_applicants_first_with_stable_ids(integration_app):
    from app.models import db, Applicant
    app = integration_app
    with app.app_context():
        first = Applicant(company='LOLC', kind='NEW', status='APPROVED', surname='OLDER', given_names='APPROVED')
        second = Applicant(company='LOLC', kind='NEW', status='NEEDS_REVIEW', surname='PEND_EARLIER', given_names='EARLIER')
        third = Applicant(company='ALLIANZ', kind='EXTEND', status='EXTEND_DRAFT', surname='PEND_NEWEST', given_names='NEWEST')
        cancelled = Applicant(company='LOLC', kind='NEW', status='CANCELLED_USER', surname='HIDDEN', given_names='RECORD')
        db.session.add_all([first, second, third, cancelled]); db.session.commit()
        ids = {a.surname: a.id for a in (first, second, third, cancelled)}
    html = app.test_client().get('/').get_data(as_text=True)
    # Pending rows come first, newest ID first; the # value remains the actual
    # applicant ID and is rendered in the same row as that applicant.
    expected = [
        f'<td>INS {ids["PEND_NEWEST"]}</td><td><b>PEND_NEWEST NEWEST</b></td>',
        f'<td>INS {ids["PEND_EARLIER"]}</td><td><b>PEND_EARLIER EARLIER</b></td>',
        f'<td>INS {ids["OLDER"]}</td><td><b>OLDER APPROVED</b></td>',
    ]
    positions = [html.index(item) for item in expected]
    assert positions == sorted(positions)
    assert 'HIDDEN RECORD' not in html
    assert f'<td>INS {ids["PEND_NEWEST"]}</td><td><b>PEND_NEWEST NEWEST</b></td>' in html


def test_review_shows_actual_sheet_row_and_cancel_is_reversible(integration_app):
    from app.models import db, Applicant
    app = integration_app
    with app.app_context():
        applicant = Applicant(company='ALLIANZ', kind='NEW', entry_mode='BATCH', status='NEEDS_REVIEW',
            salutation='Mr.', gender='Male', surname='PERERA', given_names='KASUN NIMAL',
            passport_no='N12345678', birthday=date(1990, 2, 3), telephone='0771234567',
            addr_no='12/A', addr_street='MAIN ROAD', city='Colombo', address_raw='12/A MAIN ROAD COLOMBO',
            issue_date=date(2026, 10, 22), departure_date=date(2026, 10, 22), arrival_date=date(2027, 4, 22),
            confidence_json='{}')
        db.session.add(applicant); db.session.commit(); aid=applicant.id
    client=app.test_client()
    response=client.get(f'/review/{aid}')
    assert response.status_code==200
    page=response.get_data(as_text=True)
    assert 'ALLIANZ insurer sheet · NEW' in page
    assert f'Final check — INS {aid}' in page
    assert 'PERERA KASUN NIMAL · Passport N12345678' in page
    dashboard = client.get('/').get_data(as_text=True)
    assert f'<td>INS {aid}</td><td><b>PERERA KASUN NIMAL</b></td>' in dashboard
    assert 'N12345678' in dashboard and 'ALLIANZ' in dashboard and 'NEEDS REVIEW' in dashboard
    assert 'Worldwide E' in page and 'DDP JAYASURIYA' in page
    assert 'PERERA' in page and 'KASUN NIMAL' in page and '0771234567' in page
    assert 'E-TRAVEL POLICY NO' in page and 'CONTACT NO' in page
    assert 'data-sheet-column="FIRST NAME"' in page and 'data-sheet-column="INDIVIDUAL CONTRACT"' in page
    assert 'Remove applicant from active tables' in page and 'Cancel · return to company reviews' in page
    assert client.post(f'/applicants/{aid}/cancel').status_code==302
    assert client.get('/reviews?kind=NEW&mode=BATCH&status=PENDING&company=LOLC').status_code==200
    assert 'PERERA' not in client.get('/reviews?kind=NEW&mode=BATCH&status=PENDING&company=LOLC').get_data(as_text=True)
    assert 'PERERA' not in client.get('/applicants').get_data(as_text=True)
    assert 'PERERA' in client.get('/applicants?status=CANCELLED').get_data(as_text=True)
    assert client.post(f'/applicants/{aid}/restore').status_code==302
    with app.app_context():
        assert db.session.get(Applicant, aid).status=='NEEDS_REVIEW'
        ext=Applicant(company='LOLC',kind='EXTEND',entry_mode='SINGLE',status='NEEDS_REVIEW',
            salutation='Ms.',gender='Female',surname='JAYASINGHE',given_names='ANU THARUSHI',
            passport_no='N11112222',birthday=date(1995,8,9),telephone='0771112222',
            addr_no='9',addr_street='LAKE ROAD',city='Gampaha',existing_policy_no='LOLC-EXT-991',
            embassy_appointment_date=date(2026,10,1),departure_date=date(2026,10,15),arrival_date=date(2027,4,15),
            confidence_json='{}')
        db.session.add(ext);db.session.commit();ext_id=ext.id
    ext_page=client.get(f'/review/{ext_id}').get_data(as_text=True)
    assert 'INSURANCE POLICY NO' in ext_page and 'LOLC-EXT-991' in ext_page
    assert 'JAYASINGHE' in ext_page and 'ANU THARUSHI' in ext_page


def test_all_four_intake_forms_keep_phone_documents_and_extend_policy_scope(integration_app):
    client=integration_app.test_client()
    for kind in ('NEW','EXTEND'):
        for mode in ('SINGLE','BATCH'):
            response=client.get(f'/insurance/{kind}/{mode}')
            assert response.status_code==200
            page=response.get_data(as_text=True)
            assert 'Extract &amp; show insurer preview' in page
            assert 'Remove selected files' in page and 'Cancel' in page
            assert 'Applicant application / phone document' in page if mode=='SINGLE' else 'batch_files' in page and 'applicant/phone forms' in page
            assert 'name="application"' in page if mode=='SINGLE' else 'name="batch_files"' in page
            assert ('Existing insurance policy / certificate copy' in page) == (kind=='EXTEND' and mode=='SINGLE')
            assert ('plus existing policy documents' in page) == (kind=='EXTEND' and mode=='BATCH')


def test_approval_moves_record_from_pending_to_approved_table(integration_app):
    from app.models import db, Applicant
    app=integration_app
    with app.app_context():
        applicant=Applicant(company='LOLC',kind='NEW',entry_mode='SINGLE',status='NEEDS_REVIEW',
            salutation='Mr.',gender='Male',surname='SILVA',given_names='NIMAL KUMARA',
            passport_no='N87654321',birthday=date(1992,5,6),telephone='0771234567',
            addr_no='45',addr_street='TEMPLE ROAD',city='Colombo',
            issue_date=date(2026,10,22),departure_date=date(2026,10,22),arrival_date=date(2027,4,22),
            confidence_json='{}')
        db.session.add(applicant);db.session.commit();aid=applicant.id
    import json
    client=app.test_client()
    row={'ISSUE DATE':'2026-10-22','SALUTATION':'Mr.','GENDER':'Male','FIRST NAME':'SILVA',
         'LAST NAME':'NIMAL KUMARA','PASSPORT NO':'N87654321','BIRTHDAY':'1992-05-06',
         'DEPATURE DATE':'2026-10-22','ARRIVAL DATE':'2027-04-22','NO.':'45',
         'STREET':'TEMPLE ROAD','CITY':'Colombo'}
    base={'sheet_row_json':json.dumps(row),'approve':'1'}
    response=client.post(f'/review/{aid}',data=base)
    assert response.status_code==302 and f'/review/{aid}' in response.location
    with app.app_context(): assert db.session.get(Applicant,aid).status=='NEEDS_REVIEW'
    # Every cell survives save; approval is blocked until the explicit final-check box is ticked.
    page=client.get(f'/review/{aid}').get_data(as_text=True)
    assert 'value="SILVA"' in page and 'value="TEMPLE ROAD"' in page
    row['FIRST NAME']='SILVA'
    response=client.post(f'/review/{aid}',data={**base,'sheet_row_json':json.dumps(row),'final_check':'1'})
    assert response.status_code==302
    assert f'/reviews?kind=NEW&mode=SINGLE&status=APPROVED' in response.location
    assert 'SILVA' not in client.get('/reviews?kind=NEW&mode=SINGLE&status=PENDING&company=LOLC').get_data(as_text=True)
    assert 'SILVA' in client.get('/reviews?kind=NEW&mode=SINGLE&status=APPROVED&company=LOLC').get_data(as_text=True)
    with app.app_context(): assert db.session.get(Applicant,aid).status=='APPROVED'
    assert client.post(f'/applicants/{aid}/cancel').status_code==302
    assert 'SILVA' not in client.get('/reviews?kind=NEW&mode=SINGLE&status=APPROVED&company=LOLC').get_data(as_text=True)
    assert 'SILVA' not in client.get('/emails').get_data(as_text=True)
    assert client.post(f'/applicants/{aid}/restore').status_code==302
    with app.app_context(): assert db.session.get(Applicant,aid).status=='APPROVED'


def test_single_upload_shows_complete_allianz_row_before_final_review(integration_app):
    from io import BytesIO
    from unittest.mock import patch
    from app.models import db,Applicant
    app=integration_app;client=app.test_client()
    fake={"surname":"SAMPLE SURNAME","given_names":"SAMPLE GIVEN NAMES","passport_no":"N12345678",
          "birthday":"1990-02-03","gender":"Female","salutation":"Ms.","telephone":"0771234567",
          "addr_no":"12/A","addr_street":"MAIN ROAD","city":"Colombo","address_raw":"12/A MAIN ROAD COLOMBO",
          "confidence":{}}
    with patch('app.routes.process_documents',return_value=fake):
        response=client.post('/upload',data={'kind':'NEW','company':'ALLIANZ','apply_date':'2026-10-23',
            'passport':(BytesIO(b'passport'),'passport.pdf'),'police':(BytesIO(b'police'),'police.pdf')},
            content_type='multipart/form-data')
    assert response.status_code==302 and response.location.startswith('/insurance/NEW/SINGLE')
    page=client.get(response.location).get_data(as_text=True)
    assert 'id="format-preview"' in page and 'Selected company’s insurer-sheet preview' in page
    assert 'SAMPLE SURNAME' in page and 'SAMPLE GIVEN NAMES' in page and '0771234567' in page
    table=page.split('id="format-preview"',1)[1].split('</table>',1)[0]
    assert 'E-TRAVEL POLICY NO' in table and 'INDIVIDUAL CONTRACT' in table
    assert table.count('<th>')==len(__import__('app.constants',fromlist=['COMPANIES']).COMPANIES['ALLIANZ']['columns_new'])
    assert 'Open this row in Review' not in page
    assert 'Open Review queue' not in page and 'Review extracted applicants' not in page
    assert 'Remove applicant' not in page and 'Clear form' in page
    assert page.index('id="upload-card"') < page.index('id="selected-company-preview"')
    with app.app_context():
        applicant=Applicant.query.first()
        assert applicant.status=='NEEDS_REVIEW' and applicant.surname=='SAMPLE SURNAME'
        aid=applicant.id
    # Persistent preview survives a refresh without preview_ids in the URL.
    refreshed=client.get('/insurance/NEW/SINGLE?company=ALLIANZ').get_data(as_text=True)
    assert 'SAMPLE SURNAME' in refreshed and 'SAMPLE GIVEN NAMES' in refreshed
    review=client.get(f'/review/{aid}').get_data(as_text=True)
    review_table=review.split('id="sheet-preview-table"',1)[1].split('</table>',1)[0]
    assert review_table.split('<tbody>',1)[0].count('<th ')==len(__import__('app.constants',fromlist=['COMPANIES']).COMPANIES['ALLIANZ']['columns_new'])
    assert 'value="10/23/2026"' in review_table and 'value="03.02.1990"' in review_table
    # Clear form clears only the persistent preview; the applicant remains available in Review.
    cleared=client.post('/insurance/clear-preview',data={'company':'ALLIANZ','kind':'NEW','mode':'SINGLE'})
    assert cleared.status_code==302
    after_clear=client.get(cleared.location).get_data(as_text=True)
    table_after_clear=after_clear.split('id="format-preview"',1)[1].split('</table>',1)[0]
    assert 'SAMPLE SURNAME' not in table_after_clear
    assert client.get(f'/review/{aid}').status_code==200


def test_batch_upload_shows_each_row_in_exact_extend_sheet_before_review(integration_app):
    from io import BytesIO
    from unittest.mock import patch
    from app.models import db,Applicant
    from app.constants import COMPANIES
    app=integration_app;client=app.test_client()
    def fake_process(passport_path=None,police_path=None,application_path=None,**kwargs):
        if passport_path:
            idx='ONE' if 'one' in str(passport_path).lower() else 'TWO'
            num='N11111111' if idx=='ONE' else 'N22222222'
            return {'surname':f'{idx} SURNAME','given_names':f'{idx} GIVEN','passport_no':num,
                    'birthday':'1990-01-01','gender':'Male','salutation':'Mr.','confidence':{}}
        return {'address_raw':'1 MAIN ROAD COLOMBO','addr_no':'1','addr_street':'MAIN ROAD',
                'city':'Colombo','confidence':{}}
    data={'kind':'EXTEND','company':'ALLIANZ','embassy_date':'2026-10-01',
          'passport_files':[(BytesIO(b'p1'),'one.pdf'),(BytesIO(b'p2'),'two.pdf')],
          'police_files':[(BytesIO(b'r1'),'one-report.pdf'),(BytesIO(b'r2'),'two-report.pdf')]}
    with patch('app.routes.process_documents',side_effect=fake_process):
        response=client.post('/upload/bulk',data=data,content_type='multipart/form-data')
    assert response.status_code==302 and response.location.startswith('/insurance/EXTEND/BATCH')
    page=client.get(response.location).get_data(as_text=True)
    assert 'Selected company’s insurer-sheet preview' in page and 'ALLIANZ · EXTEND · Batch' in page
    table=page.split('id="format-preview"',1)[1].split('</table>',1)[0]
    assert table.count('<th>')==len(COMPANIES['ALLIANZ']['columns_extend'])
    assert table.count('<tr>')==3  # header + two applicant rows
    assert 'ONE SURNAME' in table and 'TWO SURNAME' in table and 'STATUS' in table and 'EXTEND' in table
    assert 'name="salutation_' in table and 'name="gender_' in table
    assert 'value="Mr." selected' not in table and 'value="Male" selected' not in table
    with app.app_context():
        rows=Applicant.query.order_by(Applicant.id).all()
        assert len(rows)==2 and all(a.status=='NEEDS_REVIEW' for a in rows)


def test_batch_uploads_create_separate_company_groups_and_review_exact_rows(integration_app):
    from io import BytesIO
    from unittest.mock import patch
    from app.constants import COMPANIES
    from app.models import db, Applicant, ApplicantGroup
    client = integration_app.test_client()

    def extractor(passport_path=None, police_path=None, **kwargs):
        path = str(passport_path or police_path).lower()
        name = 'ALPHA' if 'alpha' in path else 'BETA' if 'beta' in path else 'GAMMA' if 'gamma' in path else 'DELTA'
        if passport_path:
            return {'surname': f'{name} SURNAME', 'given_names': f'{name} GIVEN NAMES',
                    'passport_no': f'N{abs(hash(name)) % 100000000:08d}', 'birthday': '1990-02-03',
                    'gender': 'Female', 'salutation': 'Ms.', 'telephone': '0771234567', 'confidence': {}}
        return {'address_raw': f'12 MAIN ROAD {name} COLOMBO', 'addr_no': '12',
                'addr_street': 'MAIN ROAD', 'city': 'Colombo', 'salutation':'Ms.', 'gender':'Female',
                'telephone': '0771234567', 'confidence': {}}

    def upload(names):
        data = {'kind': 'NEW', 'company': 'ALLIANZ', 'apply_date': '2026-10-23',
                'passport_files': [(BytesIO(b'passport'), f'{name}.pdf') for name in names],
                'police_files': [(BytesIO(b'police'), f'{name}-report.pdf') for name in names]}
        with patch('app.routes.process_documents', side_effect=extractor):
            response = client.post('/upload/bulk', data=data, content_type='multipart/form-data')
        assert response.status_code == 302
        return response

    upload(['alpha', 'beta'])
    upload(['gamma', 'delta'])

    with integration_app.app_context():
        groups = ApplicantGroup.query.filter_by(company='ALLIANZ', kind='NEW', entry_mode='BATCH')\
            .order_by(ApplicantGroup.group_number).all()
        assert [g.group_number for g in groups] == [1, 2]
        assert [len(g.applicants) for g in groups] == [2, 2]
        group_ids = [g.id for g in groups]
        first_id = groups[0].applicants[0].id
        assert all(a.applicant_group_id == groups[0].id for a in groups[0].applicants)
        assert groups[0].applicants[0].city == 'Colombo'

    company_picker = client.get('/reviews?kind=NEW&mode=BATCH').get_data(as_text=True)
    assert 'ALLIANZ · 4 pending' in company_picker and 'LOLC · 0 pending' in company_picker
    assert 'Open Group 01 Review' not in company_picker

    company_page = client.get('/reviews?kind=NEW&mode=BATCH&company=ALLIANZ').get_data(as_text=True)
    assert 'Group 01' in company_page and 'Group 02' in company_page
    assert 'Open Group 01 Review' in company_page and 'Open Group 02 Review' in company_page

    group_page = client.get(f'/review/groups/{group_ids[0]}').get_data(as_text=True)
    table = group_page.split('id="group-sheet-table"', 1)[1].split('</table>', 1)[0]
    assert table.count('<th>') == len(COMPANIES['ALLIANZ']['columns_new'])
    assert table.count('<tr>') == 3  # exact header plus two group member rows
    assert 'Colombo' in table and 'ALPHA SURNAME' in table and 'BETA SURNAME' in table
    assert 'Open full insurer review' in group_page

    individual = client.get(f'/review/{first_id}').get_data(as_text=True)
    review_table = individual.split('id="sheet-preview-table"', 1)[1].split('</table>', 1)[0]
    assert review_table.split('<tbody>', 1)[0].count('<th ') == len(COMPANIES['ALLIANZ']['columns_new'])
    assert 'data-sheet-column="CITY"' in review_table and 'value="Colombo"' in review_table
    assert 'Return to insurer preview' not in individual
    assert 'Return to Group 01' in individual

    # Individual final-check approval returns to its own group; other groups remain separate.
    import json
    from app.excel_gen import _row_for
    with integration_app.app_context():
        applicant = db.session.get(Applicant, first_id)
        row = _row_for(applicant.as_dict(), 'ALLIANZ', 'NEW', {})
    approved = client.post(f'/review/{first_id}', data={
        'sheet_row_json': json.dumps(row), 'final_check': '1', 'approve': '1'})
    assert approved.status_code == 302 and f'/review/groups/{group_ids[0]}' in approved.location
    assert 'status=PENDING' in approved.location
    remaining = client.get(f'/review/groups/{group_ids[0]}?status=PENDING').get_data(as_text=True)
    assert 'BETA SURNAME' in remaining and 'ALPHA SURNAME' not in remaining
    group_list = client.get('/reviews?kind=NEW&mode=BATCH&company=ALLIANZ').get_data(as_text=True)
    assert 'Group 01' in group_list and '1 approved · 1 pending' in group_list
    assert 'Group 02' in group_list and '0 approved · 2 pending' in group_list


def test_production_upload_uses_background_job_and_returns_to_preview(integration_app):
    from io import BytesIO
    from unittest.mock import patch
    import time
    app = integration_app
    client = app.test_client()
    app.config['TESTING'] = False
    fake = {'surname':'ASYNC SURNAME','given_names':'ASYNC APPLICANT','passport_no':'N12345678',
            'birthday':'1990-02-03','gender':'Male','salutation':'Mr.','telephone':'0771234567',
            'addr_no':'12','addr_street':'MAIN ROAD','city':'Colombo','confidence':{}}
    form = {
        'kind': 'NEW', 'company': 'ALLIANZ', 'apply_date': '2026-10-23',
        'passport': (BytesIO(b'passport data'), 'passport.pdf'),
        'police': (BytesIO(b'police data'), 'police.pdf'),
    }
    with patch('app.routes.process_documents', return_value=fake):
        response = client.post('/upload', data=form, content_type='multipart/form-data')
        assert response.status_code == 302 and response.location.startswith('/extraction/')
        page = client.get(response.location).get_data(as_text=True)
        assert 'Extracting applicant details' in page and 'job-progress' in page
        job_id = response.location.rsplit('/', 1)[-1]
        deadline = time.monotonic() + 5
        result = client.get(f'/extraction/{job_id}/status').get_json()
        while result['status'] in ('QUEUED', 'PROCESSING') and time.monotonic() < deadline:
            time.sleep(0.01)
            result = client.get(f'/extraction/{job_id}/status').get_json()
    assert result['status'] == 'COMPLETE', result
    preview = client.get(result['redirect_url']).get_data(as_text=True)
    assert 'ASYNC SURNAME' in preview and 'Colombo' in preview
