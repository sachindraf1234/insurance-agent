from email.message import EmailMessage
def _raw_message(from_address, subject, body='test body'):
    message=EmailMessage()
    message['From']=from_address
    message['To']='agency@yahoo.com'
    message['Subject']=subject
    message['Message-ID']=f'<{subject.replace(" ", "-")}@test>'
    message.set_content(body)
    return message.as_bytes()


def test_inbox_fetches_full_bodies_only_from_exact_allowlisted_senders(tmp_path, monkeypatch):
    from unittest.mock import patch
    from app.email_agent.smtp_imap import fetch_unseen

    monkeypatch.setenv('MAIL_ADDRESS','agency@yahoo.com')
    monkeypatch.setenv('MAIL_PASSWORD','test-only')
    allowlisted='allianz@example.com'
    unrelated='allianz@example.com.spam.test'
    calls={'search':[],'fetch':[],'store':[]}

    class FakeIMAP:
        def __init__(self,host,port): calls['connect']=(host,port)
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def login(self,address,password): calls['login']=(address,password)
        def select(self,folder): calls['folder']=folder
        def search(self,charset,*criteria):
            calls['search'].append(criteria)
            # Simulate substring-style IMAP results including one false positive.
            return 'OK',[b'1 2']
        def fetch(self,num,query):
            calls['fetch'].append((num,query))
            if num==b'1' and 'HEADER.FIELDS' in query:
                return 'OK',[(b'1 header',b'From: Allianz <allianz@example.com>\r\nTo: agency@yahoo.com\r\nSubject: Policy reply\r\n\r\n'),b')']
            if num==b'2' and 'HEADER.FIELDS' in query:
                return 'OK',[(b'2 header',b'From: Promo <allianz@example.com.spam.test>\r\nTo: agency@yahoo.com\r\nSubject: Spam\r\n\r\n'),b')']
            if num==b'1' and 'BODY.PEEK[]' in query:
                return 'OK',[(b'1 full',_raw_message('Allianz <allianz@example.com>','Policy reply','Policy attachment received.')),b')']
            raise AssertionError(f'Unapproved body fetch attempted: {num!r} {query!r}')
        def store(self,num,operation,flag): calls['store'].append((num,operation,flag))

    cfg={'imap_host':'imap.example.test','imap_port':993,'reply_from':[allowlisted]}
    with patch('app.email_agent.smtp_imap.imaplib.IMAP4_SSL',FakeIMAP):
        messages=fetch_unseen(cfg,tmp_path)
    assert len(messages)==1 and messages[0]['subject']=='Policy reply'
    assert messages[0]['from']=='Allianz <allianz@example.com>'
    assert [criteria[-1] for criteria in calls['search']]==[f'"{allowlisted}"']
    assert [(num,query) for num,query in calls['fetch'] if 'BODY.PEEK[]' in query]==[(b'1','(BODY.PEEK[])')]
    assert calls['store']==[(b'1','+FLAGS','\\Seen')]
    assert calls['connect']==('imap.example.test',993)


def test_empty_inbox_allowlist_does_not_connect_or_search(tmp_path, monkeypatch):
    from unittest.mock import patch
    from app.email_agent.smtp_imap import fetch_unseen
    monkeypatch.setenv('MAIL_ADDRESS','agency@yahoo.com')
    monkeypatch.setenv('MAIL_PASSWORD','test-only')
    with patch('app.email_agent.smtp_imap.imaplib.IMAP4_SSL') as imap:
        assert fetch_unseen({'imap_host':'imap.example.test','imap_port':993,'reply_from':[]},tmp_path)==[]
    imap.assert_not_called()


def test_insurer_recipients_and_reply_allowlist_are_exactly_configured():
    import yaml
    from pathlib import Path
    cfg=yaml.safe_load((Path(__file__).resolve().parents[1]/'config.yaml').read_text())
    companies=cfg['companies']
    assert companies['ALLIANZ']['email_to']=='chamikafernando456@gmail.com'
    assert companies['ALLIANZ']['email_cc']=='chamikaf@allianz.lk'
    assert 'warunic@allianz.lk' not in companies['ALLIANZ']['email_cc']
    assert companies['LOLC']['email_to']=='suthsil73@gmail.com'
    assert companies['LOLC']['email_cc']==''
    assert set(cfg['email']['reply_from'])=={
        'chamikafernando456@gmail.com','chamikaf@allianz.lk','warunic@allianz.lk','suthsil73@gmail.com'}
