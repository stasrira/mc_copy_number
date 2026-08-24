import openpyxl
import pytest

from requests.request_processor import _parse_request_file, _select_sheet_name


def _write_multi_sheet_workbook(path, sheets: dict):
    """sheets: {sheet_name: [row_tuple, ...]}"""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)
    wb.save(path)


class TestSelectSheetName:
    def test_configured_sheet_found(self, tmp_path, logger):
        path = tmp_path / 'req.xlsx'
        _write_multi_sheet_workbook(path, {'Data': [('a',)], 'Other': [('b',)]})

        assert _select_sheet_name(path, 'Other', logger) == 'Other'

    def test_configured_sheet_missing_falls_back_to_first_with_warning(self, tmp_path, logger, caplog):
        path = tmp_path / 'req.xlsx'
        _write_multi_sheet_workbook(path, {'Data': [('a',)], 'Other': [('b',)]})

        with caplog.at_level('WARNING'):
            result = _select_sheet_name(path, 'NoSuchSheet', logger)

        assert result == 'Data'
        assert any('not found' in rec.message for rec in caplog.records)

    def test_blank_configured_sheet_uses_first_without_warning(self, tmp_path, logger, caplog):
        path = tmp_path / 'req.xlsx'
        _write_multi_sheet_workbook(path, {'Data': [('a',)], 'Other': [('b',)]})

        with caplog.at_level('WARNING'):
            result = _select_sheet_name(path, '', logger)

        assert result == 'Data'
        assert caplog.records == []

    def test_unreadable_file_reports_error_and_returns_empty(self, tmp_path, logger, caplog):
        path = tmp_path / 'not_really.xlsx'
        path.write_text('this is not an excel file')

        with caplog.at_level('ERROR'):
            result = _select_sheet_name(path, None, logger)

        assert result == ''
        assert any('Failed to inspect Excel sheets' in rec.message for rec in caplog.records)


class TestParseRequestFile:
    def test_happy_path_multi_row(self, tmp_path, write_request_xlsx, make_config, logger):
        path = write_request_xlsx(tmp_path / 'req.xlsx', [
            {'Raw_data_source': 'run1/foo.csv', 'Program_code': 'PROG1', 'Skip_aliquot_validation': 'Yes'},
            {'Raw_data_source': 'run2/bar.csv', 'Program_code': '', 'Skip_aliquot_validation': ''},
        ])
        main_cfg = make_config({}, filename='main.yaml')

        entries, err = _parse_request_file(path, main_cfg, logger)

        assert err is None
        assert len(entries) == 2
        assert entries[0]['row_number'] == 2
        assert entries[0]['raw_data_source'] == 'run1/foo.csv'
        assert entries[0]['program_code'] == 'PROG1'
        assert entries[0]['skip_aliquot_validation'] is True
        assert entries[1]['row_number'] == 3
        assert entries[1]['program_code'] == ''
        assert entries[1]['skip_aliquot_validation'] is False

    def test_column_names_are_normalized(self, tmp_path, write_request_xlsx, make_config, logger):
        path = write_request_xlsx(tmp_path / 'req.xlsx', [
            {' Raw Data Source ': 'run1/foo.csv'},
        ])
        main_cfg = make_config({}, filename='main.yaml')

        entries, err = _parse_request_file(path, main_cfg, logger)

        # normalization is strip+lower+space->underscore, not a full alias match, so this
        # exact header ends up as 'raw_data_source' only when spaces separate the words as here
        assert err is None
        assert entries[0]['raw_data_source'] == 'run1/foo.csv'

    def test_alias_column_raw_data_folder_is_renamed(self, tmp_path, write_request_xlsx, make_config, logger):
        path = write_request_xlsx(tmp_path / 'req.xlsx', [{'Raw_data_folder': 'run1/foo.csv'}])
        main_cfg = make_config({}, filename='main.yaml')

        entries, err = _parse_request_file(path, main_cfg, logger)

        assert err is None
        assert entries[0]['raw_data_source'] == 'run1/foo.csv'

    def test_alias_column_skip_validation_typo_is_renamed(self, tmp_path, write_request_xlsx, make_config, logger):
        path = write_request_xlsx(tmp_path / 'req.xlsx', [
            {'Raw_data_source': 'run1/foo.csv', 'Skip_aliquot_validatoin': 'True'},
        ])
        main_cfg = make_config({}, filename='main.yaml')

        entries, err = _parse_request_file(path, main_cfg, logger)

        assert err is None
        assert entries[0]['skip_aliquot_validation'] is True

    def test_missing_required_column_reports_error(self, tmp_path, write_request_xlsx, make_config, logger):
        path = write_request_xlsx(tmp_path / 'req.xlsx', [{'Program_code': 'PROG1'}])
        main_cfg = make_config({}, filename='main.yaml')

        entries, err = _parse_request_file(path, main_cfg, logger)

        assert entries is None
        assert 'Raw_data_source' in err

    def test_empty_sheet_reports_error(self, tmp_path, write_request_xlsx, make_config, logger):
        path = write_request_xlsx(tmp_path / 'req.xlsx', [], columns=['Raw_data_source'])
        main_cfg = make_config({}, filename='main.yaml')

        entries, err = _parse_request_file(path, main_cfg, logger)

        assert entries is None
        assert 'contains no rows' in err
