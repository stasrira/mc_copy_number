"""Guards against utils/global_const.py's DEFAULT_* fallback constants silently drifting from the
values actually shipped in configs/main_config.yaml. These constants exist as the fallback used
when a key is absent from a site's main_config.yaml — if the checked-in YAML's default ever
changes without updating the matching Python constant (or vice versa), a missing-key fallback
would silently resolve to the wrong folder. This test makes that drift a loud failure instead.
"""
from pathlib import Path

import yaml

import utils.global_const as gc

MAIN_CONFIG_PATH = Path(__file__).resolve().parents[2] / 'configs' / 'main_config.yaml'


def _load_main_config() -> dict:
    return yaml.safe_load(MAIN_CONFIG_PATH.read_text())


class TestAlignmentDefaultsMatchMainConfig:
    def test_matches_yaml(self):
        cfg = _load_main_config()['Alignment']
        assert gc.DEFAULT_RUN_FOLDERS_DIR == cfg['run_folders_dir']
        assert gc.DEFAULT_RAW_DATA_DIR == cfg['raw_data_dir']
        assert gc.DEFAULT_ALIGNMENT_READY_SUBFOLDER == cfg['ready_subfolder']
        assert gc.DEFAULT_ALIGNMENT_PROCESSING_TEMP_SUBFOLDER == cfg['processing_temp_subfolder']
        assert gc.DEFAULT_ALIGNMENT_PROCESSED_SUBFOLDER == cfg['processed_subfolder']
        assert gc.DEFAULT_ALIGNMENT_REPROCESS_SUBFOLDER == cfg['reprocess_subfolder']
        assert gc.CONFIG_DIR_PROVIDERS == cfg['providers_config_dir']


class TestCountsDefaultsMatchMainConfig:
    def test_matches_yaml(self):
        cfg = _load_main_config()['Counts']
        assert gc.DEFAULT_PROCESSED_DATA_DIR == cfg['processed_data_dir']


class TestRequestsDefaultsMatchMainConfig:
    def test_matches_yaml(self):
        cfg = _load_main_config()['Requests']
        assert gc.DEFAULT_REQUESTS_READY_SUBFOLDER == cfg['ready_subfolder']
        assert gc.DEFAULT_REQUESTS_PROCESSING_TEMP_SUBFOLDER == cfg['processing_temp_subfolder']
        assert gc.DEFAULT_REQUESTS_PROCESSED_SUBFOLDER == cfg['processed_subfolder']
        assert gc.DEFAULT_REQUESTS_REPROCESS_SUBFOLDER == cfg['reprocess_subfolder']


class TestLogCleanupDefaultRetentionDays:
    """Unlike the DEFAULT_* constants above, this fallback is intentionally allowed to drift from
    configs/main_config.yaml's LogCleanup/retention_days — that value is expected to be tuned
    per-site over time. All this checks is that a sane fallback is actually defined."""

    def test_is_a_positive_int(self):
        assert isinstance(gc.DEFAULT_LOG_CLEANUP_RETENTION_DAYS, int)
        assert gc.DEFAULT_LOG_CLEANUP_RETENTION_DAYS > 0
