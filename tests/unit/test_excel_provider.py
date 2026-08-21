import pytest

from providers.excel_provider import ExcelProvider

PROVIDER_CFG = {
    'provider': {'name': 'TestProvider', 'type': 'excel'},
    'extraction': {
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
    },
}


def test_extract_happy_path(make_config, make_workbook, logger):
    wb_path = make_workbook([
        ('Biospecimen ID', 'mean', 'SE'),
        ('A1', 1.23, 0.05),
        ('A2', 2.34, 0.06),
    ])
    provider = ExcelProvider(make_config(PROVIDER_CFG), logger)

    df = provider.extract(wb_path)

    assert list(df.columns) == ['aliquot_id', 'mtdna_mean', 'mtdna_se']
    assert df['aliquot_id'].tolist() == ['A1', 'A2']
    assert df['mtdna_mean'].tolist() == [1.23, 2.34]


def test_extract_stops_at_first_empty_anchor(make_config, make_workbook, logger):
    wb_path = make_workbook([
        ('Biospecimen ID', 'mean', 'SE'),
        ('A1', 1.23, 0.05),
        (None, None, None),  # stray trailing row: must not be included in the output
        ('A2', 2.34, 0.06),
    ])
    provider = ExcelProvider(make_config(PROVIDER_CFG), logger)

    df = provider.extract(wb_path)

    assert df['aliquot_id'].tolist() == ['A1']


def test_extract_logs_warning_on_header_mismatch_but_still_extracts(make_config, make_workbook, logger, caplog):
    wb_path = make_workbook([
        ('Wrong Header', 'mean', 'SE'),
        ('A1', 1.23, 0.05),
    ])
    provider = ExcelProvider(make_config(PROVIDER_CFG), logger)

    with caplog.at_level('WARNING'):
        df = provider.extract(wb_path)

    assert any('Header mismatch' in rec.message for rec in caplog.records)
    assert df['aliquot_id'].tolist() == ['A1']


def test_extract_missing_extraction_block_raises(make_config, make_workbook, logger):
    wb_path = make_workbook([('a',)])
    provider = ExcelProvider(make_config({'provider': {'name': 'TestProvider'}}), logger)

    with pytest.raises(ValueError, match='Missing "extraction" section'):
        provider.extract(wb_path)


def test_extract_missing_columns_raises(make_config, make_workbook, logger):
    wb_path = make_workbook([('a',)])
    cfg = make_config({
        'provider': {'name': 'TestProvider'},
        'extraction': {'header_row': 1, 'data_start_row': 2, 'columns': {}},
    })
    provider = ExcelProvider(cfg, logger)

    with pytest.raises(ValueError, match='No columns defined'):
        provider.extract(wb_path)


def test_extract_uses_named_sheet_when_configured(make_config, make_workbook, logger):
    wb_path = make_workbook(
        [('Biospecimen ID', 'mean', 'SE'), ('A1', 1.23, 0.05)],
        sheet_name='Results',
    )
    cfg = make_config({**PROVIDER_CFG, 'extraction': {**PROVIDER_CFG['extraction'], 'sheet_name': 'Results'}})
    provider = ExcelProvider(cfg, logger)

    df = provider.extract(wb_path)

    assert df['aliquot_id'].tolist() == ['A1']
