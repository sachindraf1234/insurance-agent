import pytest
from unittest.mock import patch


@pytest.mark.parametrize('to,cc', [
    ('warunic@allianz.lk', ''),
    ('chamikafernando456@gmail.com', 'chamikaf@allianz.lk, warunic@allianz.lk'),
])
def test_receive_only_address_is_blocked_from_all_outbound_recipients(to,cc,monkeypatch):
    from app.email_agent.smtp_imap import send_email
    monkeypatch.setenv('MAIL_ADDRESS','agency@yahoo.com')
    monkeypatch.setenv('MAIL_PASSWORD','test-only')
    cfg={'smtp_host':'smtp.example.test','smtp_port':587}
    with patch('app.email_agent.smtp_imap.smtplib.SMTP') as smtp:
        with pytest.raises(RuntimeError,match='receive-only'):
            send_email(cfg,to,'Test subject','Test body',[],cc=cc)
    smtp.assert_not_called()
