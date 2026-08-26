import pytest

from utils.common import (
    _bom_note, _split_emails, build_subject_prefix, send_request_status_email, send_status_email,
)
from utils.issue_collector import FileRecord, RequestEntryRecord, RequestRecord

EMAIL_CFG = {
    'Email': {
        'send_emails': True,
        'email_subject_prefix': 'MC Copy Number',
    }
}


@pytest.fixture(autouse=True)
def _email_env(monkeypatch):
    """Recipient addresses now come from env vars (see utils.common._resolve_email_recipients),
    not main_config.yaml — set deterministic values for every test in this module regardless of
    what a real local .env might contain."""
    monkeypatch.setenv('MC_EMAIL_FROM', 'sender@example.org')
    monkeypatch.setenv('MC_EMAIL_TO', 'recipient@example.org')
    monkeypatch.delenv('MC_EMAIL_ADDITIONAL_TO', raising=False)

# Both pipeline_status.html and request_status.html join per-file/entry sections with this exact
# separator (see templates); splitting a rendered body on it isolates one section from the next.
SECTION_SEPARATOR = '----------------------------------------------------'


def _sections(body: str) -> list[str]:
    """Split a rendered status-email body into [preamble, section_1, section_2, ...]."""
    return body.split(SECTION_SEPARATOR)


class TestBuildSubjectPrefix:
    def test_environment_name_omitted_when_unset(self, make_config):
        main_cfg = make_config(EMAIL_CFG, filename='main.yaml')
        loc_cfg = make_config({}, filename='location.yaml')
        assert build_subject_prefix(main_cfg, loc_cfg, 'Alignment/Counts') == 'MC Copy Number - Alignment/Counts'

    def test_environment_name_prefixed_when_set(self, make_config):
        main_cfg = make_config(EMAIL_CFG, filename='main.yaml')
        loc_cfg = make_config({'Location': {'environment_name': 'production'}}, filename='location.yaml')
        assert (
            build_subject_prefix(main_cfg, loc_cfg, 'Alignment/Counts')
            == '[production] MC Copy Number - Alignment/Counts'
        )


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

    def test_pipeline_record_omits_request_only_fields(self, make_config, captured_emails, logger):
        """FileRecord-based feeders must leave row_number/raw_data_source undefined (not None/blank) —
        file_status.html uses `is defined` checks to tell a pipeline record from a request entry."""
        main_cfg = make_config(EMAIL_CFG)
        send_status_email(logger, [_successful_record()], '/logs/run.log', main_cfg)
        body = captured_emails[0]['body']
        assert 'Data entry row' not in body
        assert 'Request Raw data source' not in body

    def test_multiple_file_sections_are_separated_and_ordered(self, make_config, captured_emails, logger):
        """Two files with distinct content must render into two cleanly separated sections — each
        file's own errors/warnings/program data must not leak into the other's section."""
        failed = FileRecord(source_file='/data/first.xlsx', provider_name='NairLab')
        failed.alignment_ok = False
        failed.errors.append('First file alignment error.')

        multi_program = FileRecord(source_file='/data/second.xlsx', provider_name='OtherLab')
        multi_program.alignment_output = '/data/raw_data/second/second.csv'
        multi_program.alignment_ok = True
        multi_program.counts_ok = True
        multi_program.program_groups = {'PROG_A': ['A1', 'A2'], 'PROG_B': ['B1']}
        multi_program.counts_outputs = [('PROG_A', '/out/PROG_A/second.csv'), ('PROG_B', '/out/PROG_B/second.csv')]
        multi_program.warnings.append('Second file warning.')

        main_cfg = make_config(EMAIL_CFG)
        send_status_email(logger, [failed, multi_program], '/logs/run.log', main_cfg)
        body = captured_emails[0]['body']

        preamble, section_1, section_2 = _sections(body)
        assert 'first.xlsx' not in preamble and 'second.xlsx' not in preamble

        assert 'first.xlsx' in section_1
        assert 'First file alignment error.' in section_1
        assert 'second.xlsx' not in section_1
        assert 'Second file warning.' not in section_1
        assert 'PROG_A' not in section_1  # program_groups only renders when alignment_ok

        assert 'second.xlsx' in section_2
        assert 'Second file warning.' in section_2
        assert 'PROG_A' in section_2 and 'PROG_B' in section_2
        assert 'first.xlsx' not in section_2
        assert 'First file alignment error.' not in section_2

        assert body.index('first.xlsx') < body.index('second.xlsx')


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

    def test_entry_body_includes_request_specific_fields(self, make_config, captured_emails, logger):
        """The request-specific keys layered onto _build_entry_feeder() must actually reach the
        rendered body (row_number/raw_data_source come from the entry set up in _entry())."""
        record = self._request_record(processed_successfully=True, entries=[self._entry()])
        main_cfg = make_config(EMAIL_CFG)
        send_request_status_email(logger, record, '/logs/run.log', main_cfg)
        body = captured_emails[0]['body']
        assert '<b>Data entry row:</b> 2' in body
        assert '<b>Request Raw data source:</b> foo/foo.csv' in body

    def test_multiple_entry_sections_are_separated_and_ordered(self, make_config, captured_emails, logger):
        """Two request entries with distinct content must render into two cleanly separated
        sections — each entry's own row number/errors/warnings must not leak into the other's."""
        entry_1 = RequestEntryRecord(
            source_file='/data/raw_data/one/one.csv', provider_name='request', row_number=1,
            raw_data_source='one/one.csv',
        )
        entry_1.errors.append('Entry one error.')

        entry_2 = RequestEntryRecord(
            source_file='/data/raw_data/two/two.csv', provider_name='request', row_number=2,
            raw_data_source='two/two.csv', program_code_override='PROG_X',
        )
        entry_2.alignment_ok = True  # program_code_override only renders inside the alignment_ok branch
        entry_2.warnings.append('Entry two warning.')

        record = self._request_record(entries=[entry_1, entry_2])
        main_cfg = make_config(EMAIL_CFG)
        send_request_status_email(logger, record, '/logs/run.log', main_cfg)
        body = captured_emails[0]['body']

        preamble, section_1, section_2 = _sections(body)
        assert 'one.csv' not in preamble and 'two.csv' not in preamble

        assert 'Data entry row:</b> 1' in section_1
        assert 'one/one.csv' in section_1
        assert 'Entry one error.' in section_1
        assert 'two/two.csv' not in section_1
        assert 'Entry two warning.' not in section_1
        assert 'PROG_X' not in section_1

        assert 'Data entry row:</b> 2' in section_2
        assert 'two/two.csv' in section_2
        assert 'Entry two warning.' in section_2
        assert 'PROG_X' in section_2
        assert 'one/one.csv' not in section_2
        assert 'Entry one error.' not in section_2

        assert body.index('one/one.csv') < body.index('two/two.csv')


class TestSplitEmails:
    def test_none_returns_empty_list(self):
        assert _split_emails(None) == []

    def test_empty_string_returns_empty_list(self):
        assert _split_emails('') == []

    def test_single_address(self):
        assert _split_emails('a@example.org') == ['a@example.org']

    def test_multiple_addresses_split_and_trimmed(self):
        assert _split_emails('a@example.org, b@example.org ,c@example.org') == [
            'a@example.org', 'b@example.org', 'c@example.org',
        ]

    def test_stray_commas_produce_no_blank_entries(self):
        assert _split_emails('a@example.org,,  ,b@example.org') == ['a@example.org', 'b@example.org']


class TestAdditionalEmails:
    def test_excluded_by_default(self, make_config, captured_emails, logger, monkeypatch):
        monkeypatch.setenv('MC_EMAIL_ADDITIONAL_TO', 'extra@example.org')
        main_cfg = make_config(EMAIL_CFG)  # include_additional_emails unset -> defaults to False

        send_status_email(logger, [], '/logs/run.log', main_cfg)

        assert captured_emails[0]['to'] == ['recipient@example.org']

    def test_included_when_enabled_and_results_produced(self, make_config, captured_emails, logger, monkeypatch):
        monkeypatch.setenv('MC_EMAIL_ADDITIONAL_TO', 'extra@example.org')
        cfg_data = {'Email': {**EMAIL_CFG['Email'], 'include_additional_emails': True}}
        main_cfg = make_config(cfg_data)

        send_status_email(logger, [_successful_record()], '/logs/run.log', main_cfg)

        assert captured_emails[0]['to'] == ['recipient@example.org', 'extra@example.org']

    def test_excluded_when_enabled_but_no_results_produced(self, make_config, captured_emails, logger, monkeypatch):
        monkeypatch.setenv('MC_EMAIL_ADDITIONAL_TO', 'extra@example.org')
        cfg_data = {'Email': {**EMAIL_CFG['Email'], 'include_additional_emails': True}}
        main_cfg = make_config(cfg_data)

        send_status_email(logger, [], '/logs/run.log', main_cfg)

        assert captured_emails[0]['to'] == ['recipient@example.org']

    def test_included_when_enabled_even_if_all_files_errored(
        self, make_config, captured_emails, logger, monkeypatch,
    ):
        """A file was attempted but failed — that's still a 'result' (something to be aware of),
        as opposed to there being nothing to process at all. Additional recipients must still see it."""
        monkeypatch.setenv('MC_EMAIL_ADDITIONAL_TO', 'extra@example.org')
        cfg_data = {'Email': {**EMAIL_CFG['Email'], 'include_additional_emails': True}}
        main_cfg = make_config(cfg_data)
        failed_record = FileRecord(source_file='/data/foo.xlsx', provider_name='NairLab')
        failed_record.alignment_ok = False
        failed_record.errors.append('Aliquot DB validation failed.')

        send_status_email(logger, [failed_record], '/logs/run.log', main_cfg)

        assert captured_emails[0]['to'] == ['recipient@example.org', 'extra@example.org']

    def test_enabled_but_env_var_unset_adds_nothing(self, make_config, captured_emails, logger):
        cfg_data = {'Email': {**EMAIL_CFG['Email'], 'include_additional_emails': True}}
        main_cfg = make_config(cfg_data)

        send_status_email(logger, [], '/logs/run.log', main_cfg)

        assert captured_emails[0]['to'] == ['recipient@example.org']

    def test_multiple_additional_recipients_are_included(self, make_config, captured_emails, logger, monkeypatch):
        monkeypatch.setenv('MC_EMAIL_ADDITIONAL_TO', 'extra1@example.org, extra2@example.org')
        cfg_data = {'Email': {**EMAIL_CFG['Email'], 'include_additional_emails': True}}
        main_cfg = make_config(cfg_data)

        send_status_email(logger, [_successful_record()], '/logs/run.log', main_cfg)

        assert captured_emails[0]['to'] == [
            'recipient@example.org', 'extra1@example.org', 'extra2@example.org',
        ]

    def test_applies_to_request_status_email_too(self, make_config, captured_emails, logger, monkeypatch):
        monkeypatch.setenv('MC_EMAIL_ADDITIONAL_TO', 'extra@example.org')
        cfg_data = {'Email': {**EMAIL_CFG['Email'], 'include_additional_emails': True}}
        main_cfg = make_config(cfg_data)
        record = RequestRecord('/requests/ready/foo.xlsx')
        record.entries = [RequestEntryRecord(
            source_file='/data/raw_data/foo/foo.csv', provider_name='request', row_number=2,
            raw_data_source='foo/foo.csv',
        )]

        send_request_status_email(logger, record, '/logs/run.log', main_cfg)

        assert captured_emails[0]['to'] == ['recipient@example.org', 'extra@example.org']

    def test_excluded_from_request_status_email_when_no_entries(
        self, make_config, captured_emails, logger, monkeypatch,
    ):
        monkeypatch.setenv('MC_EMAIL_ADDITIONAL_TO', 'extra@example.org')
        cfg_data = {'Email': {**EMAIL_CFG['Email'], 'include_additional_emails': True}}
        main_cfg = make_config(cfg_data)
        record = RequestRecord('/requests/ready/foo.xlsx')

        send_request_status_email(logger, record, '/logs/run.log', main_cfg)

        assert captured_emails[0]['to'] == ['recipient@example.org']
