import logging

import mc_copy_number_requests as requests_entrypoint
from requests.request_processor import process_request_file
from utils.issue_collector import RequestEntryRecord


def _dirs(tmp_path):
    requests_dir = tmp_path / 'requests'
    return {
        'requests_dir': requests_dir,
        'ready': requests_dir / 'ready',
        'processing_temp': requests_dir / 'processing_temp',
        'processed': requests_dir / 'processed',
        'work': requests_dir / 'work',
    }


def _entry_record(counts_ok=True, errors=None):
    record = RequestEntryRecord(source_file='foo.csv', provider_name='request', row_number=2)
    record.counts_ok = counts_ok
    record.errors = errors or []
    return record


class TestProcessRequestFileLifecycle:
    def test_happy_path_moves_to_processed(self, tmp_path, make_config, monkeypatch, logger):
        dirs = _dirs(tmp_path)
        req_file = dirs['ready'] / 'req.xlsx'
        req_file.parent.mkdir(parents=True)
        req_file.write_text('placeholder')
        monkeypatch.setattr(
            'requests.request_processor._parse_request_file',
            lambda path, main_cfg, logger: ([{'row_number': 2, 'raw_data_source': 'x', 'program_code': '',
                                               'skip_aliquot_validation': False}], None),
        )
        monkeypatch.setattr(
            'requests.request_processor._process_request_entry',
            lambda entry, main_cfg, loc_cfg, logger: _entry_record(counts_ok=True),
        )
        main_cfg = make_config({}, filename='main.yaml')
        loc_cfg = make_config({}, filename='loc.yaml')

        record = process_request_file(req_file, main_cfg, loc_cfg, logger, dirs)

        assert record.processed_successfully is True
        assert record.has_errors is False
        assert not req_file.exists()
        assert (dirs['processed'] / 'req.xlsx').exists()
        assert not (dirs['work'] / 'req.xlsx').exists()

    def test_entry_failure_moves_to_work(self, tmp_path, make_config, monkeypatch, logger):
        dirs = _dirs(tmp_path)
        req_file = dirs['ready'] / 'req.xlsx'
        req_file.parent.mkdir(parents=True)
        req_file.write_text('placeholder')
        monkeypatch.setattr(
            'requests.request_processor._parse_request_file',
            lambda path, main_cfg, logger: ([{'row_number': 2, 'raw_data_source': 'x', 'program_code': '',
                                               'skip_aliquot_validation': False}], None),
        )
        monkeypatch.setattr(
            'requests.request_processor._process_request_entry',
            lambda entry, main_cfg, loc_cfg, logger: _entry_record(counts_ok=False, errors=['DB validation failed.']),
        )
        main_cfg = make_config({}, filename='main.yaml')
        loc_cfg = make_config({}, filename='loc.yaml')

        record = process_request_file(req_file, main_cfg, loc_cfg, logger, dirs)

        assert record.processed_successfully is False
        assert record.has_errors is True
        assert (dirs['work'] / 'req.xlsx').exists()
        assert not (dirs['processed'] / 'req.xlsx').exists()

    def test_parse_failure_moves_to_work_without_processing_entries(
        self, tmp_path, write_request_xlsx, make_config, monkeypatch, logger,
    ):
        dirs = _dirs(tmp_path)
        req_file = write_request_xlsx(dirs['ready'] / 'req.xlsx', [{'Program_code': 'PROG1'}])  # no Raw_data_source

        def fail_if_called(*a, **k):
            raise AssertionError('entries must not be processed when parsing fails')

        monkeypatch.setattr('requests.request_processor._process_request_entry', fail_if_called)
        main_cfg = make_config({}, filename='main.yaml')
        loc_cfg = make_config({}, filename='loc.yaml')

        record = process_request_file(req_file, main_cfg, loc_cfg, logger, dirs)

        assert record.processed_successfully is False
        assert any('Raw_data_source' in e for e in record.errors)
        assert (dirs['work'] / 'req.xlsx').exists()

    def test_zero_parsed_entries_moves_to_work(self, tmp_path, make_config, monkeypatch, logger):
        dirs = _dirs(tmp_path)
        req_file = dirs['ready'] / 'req.xlsx'
        req_file.parent.mkdir(parents=True)
        req_file.write_text('placeholder')
        monkeypatch.setattr('requests.request_processor._parse_request_file', lambda path, main_cfg, logger: ([], None))
        main_cfg = make_config({}, filename='main.yaml')
        loc_cfg = make_config({}, filename='loc.yaml')

        record = process_request_file(req_file, main_cfg, loc_cfg, logger, dirs)

        assert record.processed_successfully is False
        assert any('No entries found' in e for e in record.errors)
        assert (dirs['work'] / 'req.xlsx').exists()

    def test_claim_race_reports_error_without_moving_anything(self, tmp_path, make_config, logger):
        dirs = _dirs(tmp_path)
        req_file = dirs['ready'] / 'req.xlsx'  # never created: simulates another process already claiming it
        main_cfg = make_config({}, filename='main.yaml')
        loc_cfg = make_config({}, filename='loc.yaml')

        record = process_request_file(req_file, main_cfg, loc_cfg, logger, dirs)

        assert record.processed_successfully is False
        assert any('Failed to claim request file' in e for e in record.errors)
        assert not dirs['work'].exists()
        assert not dirs['processed'].exists()

    def test_unexpected_exception_mid_processing_moves_to_work(self, tmp_path, make_config, monkeypatch, logger):
        dirs = _dirs(tmp_path)
        req_file = dirs['ready'] / 'req.xlsx'
        req_file.parent.mkdir(parents=True)
        req_file.write_text('placeholder')
        monkeypatch.setattr(
            'requests.request_processor._parse_request_file',
            lambda path, main_cfg, logger: ([{'row_number': 2, 'raw_data_source': 'x', 'program_code': '',
                                               'skip_aliquot_validation': False}], None),
        )

        def raise_error(entry, main_cfg, loc_cfg, logger):
            raise RuntimeError('boom')

        monkeypatch.setattr('requests.request_processor._process_request_entry', raise_error)
        main_cfg = make_config({}, filename='main.yaml')
        loc_cfg = make_config({}, filename='loc.yaml')

        record = process_request_file(req_file, main_cfg, loc_cfg, logger, dirs)

        assert record.processed_successfully is False
        assert any('Unexpected error processing request file' in e for e in record.errors)
        assert (dirs['work'] / 'req.xlsx').exists()


class TestMain:
    def _stub_initialize_run(self, monkeypatch, main_cfg, loc_cfg):
        monkeypatch.setattr(requests_entrypoint, 'initialize_run', lambda *a, **k: {
            'logger': logging.getLogger('test'),
            'main_cfg': main_cfg,
            'loc_cfg': loc_cfg,
            'log_dir': '/tmp',
            'log_filename': 'requests_test.log',
            'project_root': None,
        })

    def test_no_request_files_sends_no_email(self, tmp_path, make_config, monkeypatch):
        main_cfg = make_config({}, filename='main.yaml')
        loc_cfg = make_config({'Location': {'requests_dir': str(tmp_path / 'requests')}}, filename='loc.yaml')
        self._stub_initialize_run(monkeypatch, main_cfg, loc_cfg)
        sent = []
        monkeypatch.setattr(
            requests_entrypoint, 'send_request_status_email',
            lambda *a, **k: sent.append(1),
        )

        requests_entrypoint.main()  # ready/ folder doesn't even exist -> no request files

        assert sent == []

    def test_one_email_sent_per_request_file_not_per_entry(self, tmp_path, make_config, monkeypatch):
        requests_dir = tmp_path / 'requests'
        ready_dir = requests_dir / 'ready'
        ready_dir.mkdir(parents=True)
        (ready_dir / 'req1.xlsx').write_text('placeholder')
        (ready_dir / 'req2.xlsx').write_text('placeholder')
        main_cfg = make_config({}, filename='main.yaml')
        loc_cfg = make_config({'Location': {'requests_dir': str(requests_dir)}}, filename='loc.yaml')
        self._stub_initialize_run(monkeypatch, main_cfg, loc_cfg)

        def fake_process_request_file(file_path, main_cfg_arg, loc_cfg_arg, logger, dirs):
            from utils.issue_collector import RequestRecord
            record = RequestRecord(str(file_path))
            record.entries = [_entry_record(), _entry_record()]
            record.processed_successfully = True
            return record

        monkeypatch.setattr(requests_entrypoint, 'process_request_file', fake_process_request_file)
        sent = []
        monkeypatch.setattr(
            requests_entrypoint, 'send_request_status_email',
            lambda logger, request_record, *a, **k: sent.append(request_record),
        )

        requests_entrypoint.main()

        assert len(sent) == 2  # one email per request file, not one per entry (4 entries total)
