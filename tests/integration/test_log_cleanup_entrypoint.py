import os
import time

import mc_copy_number_log_cleanup as log_cleanup_module
from mc_copy_number_log_cleanup import run_log_cleanup

DAY = 86400


def _touch(path, age_seconds=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('x')
    if age_seconds:
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))
    return path


class TestRunLogCleanup:
    def _patch_root(self, monkeypatch, root):
        monkeypatch.setattr(log_cleanup_module, 'get_project_root', lambda: root)

    def test_deletes_files_older_than_configured_retention(
        self, tmp_path, build_project_root, monkeypatch, logger,
    ):
        root, _ = build_project_root(main_cfg_overrides={'LogCleanup': {'retention_days': 60}})
        self._patch_root(monkeypatch, root)
        log_dir = tmp_path / 'logs'
        old_log = _touch(log_dir / 'alignment_20200101_000000.log', age_seconds=90 * DAY)
        recent_log = _touch(log_dir / 'alignment_20260101_000000.log', age_seconds=5 * DAY)

        result = run_log_cleanup(logger)

        assert not old_log.exists()
        assert recent_log.exists()
        assert result.deleted == [str(old_log)]

    def test_defaults_retention_when_not_configured(self, tmp_path, build_project_root, monkeypatch, logger):
        """LogCleanup/retention_days absent from main_config.yaml falls back to
        gc.DEFAULT_LOG_CLEANUP_RETENTION_DAYS (21), same as every other config default in this
        project (see tests/unit/test_default_constants_match_main_config.py)."""
        root, _ = build_project_root()
        self._patch_root(monkeypatch, root)
        log_dir = tmp_path / 'logs'
        old_log = _touch(log_dir / 'old.log', age_seconds=25 * DAY)
        recent_log = _touch(log_dir / 'recent.log', age_seconds=10 * DAY)

        result = run_log_cleanup(logger)

        assert not old_log.exists()
        assert recent_log.exists()

    def test_missing_log_dir_is_handled_without_raising(self, tmp_path, build_project_root, monkeypatch, logger):
        root, _ = build_project_root(location_overrides={'Location': {'logs': str(tmp_path / 'no_such_dir')}})
        self._patch_root(monkeypatch, root)

        result = run_log_cleanup(logger)

        assert result.deleted == []
        assert result.scanned == 0
