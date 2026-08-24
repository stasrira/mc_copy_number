import pandas as pd

from mc_copy_number_counts import run_counts
from utils.issue_collector import FileRecord


def _configs(tmp_path, make_config, schema_fields, output_path_depth=1):
    main_cfg = make_config({
        'Counts': {'processed_data_dir': 'processed_data', 'output_path_depth': output_path_depth},
        'Alligned_file_schema': {'fields': schema_fields},
        'Csv_output': {'enable_utf8_bom': True},
    }, filename='main.yaml')
    loc_cfg = make_config(
        {'Location': {'mitCopyN_studies_dir': str(tmp_path / 'studies')}}, filename='loc.yaml',
    )
    return main_cfg, loc_cfg


class TestRunCountsBasics:
    def test_no_records_warns_and_returns(self, logger, caplog):
        with caplog.at_level('WARNING'):
            run_counts(logger, [])  # must not raise even with no configs supplied

        assert any('no file records provided' in rec.message for rec in caplog.records)

    def test_records_without_alignment_output_are_skipped(self, tmp_path, make_config, schema_fields, logger):
        main_cfg, loc_cfg = _configs(tmp_path, make_config, schema_fields)
        record = FileRecord('n/a', 'test')
        record.alignment_output = None

        run_counts(logger, [record], main_cfg=main_cfg, loc_cfg=loc_cfg)

        assert record.counts_ok is False
        assert record.counts_output is None

    def test_missing_schema_fields_aborts(self, tmp_path, make_config, logger, caplog):
        main_cfg = make_config({'Counts': {'processed_data_dir': 'processed_data'}}, filename='main.yaml')
        loc_cfg = make_config(
            {'Location': {'mitCopyN_studies_dir': str(tmp_path / 'studies')}}, filename='loc.yaml',
        )
        record = FileRecord('n/a', 'test')
        record.alignment_output = tmp_path / 'raw_data' / 'run1' / 'foo.csv'

        with caplog.at_level('ERROR'):
            run_counts(logger, [record], main_cfg=main_cfg, loc_cfg=loc_cfg)

        assert record.counts_ok is False
        assert any('Alligned_file_schema/fields is not defined' in rec.message for rec in caplog.records)


class TestRunCountsSingleProgram:
    def test_single_program_writes_one_file(
        self, tmp_path, make_config, schema_fields, write_aligned_csv, logger,
    ):
        main_cfg, loc_cfg = _configs(tmp_path, make_config, schema_fields)
        csv_path = write_aligned_csv(tmp_path / 'studies' / 'raw_data' / 'run1' / 'foo.csv', ['A1', 'A2'])
        record = FileRecord(str(csv_path), 'test')
        record.alignment_output = csv_path
        record.alignment_ok = True
        record.program_code = 'PROG1'
        record.program_groups = {'PROG1': ['A1', 'A2']}

        run_counts(logger, [record], main_cfg=main_cfg, loc_cfg=loc_cfg)

        assert record.counts_ok is True
        assert record.counts_output is not None
        assert record.counts_output.exists()
        assert record.counts_aliquot_count == 2
        written = pd.read_csv(record.counts_output, index_col=0)
        assert list(written.columns) == ['A1', 'A2']


class TestRunCountsMultiProgram:
    def test_aliquots_split_across_programs_land_in_their_own_program_folders(
        self, tmp_path, make_config, schema_fields, write_aligned_csv, logger,
    ):
        main_cfg, loc_cfg = _configs(tmp_path, make_config, schema_fields)
        csv_path = write_aligned_csv(
            tmp_path / 'studies' / 'raw_data' / 'run1' / 'foo.csv', ['A1', 'A2', 'B1'],
        )
        record = FileRecord(str(csv_path), 'test')
        record.alignment_output = csv_path
        record.alignment_ok = True
        # Aliquots from the SAME source file belong to two different programs.
        record.program_groups = {'PROG1': ['A1', 'A2'], 'PROG2': ['B1']}

        run_counts(logger, [record], main_cfg=main_cfg, loc_cfg=loc_cfg)

        assert record.counts_ok is True
        assert record.counts_aliquot_count == 3
        outputs = dict(record.counts_outputs)
        assert set(outputs) == {'PROG1', 'PROG2'}

        prog1_csv = pd.read_csv(outputs['PROG1'], index_col=0)
        assert list(prog1_csv.columns) == ['A1', 'A2']
        assert outputs['PROG1'].parent.parent == tmp_path / 'studies' / 'processed_data' / 'PROG1'

        prog2_csv = pd.read_csv(outputs['PROG2'], index_col=0)
        assert list(prog2_csv.columns) == ['B1']
        assert outputs['PROG2'].parent.parent == tmp_path / 'studies' / 'processed_data' / 'PROG2'

    def test_one_program_failing_marks_whole_record_not_ok(
        self, tmp_path, make_config, schema_fields, write_aligned_csv, logger,
    ):
        main_cfg, loc_cfg = _configs(tmp_path, make_config, schema_fields)
        csv_path = write_aligned_csv(tmp_path / 'studies' / 'raw_data' / 'run1' / 'foo.csv', ['A1'])
        record = FileRecord(str(csv_path), 'test')
        record.alignment_output = csv_path
        record.alignment_ok = True
        # PROG2's aliquot doesn't exist in this CSV at all -> its file write fails.
        record.program_groups = {'PROG1': ['A1'], 'PROG2': ['NOT_IN_CSV']}

        run_counts(logger, [record], main_cfg=main_cfg, loc_cfg=loc_cfg)

        assert record.counts_ok is False
        outputs = dict(record.counts_outputs)
        assert outputs['PROG1'] is not None
        assert outputs['PROG2'] is None


class TestRunCountsForcedProgramOverride:
    def test_forced_single_program_group_writes_everything_into_one_folder(
        self, tmp_path, make_config, schema_fields, write_aligned_csv, logger,
    ):
        # Simulates the result of a Program_code override in process_counts_input: aliquots that
        # would otherwise span multiple programs are collapsed into a single forced group before
        # run_counts ever sees them, so Counts always treats the record as single-program and
        # writes one file for all of them under the forced program's folder.
        main_cfg, loc_cfg = _configs(tmp_path, make_config, schema_fields)
        csv_path = write_aligned_csv(
            tmp_path / 'studies' / 'raw_data' / 'run1' / 'foo.csv', ['A1', 'A2', 'B1'],
        )
        record = FileRecord(str(csv_path), 'test')
        record.alignment_output = csv_path
        record.alignment_ok = True
        record.program_code = 'FORCED_PROG'
        record.program_groups = {'FORCED_PROG': ['A1', 'A2', 'B1']}

        run_counts(logger, [record], main_cfg=main_cfg, loc_cfg=loc_cfg)

        assert record.counts_ok is True
        assert len(record.counts_outputs) == 1
        prog_code, out_path = record.counts_outputs[0]
        assert prog_code == 'FORCED_PROG'
        written = pd.read_csv(out_path, index_col=0)
        assert list(written.columns) == ['A1', 'A2', 'B1']
        assert out_path.parent.parent == tmp_path / 'studies' / 'processed_data' / 'FORCED_PROG'
