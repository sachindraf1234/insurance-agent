from datetime import date
from io import BytesIO
from pathlib import Path
import yaml
from flask import Flask
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def app_client(tmp_path):
    from app.models import db
    from app.city_matcher import CityMatcher
    from app.routes import bp
    app=Flask('policy-upload-test',template_folder=str(ROOT/'app'/'templates'))
    app.config.update(SECRET_KEY='test',TESTING=True,SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path/'policy.sqlite'}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,MAX_CONTENT_LENGTH=60*1024*1024,
        APP_CFG=yaml.safe_load((ROOT/'config.yaml').read_text()),BASE=ROOT,
        UPLOAD_DIR=tmp_path/'uploads',OUTPUT_DIR=tmp_path/'output',INBOX_DIR=tmp_path/'inbox',
        MAIN_SYSTEM_API_KEY='test')
    for key in ('UPLOAD_DIR','OUTPUT_DIR','INBOX_DIR'): Path(app.config[key]).mkdir(parents=True,exist_ok=True)
    db.init_app(app); app.config['CITY_MATCHER']=CityMatcher(ROOT/'data'/'cities.txt'); app.register_blueprint(bp)
    with app.app_context(): db.create_all()
    yield app
    with app.app_context(): db.drop_all()


def test_policy_number_extraction_handles_allianz_and_lolc_labels():
    from app.ocr.extractors import extract_policy_document
    allianz=extract_policy_document('E-TRAVEL POLICY NO.\nLP8028518796\nPASSPORT NO: N11416857',0.94)
    assert allianz['policy_no']=='LP8028518796'
    assert allianz['passport_no']=='N11416857'
    lolc=extract_policy_document('INSURANCE POLICY NUMBER: HODTST123456',0.90)
    assert lolc['policy_no']=='HODTST123456'


def test_extend_single_upload_passes_policy_file_to_extractor_and_saves_number(app_client):
    from unittest.mock import patch
    from app.models import db,Applicant
    client=app_client.test_client()
    fake={"passport_no":"N12345678","surname":"SAMPLE","given_names":"APPLICANT",
          "birthday":"1990-01-01","gender":"Male","salutation":"Mr.",
          "addr_no":"10","addr_street":"MAIN ROAD","city":"Dankotuwa",
          "existing_policy_no":"LP8028518796","confidence":{}}
    form={'kind':'EXTEND','company':'ALLIANZ',
          'passport':(BytesIO(b'passport-image'),'passport.png'),
          'police':(BytesIO(b'police-image'),'police.png'),
          'policy_doc':(BytesIO(b'policy-image'),'certificate.png')}
    with patch('app.routes.process_documents',return_value=fake) as process:
        response=client.post('/upload',data=form,content_type='multipart/form-data')
    assert response.status_code==302
    assert process.call_args.kwargs['policy_document_path'].is_file()
    with app_client.app_context():
        applicant=Applicant.query.one()
        assert applicant.kind=='EXTEND' and applicant.company=='ALLIANZ'
        assert applicant.existing_policy_no=='LP8028518796'


def test_extend_batch_policy_files_match_applicants_by_passport(app_client):
    from unittest.mock import patch
    from app.models import db,Applicant
    client=app_client.test_client()
    passport_ids={'passport-a.png':'N12345678','passport-b.png':'N87654321'}
    policy_ids={'certificate-a.pdf':('N12345678','LP0000000001'),
                'certificate-b.pdf':('N87654321','LP0000000002')}
    def fake_process(*args,**kwargs):
        path=kwargs.get('passport_path') or kwargs.get('police_path') or kwargs.get('policy_document_path')
        name=Path(path).name
        if kwargs.get('passport_path'):
            no=passport_ids[name]
            return {'passport_no':no,'surname':'TEST','given_names':no,'birthday':'1990-01-01',
                    'gender':'Male','salutation':'Mr.','confidence':{}}
        if kwargs.get('police_path'):
            no=passport_ids[name.replace('police-','passport-').replace('.pdf','.png')]
            return {'passport_no':no,'addr_no':'10','addr_street':'ROAD','city':'Dankotuwa',
                    'address_raw':'10 ROAD DANKOTUWA SRI LANKA','confidence':{}}
        if kwargs.get('policy_document_path'):
            no,policy=policy_ids[name]
            return {'_policy_passport_no':no,'existing_policy_no':policy,'confidence':{}}
        return {'confidence':{}}
    form={'kind':'EXTEND','company':'ALLIANZ',
          'passport_files':[(BytesIO(b'pa'),'passport-a.png'),(BytesIO(b'pb'),'passport-b.png')],
          'police_files':[(BytesIO(b'ra'),'police-a.pdf'),(BytesIO(b'rb'),'police-b.pdf')],
          'policy_files':[(BytesIO(b'cb'),'certificate-b.pdf'),(BytesIO(b'ca'),'certificate-a.pdf')]}
    with patch('app.routes.process_documents',side_effect=fake_process):
        response=client.post('/upload/bulk',data=form,content_type='multipart/form-data')
    assert response.status_code==302
    with app_client.app_context():
        rows={a.passport_no:a.existing_policy_no for a in Applicant.query.order_by(Applicant.id).all()}
        assert rows=={'N12345678':'LP0000000001','N87654321':'LP0000000002'}


def test_extend_policy_upload_controls_only_appear_on_extend_pages(app_client):
    client=app_client.test_client()
    single=client.get('/insurance/EXTEND/SINGLE').get_data(as_text=True)
    assert 'name="policy_doc"' in single
    batch=client.get('/insurance/EXTEND/BATCH').get_data(as_text=True)
    assert 'name="batch_files"' in batch and 'plus existing policy documents' in batch
    for mode in ('SINGLE','BATCH'):
        page=client.get(f'/insurance/NEW/{mode}').get_data(as_text=True)
        assert 'policy_doc' not in page and 'policy_files' not in page
        assert 'plus existing policy documents' not in page
