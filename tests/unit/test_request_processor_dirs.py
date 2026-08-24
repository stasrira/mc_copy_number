import pytest

from requests.request_processor import _discover_request_files, _parse_bool, _resolve_requests_dirs


class TestResolveRequestsDirs:
    def test_missing_requests_dir_raises(self, make_config, tmp_path):
        loc_cfg = make_config({}, filename='loc.yaml')
        main_cfg = make_config({}, filename='main.yaml')

        with pytest.raises(ValueError, match='Location/requests_dir is not set'):
            _resolve_requests_dirs(loc_cfg, main_cfg, tmp_path)

    def test_subfolders_default_when_not_configured(self, make_config, tmp_path):
        requests_dir = tmp_path / 'requests'
        loc_cfg = make_config({'Location': {'requests_dir': str(requests_dir)}}, filename='loc.yaml')
        main_cfg = make_config({}, filename='main.yaml')

        dirs = _resolve_requests_dirs(loc_cfg, main_cfg, tmp_path)

        assert dirs['requests_dir'] == requests_dir
        assert dirs['ready'] == requests_dir / 'ready'
        assert dirs['processing_temp'] == requests_dir / 'processing_temp'
        assert dirs['processed'] == requests_dir / 'processed'
        assert dirs['work'] == requests_dir / 'work'

    def test_subfolders_use_configured_overrides(self, make_config, tmp_path):
        requests_dir = tmp_path / 'requests'
        loc_cfg = make_config({'Location': {'requests_dir': str(requests_dir)}}, filename='loc.yaml')
        main_cfg = make_config({
            'Requests': {
                'ready_subfolder': 'incoming',
                'processing_temp_subfolder': 'in_progress',
                'processed_subfolder': 'done',
                'reprocess_subfolder': 'needs_attention',
            },
        }, filename='main.yaml')

        dirs = _resolve_requests_dirs(loc_cfg, main_cfg, tmp_path)

        assert dirs['ready'] == requests_dir / 'incoming'
        assert dirs['processing_temp'] == requests_dir / 'in_progress'
        assert dirs['processed'] == requests_dir / 'done'
        assert dirs['work'] == requests_dir / 'needs_attention'


class TestDiscoverRequestFiles:
    def test_missing_dir_returns_empty(self, tmp_path):
        assert _discover_request_files(tmp_path / 'does_not_exist') == []

    def test_sorts_and_ignores_lock_files(self, tmp_path):
        ready = tmp_path / 'ready'
        ready.mkdir()
        (ready / 'b.xlsx').write_text('x')
        (ready / 'a.xlsx').write_text('x')
        (ready / '~$b.xlsx').write_text('lock file placeholder')
        (ready / 'notes.txt').write_text('not a request file')

        files = _discover_request_files(ready)

        assert [f.name for f in files] == ['a.xlsx', 'b.xlsx']


class TestParseBool:
    @pytest.mark.parametrize('value', ['True', 'true', 'YES', 'yes', '1', True, 1])
    def test_truthy_values(self, value):
        assert _parse_bool(value) is True

    @pytest.mark.parametrize('value', [None, 'False', 'false', 'no', '0', '', False, 0])
    def test_falsy_values(self, value):
        assert _parse_bool(value) is False
