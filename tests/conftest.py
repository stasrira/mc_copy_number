import logging

import pytest
import yaml

from utils.configuration import ConfigData


@pytest.fixture
def make_config(tmp_path):
    """Factory fixture: write *data* as YAML and return it loaded as a ConfigData."""

    def _make(data: dict, filename: str = 'config.yaml') -> ConfigData:
        path = tmp_path / filename
        path.write_text(yaml.safe_dump(data))
        return ConfigData(path)

    return _make


@pytest.fixture
def captured_emails(monkeypatch):
    """Monkeypatch utils.send_email.send_email to capture calls instead of sending."""
    calls = []

    def fake_send_email(emails_to, subject, message, email_from=None, **kwargs):
        calls.append({'to': emails_to, 'subject': subject, 'body': message, 'from': email_from})

    monkeypatch.setattr('utils.send_email.send_email', fake_send_email)
    return calls


@pytest.fixture
def logger():
    return logging.getLogger('test')
