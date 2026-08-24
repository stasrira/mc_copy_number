from utils.common import _bom_note, send_request_status_email, send_status_email
from utils.issue_collector import FileRecord, RequestEntryRecord, RequestRecord

EMAIL_CFG = {
    'Email': {
        'send_emails': True,
        'default_from_email': 'sender@example.org',
        'sent_to_emails': ['recipient@example.org'],
        'email_subject_prefix': 'MC Copy Number',
    }
}


def _successful_record():
    record = FileRecord(source_file='/data/foo.xlsx', provider_name='NairLab')
    record.alignment_output = '/data/raw_data/foo/foo.csv'
    record.alignment_ok = True
    record.counts_ok = True
    record.counts_output = '/data/processed_data/PROG/foo.csv'
    return record


class TestBomNote:
    def test_no_bom_paths_returns_none(self):
        record = _successful_record()
        assert _bom_note(record) is None

    def test_singular_phrasing_for_one_path(self):
        record = _successful_record()
        record.bom_applied_paths = ['/data/processed_data/PROG/foo.csv']
        record.bom_example = 'Naïve-A4'
        note = _bom_note(record)
        assert 'The output file marked with "*"' in note
        assert 'Naïve-A4' in note

    def test_plural_phrasing_for_multiple_paths(self):
        record = _successful_record()
        record.bom_applied_paths = ['/a.csv', '/b.csv']
        record.bom_example = 'Naïve-A4'
        note = _bom_note(record)
        assert 'The 2 output files marked with "*"' in note


class TestSendStatusEmail:
    def test_zero_files_subject(self, make_config, captured_emails, logger):
        main_cfg = make_config(EMAIL_CFG)
        send_status_email(logger, [], '/logs/run.log', main_cfg)
        assert len(captured_emails) == 1
        assert 'no files processed' in captured_emails[0]['subject']

    def test_all_successful_subject(self, make_config, captured_emails, logger):
        main_cfg = make_config(EMAIL_CFG)
        send_status_email(logger, [_successful_record()], '/logs/run.log', main_cfg)
        subject = captured_emails[0]['subject']
        assert 'ERRORS' not in subject
        assert 'Warnings' not in subject
        assert '1 file(s) processed successfully' in subject

    def test_warnings_present_subject(self, make_config, captured_emails, logger):
        record = _successful_record()
        record.warnings.append('Header mismatch for field X.')
        main_cfg = make_config(EMAIL_CFG)
        send_status_email(logger, [record], '/logs/run.log', main_cfg)
        assert 'Warnings present (1)' in captured_emails[0]['subject']

    def test_errors_present_subject(self, make_config, captured_emails, logger):
        record = _successful_record()
        record.alignment_ok = False
        record.errors.append('Aliquot DB validation failed.')
        main_cfg = make_config(EMAIL_CFG)
        send_status_email(logger, [record], '/logs/run.log', main_cfg)
        assert 'ERRORS PRESENT (1)' in captured_emails[0]['subject']

    def test_send_emails_disabled_does_not_call_send_email(self, make_config, captured_emails, logger):
        cfg_data = {'Email': {**EMAIL_CFG['Email'], 'send_emails': False}}
        main_cfg = make_config(cfg_data)
        send_status_email(logger, [_successful_record()], '/logs/run.log', main_cfg)
        assert captured_emails == []

    def test_send_emails_enabled_sends_to_configured_recipients(self, make_config, captured_emails, logger):
        main_cfg = make_config(EMAIL_CFG)
        send_status_email(logger, [_successful_record()], '/logs/run.log', main_cfg)
        assert captured_emails[0]['to'] == ['recipient@example.org']
        assert captured_emails[0]['from'] == 'sender@example.org'

    def test_subject_prefix_override_is_used(self, make_config, captured_emails, logger):
        main_cfg = make_config(EMAIL_CFG)
        send_status_email(logger, [], '/logs/run.log', main_cfg, subject_prefix='Custom Prefix')
        assert captured_emails[0]['subject'].startswith('Custom Prefix')

    def test_empty_default_record_renders_without_crashing(self, make_config, captured_emails, logger):
        main_cfg = make_config(EMAIL_CFG)
        blank_record = FileRecord(source_file='N/A', provider_name='standalone')
        send_status_email(logger, [blank_record], '/logs/run.log', main_cfg)
        assert len(captured_emails) == 1
        assert 'N/A' in captured_emails[0]['body']


class TestSendRequestStatusEmail:
    def _request_record(self, **flags):
        record = RequestRecord('/requests/ready/foo.xlsx')
        record.processed_successfully = flags.get('processed_successfully', False)
        if 'entries' in flags:
            record.entries = flags['entries']
        return record

    def _entry(self, errors=None, warnings=None):
        entry = RequestEntryRecord(
            source_file='/data/raw_data/foo/foo.csv', provider_name='request', row_number=2,
            raw_data_source='foo/foo.csv',
        )
        entry.errors = errors or []
        entry.warnings = warnings or []
        return entry

    def test_processed_successfully_subject(self, make_config, captured_emails, logger):
        record = self._request_record(processed_successfully=True, entries=[self._entry()])
        main_cfg = make_config(EMAIL_CFG)
        send_request_status_email(logger, record, '/logs/run.log', main_cfg)
        assert 'Request completed successfully' in captured_emails[0]['subject']

    def test_entry_errors_produce_error_subject(self, make_config, captured_emails, logger):
        record = self._request_record(entries=[self._entry(errors=['DB validation failed.'])])
        main_cfg = make_config(EMAIL_CFG)
        send_request_status_email(logger, record, '/logs/run.log', main_cfg)
        assert 'Request ERRORS' in captured_emails[0]['subject']

    def test_entry_warnings_without_errors_produce_warning_subject(self, make_config, captured_emails, logger):
        record = self._request_record(entries=[self._entry(warnings=['Sheet fallback used.'])])
        main_cfg = make_config(EMAIL_CFG)
        send_request_status_email(logger, record, '/logs/run.log', main_cfg)
        assert 'Request warnings' in captured_emails[0]['subject']

    def test_no_entries_subject(self, make_config, captured_emails, logger):
        record = self._request_record(entries=[])
        main_cfg = make_config(EMAIL_CFG)
        send_request_status_email(logger, record, '/logs/run.log', main_cfg)
        assert 'Request no entries' in captured_emails[0]['subject']

    def test_request_level_error_takes_precedence(self, make_config, captured_emails, logger):
        record = self._request_record(processed_successfully=True, entries=[self._entry()])
        record.errors.append('Failed to claim request file.')
        main_cfg = make_config(EMAIL_CFG)
        send_request_status_email(logger, record, '/logs/run.log', main_cfg)
        assert 'Request ERRORS' in captured_emails[0]['subject']
