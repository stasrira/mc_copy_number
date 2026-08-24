import logging

import openpyxl
import pytest

import mc_copy_number_alignment as alignment_module
from mc_copy_number_alignment import _discover_providers, run_alignment
from utils.issue_collector import FileRecord

EXTRACTION_CFG = {
    'sheet_name': None,
    'header_row': 1,
    'data_start_row': 2,
    'data_end_strategy': 'first_empty',
    'data_end_anchor_field': 'aliquot_id',
    'columns': {
        'aliquot_id': {'source_header': 'Biospecimen ID', 'column_index': 1},
        'mtdna_mean': {'source_header': 'mean', 'column_index': 2},
        'mtdna_se': {'source_header': 'SE', 'column_index': 3},
    },
}


def _write_xlsx(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    wb.save(path)


class TestDiscoverProviders:
    def test_finds_valid_provider_configs(self, tmp_path, add_provider_config, logger):
        add_provider_config(tmp_path, 'ProviderA', extraction_cfg=EXTRACTION_CFG)
        add_provider_config(tmp_path, 'ProviderB', extraction_cfg=EXTRACTION_CFG)

        providers = _discover_providers(tmp_path / 'configs' / 'providers', logger)

        names = sorted(p.get_value('provider/name') for p in providers)
        assert names == ['ProviderA', 'ProviderB']

    def test_skips_folder_without_provider_config_file(self, tmp_path, add_provider_config, logger):
        add_provider_config(tmp_path, 'ProviderA', extraction_cfg=EXTRACTION_CFG)
        (tmp_path / 'configs' / 'providers' / 'NotAProvider').mkdir(parents=True)

        providers = _discover_providers(tmp_path / 'configs' / 'providers', logger)

        assert len(providers) == 1

    def test_warns_on_malformed_yaml(self, tmp_path, logger, caplog):
        provider_dir = tmp_path / 'configs' / 'providers' / 'Bad'
        provider_dir.mkdir(parents=True)
        (provider_dir / 'provider_config.yaml').write_text('key: [unclosed')

        with caplog.at_level('WARNING'):
            providers = _discover_providers(tmp_path / 'configs' / 'providers', logger)

        assert providers == []
        assert any('Failed to load provider config' in rec.message for rec in caplog.records)

    def test_missing_providers_dir_returns_empty_with_warning(self, tmp_path, logger, caplog):
        with caplog.at_level('WARNING'):
            providers = _discover_providers(tmp_path / 'does_not_exist', logger)

        assert providers == []
        assert any('not found' in rec.message for rec in caplog.records)


class TestRunAlignment:
    def _patch_root(self, monkeypatch, root):
        monkeypatch.setattr(alignment_module, 'get_project_root', lambda: root)

    def test_missing_ready_folder_warns_and_skips(
        self, tmp_path, build_project_root, add_provider_config, monkeypatch, logger, caplog,
    ):
        root, studies_dir = build_project_root()
        add_provider_config(root, 'ProviderA', extraction_cfg=EXTRACTION_CFG)
        # runFolders/ProviderA/ready is never created
        self._patch_root(monkeypatch, root)

        with caplog.at_level('WARNING'):
            records = run_alignment(logger)

        assert records == []
        assert any('Ready folder not found' in rec.message for rec in caplog.records)

    def test_empty_ready_folder_logs_info_and_skips(
        self, tmp_path, build_project_root, add_provider_config, monkeypatch, logger,
    ):
        root, studies_dir = build_project_root()
        add_provider_config(root, 'ProviderA', extraction_cfg=EXTRACTION_CFG)
        (studies_dir / 'runFolders' / 'ProviderA' / 'ready').mkdir(parents=True)
        self._patch_root(monkeypatch, root)

        records = run_alignment(logger)

        assert records == []

    def test_if_only_lock_file_present_treat_it_as_empty(
        self, tmp_path, build_project_root, add_provider_config, monkeypatch, logger, caplog,
    ):
        root, studies_dir = build_project_root()
        add_provider_config(root, 'ProviderA', extraction_cfg=EXTRACTION_CFG)
        ready_dir = studies_dir / 'runFolders' / 'ProviderA' / 'ready'
        ready_dir.mkdir(parents=True)
        (ready_dir / '~$foo.xlsx').write_text('excel lock file placeholder')
        self._patch_root(monkeypatch, root)

        with caplog.at_level('WARNING'):
            records = run_alignment(logger)

        assert records == []
        assert not any('No files matching' in rec.message for rec in caplog.records)

    def test_non_matching_file_pattern_warns(
        self, tmp_path, build_project_root, add_provider_config, monkeypatch, logger, caplog,
    ):
        root, studies_dir = build_project_root()
        add_provider_config(root, 'ProviderA', extraction_cfg=EXTRACTION_CFG)
        ready_dir = studies_dir / 'runFolders' / 'ProviderA' / 'ready'
        ready_dir.mkdir(parents=True)
        (ready_dir / 'notes.txt').write_text('not an excel file')
        self._patch_root(monkeypatch, root)

        with caplog.at_level('WARNING'):
            records = run_alignment(logger)

        assert records == []
        assert any('No files matching' in rec.message for rec in caplog.records)

    def test_processes_valid_file_end_to_end_without_db_validation(
        self, tmp_path, build_project_root, add_provider_config, monkeypatch, logger,
    ):
        root, studies_dir = build_project_root()
        add_provider_config(root, 'ProviderA', extraction_cfg=EXTRACTION_CFG)
        ready_dir = studies_dir / 'runFolders' / 'ProviderA' / 'ready'
        _write_xlsx(ready_dir / 'foo.xlsx', [
            ('Biospecimen ID', 'mean', 'SE'),
            ('A1', 1.23, 0.05),
        ])
        self._patch_root(monkeypatch, root)

        records = run_alignment(logger)

        assert len(records) == 1
        record = records[0]
        assert record.alignment_ok is True
        assert record.provider_name == 'ProviderA'
        assert record.aliquots == ['A1']
        assert record.db_validation_skipped is True
        assert (studies_dir / 'runFolders' / 'ProviderA' / 'processed' / 'foo.xlsx').exists()

    def test_multiple_providers_processed_independently(
        self, tmp_path, build_project_root, add_provider_config, monkeypatch, logger,
    ):
        root, studies_dir = build_project_root()
        add_provider_config(root, 'ProviderA', extraction_cfg=EXTRACTION_CFG)
        add_provider_config(root, 'ProviderB', extraction_cfg=EXTRACTION_CFG)
        for provider in ('ProviderA', 'ProviderB'):
            ready_dir = studies_dir / 'runFolders' / provider / 'ready'
            _write_xlsx(ready_dir / 'foo.xlsx', [
                ('Biospecimen ID', 'mean', 'SE'),
                ('A1', 1.23, 0.05),
            ])
        self._patch_root(monkeypatch, root)

        records = run_alignment(logger)

        assert sorted(r.provider_name for r in records) == ['ProviderA', 'ProviderB']
        assert all(r.alignment_ok for r in records)

    def test_db_validation_success_wires_program_groups_onto_record(
        self, tmp_path, build_project_root, add_provider_config, monkeypatch, logger,
    ):
        root, studies_dir = build_project_root(
            main_cfg_overrides={'Alignment': {'validate_aliquots_against_db': True}},
        )
        add_provider_config(root, 'ProviderA', extraction_cfg=EXTRACTION_CFG)
        ready_dir = studies_dir / 'runFolders' / 'ProviderA' / 'ready'
        _write_xlsx(ready_dir / 'foo.xlsx', [
            ('Biospecimen ID', 'mean', 'SE'),
            ('A1', 1.23, 0.05),
        ])
        self._patch_root(monkeypatch, root)
        monkeypatch.setattr(
            'alignment.aliquot_db_validator.validate_aliquots',
            lambda aliquots, main_cfg, logger, allow_multiple_programs=False: (
                True, {'PROG1': aliquots}, []
            ),
        )

        records = run_alignment(logger)

        assert len(records) == 1
        record = records[0]
        assert record.db_validation_ok is True
        assert record.program_groups == {'PROG1': ['A1']}
        assert record.program_code == 'PROG1'

    def test_db_validation_failure_marks_record_and_logs_errors(
        self, tmp_path, build_project_root, add_provider_config, monkeypatch, logger, caplog,
    ):
        root, studies_dir = build_project_root(
            main_cfg_overrides={'Alignment': {'validate_aliquots_against_db': True}},
        )
        add_provider_config(root, 'ProviderA', extraction_cfg=EXTRACTION_CFG)
        ready_dir = studies_dir / 'runFolders' / 'ProviderA' / 'ready'
        _write_xlsx(ready_dir / 'foo.xlsx', [
            ('Biospecimen ID', 'mean', 'SE'),
            ('A1', 1.23, 0.05),
        ])
        self._patch_root(monkeypatch, root)
        monkeypatch.setattr(
            'alignment.aliquot_db_validator.validate_aliquots',
            lambda aliquots, main_cfg, logger, allow_multiple_programs=False: (
                False, None, ['Aliquot ID validation failed: not found.']
            ),
        )

        with caplog.at_level('ERROR'):
            records = run_alignment(logger)

        assert len(records) == 1
        assert records[0].db_validation_ok is False
        assert any('DB validation failed' in rec.message for rec in caplog.records)


class TestMain:
    def _stub_initialize_run(self, monkeypatch, main_cfg):
        monkeypatch.setattr(alignment_module, 'initialize_run', lambda *a, **k: {
            'logger': logging.getLogger('test'),
            'main_cfg': main_cfg,
            'log_dir': '/tmp',
            'log_filename': 'alignment_test.log',
        })

    def _stub_send_status_email(self, monkeypatch):
        sent = {}

        def fake_send_status_email(logger, file_records, log_filename, main_cfg,
                                    subject_prefix=None, process_label=None):
            sent['file_records'] = file_records

        monkeypatch.setattr(alignment_module, 'send_status_email', fake_send_status_email)
        return sent

    def test_for_alignment_only_execution_forces_counts_ran_flag_to_false_on_every_record_for_email_template(self, make_config, monkeypatch):
        main_cfg = make_config({})
        self._stub_initialize_run(monkeypatch, main_cfg)
        fake_records = [FileRecord('a.xlsx', 'ProviderA'), FileRecord('b.xlsx', 'ProviderB')]
        assert all(r.counts_ran is True for r in fake_records)  # sanity: default is True
        monkeypatch.setattr(alignment_module, 'run_alignment', lambda logger: fake_records)
        sent = self._stub_send_status_email(monkeypatch)

        alignment_module.main()

        assert all(r.counts_ran is False for r in sent['file_records'])

    def test_unexpected_exception_is_caught_and_email_still_sent(self, make_config, monkeypatch):
        main_cfg = make_config({})
        self._stub_initialize_run(monkeypatch, main_cfg)

        def raise_error(logger):
            raise RuntimeError('boom')

        monkeypatch.setattr(alignment_module, 'run_alignment', raise_error)
        sent = self._stub_send_status_email(monkeypatch)

        alignment_module.main()  # must not raise

        assert sent['file_records'] == []
