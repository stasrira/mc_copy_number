import os

from utils.common import claim_file


class TestClaimFile:
    def test_success_moves_file_into_temp_dir_and_returns_new_path(self, tmp_path, logger):
        src = tmp_path / 'ready' / 'foo.xlsx'
        src.parent.mkdir()
        src.write_text('placeholder')
        temp_dir = tmp_path / 'temp_processing'

        result = claim_file(src, temp_dir, logger)

        assert result == temp_dir / 'foo.xlsx'
        assert result.exists()
        assert not src.exists()

    def test_creates_temp_dir_if_missing(self, tmp_path, logger):
        src = tmp_path / 'ready' / 'foo.xlsx'
        src.parent.mkdir()
        src.write_text('placeholder')
        temp_dir = tmp_path / 'does' / 'not' / 'exist'

        result = claim_file(src, temp_dir, logger)

        assert result.exists()

    def test_missing_source_returns_none_without_crashing(self, tmp_path, logger):
        # Simulates another process already having claimed the file.
        src = tmp_path / 'ready' / 'foo.xlsx'  # never created
        temp_dir = tmp_path / 'temp_processing'

        result = claim_file(src, temp_dir, logger)

        assert result is None

    def test_permission_error_leaves_source_in_place(self, tmp_path, logger, monkeypatch):
        src = tmp_path / 'ready' / 'foo.xlsx'
        src.parent.mkdir()
        src.write_text('placeholder')
        temp_dir = tmp_path / 'temp_processing'

        def raise_permission_error(_src, _dst):
            raise PermissionError('file is open in Excel')

        monkeypatch.setattr(os, 'rename', raise_permission_error)

        result = claim_file(src, temp_dir, logger)

        assert result is None
        assert src.exists()

    def test_unexpected_exception_returns_none_and_is_logged(self, tmp_path, logger, caplog, monkeypatch):
        src = tmp_path / 'ready' / 'foo.xlsx'
        src.parent.mkdir()
        src.write_text('placeholder')
        temp_dir = tmp_path / 'temp_processing'

        def raise_os_error(_src, _dst):
            raise OSError('disk full')

        monkeypatch.setattr(os, 'rename', raise_os_error)

        with caplog.at_level('ERROR'):
            result = claim_file(src, temp_dir, logger)

        assert result is None
        assert any('Failed to claim' in rec.message for rec in caplog.records)

    def test_log_label_is_prefixed_on_success_and_failure(self, tmp_path, logger, caplog):
        src = tmp_path / 'ready' / 'foo.xlsx'
        src.parent.mkdir()
        src.write_text('placeholder')
        temp_dir = tmp_path / 'temp_processing'

        with caplog.at_level('INFO'):
            claim_file(src, temp_dir, logger, log_label='[ProviderA] ')

        assert any(rec.message.startswith('[ProviderA] Claimed') for rec in caplog.records)

        caplog.clear()
        missing = tmp_path / 'ready' / 'bar.xlsx'  # never created
        with caplog.at_level('WARNING'):
            claim_file(missing, temp_dir, logger, log_label='[ProviderA] ')

        assert any(rec.message.startswith('[ProviderA] "bar.xlsx" was already claimed') for rec in caplog.records)
