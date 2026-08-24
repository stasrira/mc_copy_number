import logging

import openpyxl
import pandas as pd
import pytest
import yaml

from utils.configuration import ConfigData


def _deep_merge(base: dict, overrides: dict) -> dict:
    """Recursively merge *overrides* into *base* in place and return *base*."""
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


DEFAULT_MAIN_CONFIG = {
    'Logging': {'log_level': 'INFO', 'mirror_to_stdout': False},
    'Alligned_file_schema': {'fields': {
        'aliquot_id': 'aliquot_id',
        'mtdna_mean': 'mtDNA_copy_num_mean',
        'mtdna_se': 'mtDNA_copy_num_SE',
    }},
    'Alignment': {
        'run_folders_dir': 'runFolders',
        'raw_data_dir': 'raw_data',
        'ready_subfolder': 'ready',
        'processing_temp_subfolder': 'temp_processing',
        'processed_subfolder': 'processed',
        'reprocess_subfolder': 'work',
        'providers_config_dir': 'configs/providers',
        'validate_aliquots_against_db': False,
        'allow_automated_counts_processing': True,
        'allow_multiple_programs': False,
    },
    'Counts': {'processed_data_dir': 'processed_data', 'output_path_depth': 1},
    'Csv_output': {'enable_utf8_bom': True},
    'Requests': {
        'ready_subfolder': 'ready',
        'processing_temp_subfolder': 'processing_temp',
        'processed_subfolder': 'processed',
        'reprocess_subfolder': 'work',
    },
    'Email': {'send_emails': False, 'email_subject_prefix': 'Test'},
}


@pytest.fixture
def make_config(tmp_path):
    """Factory fixture: write *data* as YAML and return it loaded as a ConfigData."""

    def _make(data: dict, filename: str = 'config.yaml') -> ConfigData:
        path = tmp_path / filename
        path.write_text(yaml.safe_dump(data))
        return ConfigData(path)

    return _make


@pytest.fixture
def captured_emails(monkeypatch):
    """Monkeypatch utils.send_email.send_email to capture calls instead of sending."""
    calls = []

    def fake_send_email(emails_to, subject, message, email_from=None, **kwargs):
        calls.append({'to': emails_to, 'subject': subject, 'body': message, 'from': email_from})

    monkeypatch.setattr('utils.send_email.send_email', fake_send_email)
    return calls


@pytest.fixture
def logger():
    return logging.getLogger('test')


@pytest.fixture
def make_workbook(tmp_path):
    """Factory fixture: write *rows* (tuples, including any header row) to a new .xlsx file."""

    def _make(rows: list, filename: str = 'source.xlsx', sheet_name: str = None):
        wb = openpyxl.Workbook()
        ws = wb.active
        if sheet_name:
            ws.title = sheet_name
        for row in rows:
            ws.append(row)
        path = tmp_path / filename
        wb.save(path)
        return path

    return _make


@pytest.fixture
def build_project_root(tmp_path):
    """Factory: build a fake project root with configs/{main,location}_config.yaml under
    tmp_path, for tests that exercise entry-point functions which call get_project_root()/
    load_configs() internally rather than accepting configs as arguments.

    :returns: a function (main_cfg_overrides=None, location_overrides=None) -> (root, studies_dir)
    """

    def _build(main_cfg_overrides: dict = None, location_overrides: dict = None):
        import copy

        root = tmp_path / 'project'
        (root / 'configs').mkdir(parents=True)
        studies_dir = tmp_path / 'studies'
        studies_dir.mkdir()

        main_cfg_data = _deep_merge(copy.deepcopy(DEFAULT_MAIN_CONFIG), main_cfg_overrides or {})
        location_data = _deep_merge(
            {'Location': {'mitCopyN_studies_dir': str(studies_dir), 'logs': str(tmp_path / 'logs')}},
            location_overrides or {},
        )

        (root / 'configs' / 'main_config.yaml').write_text(yaml.safe_dump(main_cfg_data))
        (root / 'configs' / 'location_config.yaml').write_text(yaml.safe_dump(location_data))

        return root, studies_dir

    return _build


@pytest.fixture
def add_provider_config():
    """Factory: write a provider_config.yaml under root/configs/providers/<provider_name>/."""

    def _add(root, provider_name: str, extraction_cfg: dict, source_folder_name: str = None,
             file_pattern: str = '*.xlsx', provider_type: str = 'excel'):
        provider_dir = root / 'configs' / 'providers' / provider_name
        provider_dir.mkdir(parents=True)
        data = {
            'provider': {
                'name': provider_name,
                'source_folder_name': source_folder_name or provider_name,
                'type': provider_type,
                'file_pattern': file_pattern,
            },
            'extraction': extraction_cfg,
        }
        (provider_dir / 'provider_config.yaml').write_text(yaml.safe_dump(data))
        return provider_dir

    return _add


@pytest.fixture
def schema_fields():
    """The canonical Alligned_file_schema/fields mapping used throughout the pipeline."""
    return {
        'aliquot_id': 'aliquot_id',
        'mtdna_mean': 'mtDNA_copy_num_mean',
        'mtdna_se': 'mtDNA_copy_num_SE',
    }


@pytest.fixture
def write_aligned_csv():
    """Factory: write a minimal aligned CSV (aliquot_id + measurement columns) to *path*."""

    def _write(path, aliquot_ids, means=None, ses=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        n = len(aliquot_ids)
        df = pd.DataFrame({
            'aliquot_id': aliquot_ids,
            'mtDNA_copy_num_mean': means if means is not None else [float(i) for i in range(n)],
            'mtDNA_copy_num_SE': ses if ses is not None else [0.1] * n,
        })
        df.to_csv(path, index=False)
        return path

    return _write


@pytest.fixture
def write_request_xlsx():
    """Factory: write a request Excel file from a list of row-dicts to *path*."""

    def _write(path, rows: list, columns: list = None):
        path.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(rows, columns=columns)
        df.to_excel(path, index=False)
        return path

    return _write
