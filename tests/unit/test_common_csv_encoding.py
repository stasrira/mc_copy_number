import pandas as pd

from utils.common import csv_bom_enabled, resolve_csv_write_encoding


class TestCsvBomEnabled:
    def test_defaults_to_true_when_key_absent(self, make_config):
        assert csv_bom_enabled(make_config({})) is True

    def test_respects_explicit_false(self, make_config):
        assert csv_bom_enabled(make_config({'Csv_output': {'enable_utf8_bom': False}})) is False

    def test_respects_explicit_true(self, make_config):
        assert csv_bom_enabled(make_config({'Csv_output': {'enable_utf8_bom': True}})) is True


class TestResolveCsvWriteEncoding:
    def test_all_ascii_returns_plain_utf8(self):
        df = pd.DataFrame({'aliquot_id': ['A1', 'A2'], 'value': [1, 2]})

        encoding, example = resolve_csv_write_encoding(df)

        assert encoding == 'utf-8'
        assert example is None

    def test_non_ascii_column_name_triggers_bom(self):
        df = pd.DataFrame({'aliquöt_id': ['A1']})

        encoding, example = resolve_csv_write_encoding(df)

        assert encoding == 'utf-8-sig'
        assert example == 'aliquöt_id'

    def test_non_ascii_index_triggers_bom(self):
        df = pd.DataFrame({'value': [1]}, index=['Naïve-A4'])

        encoding, example = resolve_csv_write_encoding(df)

        assert encoding == 'utf-8-sig'
        assert example == 'Naïve-A4'

    def test_non_ascii_cell_value_triggers_bom(self):
        df = pd.DataFrame({'note': ['plain', 'Naïve-A4']})

        encoding, example = resolve_csv_write_encoding(df)

        assert encoding == 'utf-8-sig'
        assert example == 'Naïve-A4'

    def test_enable_utf8_bom_false_forces_plain_utf8_even_with_non_ascii(self):
        df = pd.DataFrame({'note': ['Naïve-A4']})

        encoding, example = resolve_csv_write_encoding(df, enable_utf8_bom=False)

        assert encoding == 'utf-8'
        assert example is None
