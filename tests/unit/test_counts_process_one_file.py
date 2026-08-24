import pandas as pd

from mc_copy_number_counts import _process_one_file


class TestTranspose:
    def test_orientation_aliquots_become_columns(self, tmp_path, schema_fields, write_aligned_csv, logger):
        csv_path = write_aligned_csv(tmp_path / 'raw_data' / 'run1' / 'foo.csv', ['A1', 'A2'])
        processed_dir = tmp_path / 'processed_data'

        ok, out_path, count = _process_one_file(csv_path, processed_dir, schema_fields, 1, logger)

        assert ok is True
        assert count == 2
        written = pd.read_csv(out_path, index_col=0)
        assert list(written.index) == ['mtDNA_copy_num_mean', 'mtDNA_copy_num_SE']
        assert list(written.columns) == ['A1', 'A2']


class TestOutputPathDepthBoundary:
    def test_exactly_enough_parents_succeeds_one_short_fails(
        self, tmp_path, schema_fields, write_aligned_csv, logger,
    ):
        csv_path = write_aligned_csv(tmp_path / 'raw_data' / 'run1' / 'foo.csv', ['A1'])
        processed_dir = tmp_path / 'processed_data'
        available_depth = len(csv_path.parts) - 1

        ok, out_path, _ = _process_one_file(csv_path, processed_dir, schema_fields, available_depth, logger)
        assert ok is True
        assert out_path is not None

        ok2, out_path2, count2 = _process_one_file(
            csv_path, processed_dir, schema_fields, available_depth + 1, logger,
        )
        assert ok2 is False
        assert out_path2 is None
        assert count2 == 0


class TestAliquotFilter:
    def test_filter_narrows_output_columns(self, tmp_path, schema_fields, write_aligned_csv, logger):
        csv_path = write_aligned_csv(tmp_path / 'raw_data' / 'run1' / 'foo.csv', ['A1', 'A2', 'A3'])
        processed_dir = tmp_path / 'processed_data'

        ok, out_path, count = _process_one_file(
            csv_path, processed_dir, schema_fields, 1, logger, aliquot_filter=['A1', 'A3'],
        )

        assert ok is True
        assert count == 2
        written = pd.read_csv(out_path, index_col=0)
        assert list(written.columns) == ['A1', 'A3']

    def test_filter_matching_nothing_errors(self, tmp_path, schema_fields, write_aligned_csv, logger):
        csv_path = write_aligned_csv(tmp_path / 'raw_data' / 'run1' / 'foo.csv', ['A1'])
        processed_dir = tmp_path / 'processed_data'

        ok, out_path, count = _process_one_file(
            csv_path, processed_dir, schema_fields, 1, logger, aliquot_filter=['NOT_PRESENT'],
        )

        assert ok is False
        assert out_path is None
        assert count == 0


class TestProgramCodeSubfolder:
    def test_program_code_inserted_as_subfolder(self, tmp_path, schema_fields, write_aligned_csv, logger):
        csv_path = write_aligned_csv(tmp_path / 'raw_data' / 'run1' / 'foo.csv', ['A1'])
        processed_dir = tmp_path / 'processed_data'

        ok, out_path, _ = _process_one_file(
            csv_path, processed_dir, schema_fields, 1, logger, program_code='PROG1',
        )

        assert ok is True
        assert out_path.parent.parent == processed_dir / 'PROG1'

    def test_no_program_code_omits_subfolder(self, tmp_path, schema_fields, write_aligned_csv, logger):
        csv_path = write_aligned_csv(tmp_path / 'raw_data' / 'run1' / 'foo.csv', ['A1'])
        processed_dir = tmp_path / 'processed_data'

        ok, out_path, _ = _process_one_file(csv_path, processed_dir, schema_fields, 1, logger)

        assert ok is True
        assert out_path.parent.parent == processed_dir


class TestBomEncoding:
    def test_non_ascii_aliquot_triggers_bom(self, tmp_path, schema_fields, write_aligned_csv, logger):
        csv_path = write_aligned_csv(tmp_path / 'raw_data' / 'run1' / 'foo.csv', ['Naïve-A4'])
        processed_dir = tmp_path / 'processed_data'

        ok, out_path, _ = _process_one_file(csv_path, processed_dir, schema_fields, 1, logger)

        assert ok is True
        assert out_path.read_bytes().startswith(b'\xef\xbb\xbf')

    def test_bom_disabled_flag_forces_plain_utf8(self, tmp_path, schema_fields, write_aligned_csv, logger):
        csv_path = write_aligned_csv(tmp_path / 'raw_data' / 'run1' / 'foo.csv', ['Naïve-A4'])
        processed_dir = tmp_path / 'processed_data'

        ok, out_path, _ = _process_one_file(
            csv_path, processed_dir, schema_fields, 1, logger, enable_utf8_bom=False,
        )

        assert ok is True
        assert not out_path.read_bytes().startswith(b'\xef\xbb\xbf')


class TestWriteFailure:
    def test_write_failure_when_output_parent_is_blocked_by_a_file(
        self, tmp_path, schema_fields, write_aligned_csv, logger,
    ):
        csv_path = write_aligned_csv(tmp_path / 'raw_data' / 'run1' / 'foo.csv', ['A1'])
        processed_dir = tmp_path / 'processed_data'
        # Pre-create a plain file where the output subfolder needs to be a directory.
        blocking_path = processed_dir / 'run1'
        blocking_path.parent.mkdir(parents=True)
        blocking_path.write_text('not a directory')

        ok, out_path, count = _process_one_file(csv_path, processed_dir, schema_fields, 1, logger)

        assert ok is False
        assert out_path is None
        assert count == 0
