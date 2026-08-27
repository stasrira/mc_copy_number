import logging
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
        gc.DEFAULT_LOG_CLEANUP_RETENTION_DAYS (60), same as every other config default in this
        project (see tests/unit/test_default_constants_match_main_config.py)."""
        root, _ = build_project_root()
        self._patch_root(monkeypatch, root)
        log_dir = tmp_path / 'logs'
        old_log = _touch(log_dir / 'old.log', age_seconds=70 * DAY)
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


class TestMainSendsLogCleanupStatusEmail:
    """main() must always send a status email listing what run_log_cleanup() actually deleted —
    stubs initialize_run/load_configs (rather than exercising real file logging, like
    tests/integration/test_main_orchestrator.py does for mc_copy_number.main()) and captures what
    gets passed to send_log_cleanup_status_email instead of sending a real email."""

    def _stub_configs(self, monkeypatch, main_cfg, loc_cfg, log_dir):
        monkeypatch.setattr(log_cleanup_module, 'initialize_run', lambda *a, **k: {
            'logger': logging.getLogger('test'),
            'main_cfg': main_cfg,
            'loc_cfg': loc_cfg,
            'log_dir': str(log_dir),
            'log_filename': 'log_cleanup_test.log',
        })
        monkeypatch.setattr(log_cleanup_module, 'load_configs', lambda project_root: (main_cfg, loc_cfg))

    def _stub_send_email(self, monkeypatch):
        sent = {}

        def fake(logger, result, log_filename, main_cfg, subject_prefix=None, process_label=None,
                 log_dir=None, retention_days=None):
            sent['result'] = result
            sent['retention_days'] = retention_days
            sent['log_dir'] = log_dir
            sent['process_label'] = process_label

        monkeypatch.setattr(log_cleanup_module, 'send_log_cleanup_status_email', fake)
        return sent

    def test_deleted_files_reach_the_status_email(self, tmp_path, make_config, monkeypatch):
        log_dir = tmp_path / 'logs'
        old_log = _touch(log_dir / 'old.log', age_seconds=40 * DAY)
        recent_log = _touch(log_dir / 'recent.log', age_seconds=5 * DAY)

        main_cfg = make_config({'LogCleanup': {'retention_days': 30}}, filename='main.yaml')
        loc_cfg = make_config({'Location': {'logs': str(log_dir)}}, filename='location.yaml')
        self._stub_configs(monkeypatch, main_cfg, loc_cfg, log_dir)
        sent = self._stub_send_email(monkeypatch)

        log_cleanup_module.main()

        assert sent['result'].deleted == [str(old_log)]
        assert sent['retention_days'] == 30
        assert sent['log_dir'] == str(log_dir)
        assert sent['process_label'] == 'Log Cleanup'
        assert not old_log.exists()
        assert recent_log.exists()

    def test_unexpected_error_still_sends_an_email_with_empty_result(self, tmp_path, make_config, monkeypatch):
        main_cfg = make_config({'LogCleanup': {'retention_days': 30}}, filename='main.yaml')
        loc_cfg = make_config({'Location': {'logs': str(tmp_path / 'logs')}}, filename='location.yaml')
        self._stub_configs(monkeypatch, main_cfg, loc_cfg, tmp_path / 'logs')
        sent = self._stub_send_email(monkeypatch)
        monkeypatch.setattr(log_cleanup_module, 'cleanup_old_logs', lambda *a, **k: (_ for _ in ()).throw(OSError('boom')))

        log_cleanup_module.main()

        assert sent['result'].deleted == []
        assert sent['result'].scanned == 0
