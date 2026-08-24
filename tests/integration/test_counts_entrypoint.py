import logging
import sys

import pytest

import mc_copy_number_counts as counts_module


def _stub_initialize_run(monkeypatch, main_cfg, loc_cfg):
    monkeypatch.setattr(counts_module, 'initialize_run', lambda *a, **k: {
        'logger': logging.getLogger('test'),
        'main_cfg': main_cfg,
        'loc_cfg': loc_cfg,
        'log_dir': '/tmp',
        'log_filename': 'counts_test.log',
    })


def _stub_send_status_email(monkeypatch):
    sent = {}

    def fake(logger, file_records, log_filename, main_cfg, subject_prefix=None, process_label=None):
        sent['file_records'] = file_records

    monkeypatch.setattr(counts_module, 'send_status_email', fake)
    return sent


def _set_argv(monkeypatch, *args):
    monkeypatch.setattr(sys, 'argv', ['mc_copy_number_counts.py', *args])


class TestArgumentParsing:
    def test_input_file_and_input_dir_are_mutually_exclusive(self, monkeypatch):
        _set_argv(monkeypatch, '--input_file', 'a.csv', '--input_dir', 'somedir')

        with pytest.raises(SystemExit):
            counts_module.main()

    def test_missing_required_argument_exits(self, monkeypatch):
        _set_argv(monkeypatch)

        with pytest.raises(SystemExit):
            counts_module.main()


class TestInputFileValidation:
    def test_nonexistent_input_file_reports_error(self, tmp_path, make_config, monkeypatch):
        main_cfg = make_config({}, filename='main.yaml')
        loc_cfg = make_config({}, filename='loc.yaml')
        _stub_initialize_run(monkeypatch, main_cfg, loc_cfg)
        sent = _stub_send_status_email(monkeypatch)
        missing = tmp_path / 'does_not_exist.csv'
        _set_argv(monkeypatch, '--input_file', str(missing))

        counts_module.main()

        record = sent['file_records'][0]
        assert record.counts_ran is False
        assert any('does not exist' in e for e in record.errors)

    def test_input_file_pointing_to_directory_reports_error(self, tmp_path, make_config, monkeypatch):
        main_cfg = make_config({}, filename='main.yaml')
        loc_cfg = make_config({}, filename='loc.yaml')
        _stub_initialize_run(monkeypatch, main_cfg, loc_cfg)
        sent = _stub_send_status_email(monkeypatch)
        a_dir = tmp_path / 'a_directory'
        a_dir.mkdir()
        _set_argv(monkeypatch, '--input_file', str(a_dir))

        counts_module.main()

        record = sent['file_records'][0]
        assert record.counts_ran is False
        assert any('points to a directory' in e for e in record.errors)


class TestInputDirValidation:
    def test_zero_csv_files_reports_error(self, tmp_path, make_config, monkeypatch):
        main_cfg = make_config({}, filename='main.yaml')
        loc_cfg = make_config({}, filename='loc.yaml')
        _stub_initialize_run(monkeypatch, main_cfg, loc_cfg)
        sent = _stub_send_status_email(monkeypatch)
        empty_dir = tmp_path / 'empty'
        empty_dir.mkdir()
        _set_argv(monkeypatch, '--input_dir', str(empty_dir))

        counts_module.main()

        record = sent['file_records'][0]
        assert record.counts_ran is False
        assert any('No CSV files found' in e for e in record.errors)

    def test_multiple_csv_files_reports_error(self, tmp_path, make_config, monkeypatch):
        main_cfg = make_config({}, filename='main.yaml')
        loc_cfg = make_config({}, filename='loc.yaml')
        _stub_initialize_run(monkeypatch, main_cfg, loc_cfg)
        sent = _stub_send_status_email(monkeypatch)
        multi_dir = tmp_path / 'multi'
        multi_dir.mkdir()
        (multi_dir / 'a.csv').write_text('x')
        (multi_dir / 'b.csv').write_text('x')
        _set_argv(monkeypatch, '--input_dir', str(multi_dir))

        counts_module.main()

        record = sent['file_records'][0]
        assert record.counts_ran is False
        assert any('but found 2' in e for e in record.errors)

    def test_exactly_one_csv_file_delegates_to_process_counts_input(
        self, tmp_path, make_config, schema_fields, write_aligned_csv, monkeypatch,
    ):
        studies_dir = tmp_path / 'studies'
        main_cfg = make_config({
            'Counts': {'processed_data_dir': 'processed_data', 'output_path_depth': 1},
            'Alligned_file_schema': {'fields': schema_fields},
            'Csv_output': {'enable_utf8_bom': True},
            'Alignment': {'validate_aliquots_against_db': True, 'allow_multiple_programs': False},
        }, filename='main.yaml')
        loc_cfg = make_config({'Location': {'mitCopyN_studies_dir': str(studies_dir)}}, filename='loc.yaml')
        _stub_initialize_run(monkeypatch, main_cfg, loc_cfg)
        sent = _stub_send_status_email(monkeypatch)
        monkeypatch.setattr(
            'alignment.aliquot_db_validator.validate_aliquots',
            lambda aliquots, main_cfg, logger, allow_multiple_programs=False: (True, {'PROG1': aliquots}, []),
        )
        input_dir = studies_dir / 'raw_data' / 'run1'
        write_aligned_csv(input_dir / 'foo.csv', ['A1', 'A2'])
        _set_argv(monkeypatch, '--input_dir', str(input_dir))

        counts_module.main()

        record = sent['file_records'][0]
        assert record.counts_ok is True
        assert record.program_code == 'PROG1'
