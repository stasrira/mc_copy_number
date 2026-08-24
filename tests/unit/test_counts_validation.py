import pandas as pd

from mc_copy_number_counts import _validate_columns, extract_aliquots_from_csv, validate_aligned_csv


class TestValidateColumns:
    def test_all_required_columns_present_returns_true(self, tmp_path, logger):
        df = pd.DataFrame({'aliquot_id': ['A1'], 'mtDNA_copy_num_mean': [1.0]})

        assert _validate_columns(df, ['aliquot_id', 'mtDNA_copy_num_mean'], tmp_path / 'foo.csv', logger) is True

    def test_missing_columns_reported_by_name(self, tmp_path, logger, caplog):
        df = pd.DataFrame({'aliquot_id': ['A1']})

        with caplog.at_level('ERROR'):
            result = _validate_columns(df, ['aliquot_id', 'mtDNA_copy_num_mean'], tmp_path / 'foo.csv', logger)

        assert result is False
        assert any('mtDNA_copy_num_mean' in rec.message for rec in caplog.records)


class TestValidateAlignedCsv:
    def test_happy_path(self, tmp_path, schema_fields, write_aligned_csv, logger):
        csv_path = write_aligned_csv(tmp_path / 'foo.csv', ['A1'])

        df, err = validate_aligned_csv(csv_path, schema_fields, logger)

        assert err is None
        assert df is not None
        assert list(df.columns) == ['aliquot_id', 'mtDNA_copy_num_mean', 'mtDNA_copy_num_SE']

    def test_missing_columns_reports_error(self, tmp_path, schema_fields, logger):
        csv_path = tmp_path / 'foo.csv'
        pd.DataFrame({'aliquot_id': ['A1']}).to_csv(csv_path, index=False)

        df, err = validate_aligned_csv(csv_path, schema_fields, logger)

        assert df is None
        assert 'mtDNA_copy_num_mean' in err

    def test_unreadable_path_reports_error(self, tmp_path, schema_fields, logger):
        # A directory can never be parsed as a CSV -- pd.read_csv raises.
        bad_path = tmp_path / 'not_a_file.csv'
        bad_path.mkdir()

        df, err = validate_aligned_csv(bad_path, schema_fields, logger)

        assert df is None
        assert err is not None


class TestExtractAliquotsFromCsv:
    def test_extracts_ids_as_strings(self):
        df = pd.DataFrame({'aliquot_id': ['A1', 'A2']})

        assert extract_aliquots_from_csv(df) == ['A1', 'A2']

    def test_missing_column_returns_empty_list(self):
        df = pd.DataFrame({'other': [1]})

        assert extract_aliquots_from_csv(df) == []
