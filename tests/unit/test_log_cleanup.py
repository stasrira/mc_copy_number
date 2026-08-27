import logging
import os
import time

from utils.log_cleanup import LogCleanupResult, cleanup_old_logs

DAY = 86400


def _touch(path, age_seconds=0, content='x'):
    path.write_text(content)
    if age_seconds:
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))
    return path


class TestCleanupOldLogs:
    def test_missing_directory_returns_empty_result_and_warns(self, tmp_path, logger, caplog):
        missing = tmp_path / 'does_not_exist'

        with caplog.at_level('WARNING'):
            result = cleanup_old_logs(missing, retention_days=60, logger=logger)

        assert result == LogCleanupResult()
        assert any('not found' in rec.message for rec in caplog.records)

    def test_old_file_is_deleted(self, tmp_path, logger):
        old_file = _touch(tmp_path / 'old.log', age_seconds=90 * DAY)

        result = cleanup_old_logs(tmp_path, retention_days=60, logger=logger)

        assert not old_file.exists()
        assert result.deleted == [str(old_file)]
        assert result.deleted_count == 1
        assert result.scanned == 1
        assert result.bytes_freed == 1  # _touch writes a single-char file

    def test_recent_file_is_kept(self, tmp_path, logger):
        recent_file = _touch(tmp_path / 'recent.log', age_seconds=10 * DAY)

        result = cleanup_old_logs(tmp_path, retention_days=60, logger=logger)

        assert recent_file.exists()
        assert result.deleted == []
        assert result.scanned == 1

    def test_file_exactly_at_boundary_is_kept(self, tmp_path, logger):
        """A file just inside the retention window (younger than the cutoff) must survive —
        only strictly older files are deleted."""
        boundary_file = _touch(tmp_path / 'boundary.log', age_seconds=60 * DAY - 60)

        result = cleanup_old_logs(tmp_path, retention_days=60, logger=logger)

        assert boundary_file.exists()
        assert result.deleted == []

    def test_only_matching_pattern_is_considered(self, tmp_path, logger):
        old_log = _touch(tmp_path / 'old.log', age_seconds=90 * DAY)
        old_other = _touch(tmp_path / 'old.txt', age_seconds=90 * DAY)

        result = cleanup_old_logs(tmp_path, retention_days=60, logger=logger)

        assert not old_log.exists()
        assert old_other.exists()
        assert result.scanned == 1

    def test_subdirectories_are_not_recursed_into(self, tmp_path, logger):
        sub_dir = tmp_path / 'sub'
        sub_dir.mkdir()
        nested_old = _touch(sub_dir / 'old.log', age_seconds=90 * DAY)

        result = cleanup_old_logs(tmp_path, retention_days=60, logger=logger)

        assert nested_old.exists()
        assert result.scanned == 0

    def test_mixed_old_and_recent_files(self, tmp_path, logger):
        old_1 = _touch(tmp_path / 'old_1.log', age_seconds=90 * DAY)
        old_2 = _touch(tmp_path / 'old_2.log', age_seconds=61 * DAY)
        recent = _touch(tmp_path / 'recent.log', age_seconds=5 * DAY)

        result = cleanup_old_logs(tmp_path, retention_days=60, logger=logger)

        assert not old_1.exists()
        assert not old_2.exists()
        assert recent.exists()
        assert sorted(result.deleted) == sorted([str(old_1), str(old_2)])
        assert result.scanned == 3

    def test_delete_failure_is_caught_and_reported(self, tmp_path, logger, monkeypatch):
        old_file = _touch(tmp_path / 'old.log', age_seconds=90 * DAY)

        from pathlib import Path
        original_unlink = Path.unlink

        def _boom(self, *args, **kwargs):
            if self.name == 'old.log':
                raise OSError('permission denied')
            return original_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, 'unlink', _boom)

        result = cleanup_old_logs(tmp_path, retention_days=60, logger=logger)

        assert old_file.exists()
        assert result.deleted == []
        assert result.error_count == 1
        assert 'old.log' in result.errors[0][0]

    def test_deletes_are_logged(self, tmp_path, logger, caplog):
        old_file = _touch(tmp_path / 'old.log', age_seconds=90 * DAY)

        with caplog.at_level('INFO'):
            cleanup_old_logs(tmp_path, retention_days=60, logger=logger)

        assert any(str(old_file) in rec.message for rec in caplog.records)
