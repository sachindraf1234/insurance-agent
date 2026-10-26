from pathlib import Path

from app.batch_documents import classify_document_text


def test_mixed_batch_classifier_recognizes_passport_mrz():
    text='''PASSPORT\nPassport No: N12345678\nDate of Birth: 01/01/1990\nSex: F\n'
P<LKAFERNANDO<<CHAMIKA<<<<<<<<<<<<<<<<<<<<\nN12345678<8LKA9001012F3001019<<<<<<<<<<<<<<'''
    result=classify_document_text(text,'scan-01.jpg','NEW')
    assert result['category']=='passport_files'
    assert result['confidence']>=0.65


def test_mixed_batch_classifier_recognizes_police_clearance_report():
    text='''POLICE CLEARANCE REPORT\nThis is to certify that the applicant is residing at 12 Main Road, Colombo.\nHas not come to adverse notice.'''
    result=classify_document_text(text,'scan-02.pdf','NEW')
    assert result['category']=='police_files'


def test_mixed_batch_classifier_recognizes_applicant_phone_form():
    text='''TRAVEL INSURANCE APPLICATION FORM\nApplicant passport number: N12345678\nMobile: 0771234567\nAddress: 12 Main Road, Colombo'''
    result=classify_document_text(text,'scan-03.pdf','NEW')
    assert result['category']=='application_files'


def test_mixed_batch_classifier_recognizes_extend_policy_certificate():
    text='''ALLIANZ E-TRAVEL INSURANCE CERTIFICATE\nE-TRAVEL POLICY NO: ET123456789\nPASSPORT NO: N12345678'''
    result=classify_document_text(text,'scan-04.pdf','EXTEND')
    assert result['category']=='policy_files'


def test_mixed_batch_classifier_falls_back_visibly_when_ocr_has_no_cues():
    result=classify_document_text('', 'scan-unknown.pdf','BATCH')
    assert result['category']=='application_files'
    assert result['low_confidence'] is True


def test_combined_batch_files_are_grouped_and_filenames_retained(tmp_path,monkeypatch):
    from app.batch_documents import classify_combined_files
    files=[tmp_path/'passport.pdf',tmp_path/'phone.pdf']
    for file in files:file.write_bytes(b'test')
    texts={
      'passport.pdf':'PASSPORT\\nPassport No: N12345678\\nDate of Birth: 01/01/1990\\nSex: F',
      'phone.pdf':'INSURANCE APPLICATION FORM\\nMobile: 0771234567\\nPassport No: N12345678',
    }
    monkeypatch.setattr('app.batch_documents.extract_text',lambda path,force_ocr=True:{'text':texts[Path(path).name],'engine':'mock'})
    grouped=classify_combined_files(files,'NEW')
    assert grouped['passport_files'][0]['name']=='passport.pdf'
    assert grouped['application_files'][0]['name']=='phone.pdf'
