import pandas as pd

from mc_copy_number_counts import process_counts_input


def _mock_db_validation(monkeypatch, ok, groups=None, errors=None):
    """Patch alignment.aliquot_db_validator.validate_aliquots and return the list of calls made."""
    calls = []

    def fake(aliquots, main_cfg, logger, allow_multiple_programs=False):
        calls.append(aliquots)
        return ok, groups, errors or []

    monkeypatch.setattr('alignment.aliquot_db_validator.validate_aliquots', fake)
    return calls


def _configs(tmp_path, make_config, schema_fields, validate_against_db=True, allow_multiple_programs=False):
    main_cfg = make_config({
        'Counts': {'processed_data_dir': 'processed_data', 'output_path_depth': 1},
        'Alligned_file_schema': {'fields': schema_fields},
        'Csv_output': {'enable_utf8_bom': True},
        'Alignment': {
            'validate_aliquots_against_db': validate_against_db,
            'allow_multiple_programs': allow_multiple_programs,
        },
    }, filename='main.yaml')
    loc_cfg = make_config(
        {'Location': {'mitCopyN_studies_dir': str(tmp_path / 'studies')}}, filename='loc.yaml',
    )
    return main_cfg, loc_cfg


class TestHappyPath:
    def test_db_validation_success_runs_counts(
        self, tmp_path, make_config, schema_fields, write_aligned_csv, monkeypatch, logger,
    ):
        main_cfg, loc_cfg = _configs(tmp_path, make_config, schema_fields)
        csv_path = write_aligned_csv(tmp_path / 'studies' / 'raw_data' / 'run1' / 'foo.csv', ['A1', 'A2'])
        _mock_db_validation(monkeypatch, ok=True, groups={'PROG1': ['A1', 'A2']})

        record = process_counts_input(
            csv_path, main_cfg, loc_cfg, logger, launch_param='--input_file', launch_value=str(csv_path),
        )

        assert record.counts_ok is True
        assert record.program_code == 'PROG1'
        assert record.counts_output is not None
        assert record.counts_output.exists()


class TestNoProgramInfo:
    def test_validation_disabled_and_no_override_errors(
        self, tmp_path, make_config, schema_fields, write_aligned_csv, logger,
    ):
        main_cfg, loc_cfg = _configs(tmp_path, make_config, schema_fields, validate_against_db=False)
        csv_path = write_aligned_csv(tmp_path / 'studies' / 'raw_data' / 'run1' / 'foo.csv', ['A1'])

        record = process_counts_input(
            csv_path, main_cfg, loc_cfg, logger, launch_param='--input_file', launch_value=str(csv_path),
        )

        assert record.counts_ran is False
        assert record.db_validation_skipped is True
        assert any('Cannot determine program folder' in e for e in record.errors)


class TestProgramCodeOverride:
    def test_invalid_override_rejected_before_any_file_access(
        self, tmp_path, make_config, schema_fields, monkeypatch, logger,
    ):
        main_cfg, loc_cfg = _configs(tmp_path, make_config, schema_fields)
        calls = _mock_db_validation(monkeypatch, ok=True, groups={'PROG1': ['A1']})

        record = process_counts_input(
            tmp_path / 'does_not_exist.csv', main_cfg, loc_cfg, logger,
            launch_param='--input_file', launch_value='x', program_code_override='../etc',
        )

        assert record.counts_ran is False
        assert any('Invalid Program_code override' in e for e in record.errors)
        assert calls == []  # rejected before the CSV was even read, so no DB call was made

    def test_override_alone_bypasses_db_when_validation_disabled(
        self, tmp_path, make_config, schema_fields, write_aligned_csv, logger,
    ):
        main_cfg, loc_cfg = _configs(tmp_path, make_config, schema_fields, validate_against_db=False)
        csv_path = write_aligned_csv(
            tmp_path / 'studies' / 'raw_data' / 'run1' / 'foo.csv', ['A1', 'A2', 'B1'],
        )

        record = process_counts_input(
            csv_path, main_cfg, loc_cfg, logger,
            launch_param='--input_file', launch_value=str(csv_path), program_code_override='FORCED_PROG',
        )

        assert record.counts_ok is True
        assert record.program_groups == {'FORCED_PROG': ['A1', 'A2', 'B1']}
        assert len(record.counts_outputs) == 1
        prog_code, out_path = record.counts_outputs[0]
        assert prog_code == 'FORCED_PROG'
        written = pd.read_csv(out_path, index_col=0)
        assert list(written.columns) == ['A1', 'A2', 'B1']

    def test_override_combines_a_real_multi_program_db_result_into_one_folder(
        self, tmp_path, make_config, schema_fields, write_aligned_csv, monkeypatch, logger,
    ):
        # The DB genuinely reports the aliquots as spanning two programs (accepted because
        # allow_multiple_programs=True), but a Program_code override on the request always wins:
        # every aliquot still ends up in the single forced program's folder, not split by program.
        main_cfg, loc_cfg = _configs(tmp_path, make_config, schema_fields, allow_multiple_programs=True)
        csv_path = write_aligned_csv(
            tmp_path / 'studies' / 'raw_data' / 'run1' / 'foo.csv', ['A1', 'A2', 'B1'],
        )
        _mock_db_validation(monkeypatch, ok=True, groups={'REAL1': ['A1', 'A2'], 'REAL2': ['B1']})

        record = process_counts_input(
            csv_path, main_cfg, loc_cfg, logger,
            launch_param='--input_file', launch_value=str(csv_path), program_code_override='FORCED_PROG',
        )

        assert record.counts_ok is True
        assert record.program_groups == {'FORCED_PROG': ['A1', 'A2', 'B1']}
        assert len(record.counts_outputs) == 1
        prog_code, out_path = record.counts_outputs[0]
        assert prog_code == 'FORCED_PROG'
        written = pd.read_csv(out_path, index_col=0)
        assert sorted(written.columns) == ['A1', 'A2', 'B1']
        assert out_path.parent.parent == tmp_path / 'studies' / 'processed_data' / 'FORCED_PROG'

    def test_override_does_not_rescue_a_failed_db_validation(
        self, tmp_path, make_config, schema_fields, write_aligned_csv, monkeypatch, logger,
    ):
        main_cfg, loc_cfg = _configs(tmp_path, make_config, schema_fields)
        csv_path = write_aligned_csv(tmp_path / 'studies' / 'raw_data' / 'run1' / 'foo.csv', ['A1'])
        _mock_db_validation(monkeypatch, ok=False, groups=None, errors=['not found in DB'])

        record = process_counts_input(
            csv_path, main_cfg, loc_cfg, logger,
            launch_param='--input_file', launch_value=str(csv_path), program_code_override='FORCED_PROG',
        )

        assert record.counts_ran is False
        assert record.program_groups == {}
        assert record.counts_outputs == []


class TestSkipAliquotValidation:
    def test_skip_flag_bypasses_db_call_entirely(
        self, tmp_path, make_config, schema_fields, write_aligned_csv, monkeypatch, logger,
    ):
        main_cfg, loc_cfg = _configs(tmp_path, make_config, schema_fields, validate_against_db=True)
        csv_path = write_aligned_csv(tmp_path / 'studies' / 'raw_data' / 'run1' / 'foo.csv', ['A1'])
        calls = _mock_db_validation(monkeypatch, ok=True, groups={'PROG1': ['A1']})

        record = process_counts_input(
            csv_path, main_cfg, loc_cfg, logger,
            launch_param='--input_file', launch_value=str(csv_path), skip_aliquot_validation=True,
        )

        assert calls == []  # DB validator (mocked to succeed) was never even called
        assert record.db_validation_skipped is True
        # No override and no DB result means the program folder still can't be determined.
        assert record.counts_ran is False
        assert any('Cannot determine program folder' in e for e in record.errors)

    def test_skip_flag_combined_with_override_succeeds_without_db(
        self, tmp_path, make_config, schema_fields, write_aligned_csv, monkeypatch, logger,
    ):
        main_cfg, loc_cfg = _configs(tmp_path, make_config, schema_fields, validate_against_db=True)
        csv_path = write_aligned_csv(tmp_path / 'studies' / 'raw_data' / 'run1' / 'foo.csv', ['A1', 'A2'])
        calls = _mock_db_validation(monkeypatch, ok=True, groups={'PROG1': ['A1', 'A2']})

        record = process_counts_input(
            csv_path, main_cfg, loc_cfg, logger,
            launch_param='--input_file', launch_value=str(csv_path),
            program_code_override='FORCED_PROG', skip_aliquot_validation=True,
        )

        assert calls == []
        assert record.counts_ok is True
        assert record.program_groups == {'FORCED_PROG': ['A1', 'A2']}


class TestUnexpectedException:
    def test_exception_is_caught_and_recorded(
        self, tmp_path, make_config, schema_fields, write_aligned_csv, monkeypatch, logger,
    ):
        main_cfg, loc_cfg = _configs(tmp_path, make_config, schema_fields)
        csv_path = write_aligned_csv(tmp_path / 'studies' / 'raw_data' / 'run1' / 'foo.csv', ['A1'])

        def raise_error(*a, **k):
            raise RuntimeError('boom')

        monkeypatch.setattr('mc_copy_number_counts.validate_aligned_csv', raise_error)

        record = process_counts_input(
            csv_path, main_cfg, loc_cfg, logger, launch_param='--input_file', launch_value=str(csv_path),
        )

        assert record.counts_ran is False
        assert any('Unexpected error' in e for e in record.errors)
