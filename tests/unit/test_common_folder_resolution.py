from pathlib import Path

from utils.common import config_subfolder, resolve_studies_dir


class TestResolveStudiesDir:
    def test_returns_path_when_set(self, make_config):
        loc_cfg = make_config({'Location': {'mitCopyN_studies_dir': '/data/studies'}})
        assert resolve_studies_dir(loc_cfg) == Path('/data/studies')

    def test_returns_none_when_unset(self, make_config):
        loc_cfg = make_config({'Location': {}})
        assert resolve_studies_dir(loc_cfg) is None

    def test_returns_none_when_location_section_missing(self, make_config):
        loc_cfg = make_config({})
        assert resolve_studies_dir(loc_cfg) is None


class TestConfigSubfolder:
    def test_returns_configured_value_when_set(self, make_config):
        cfg = make_config({'Alignment': {'ready_subfolder': 'incoming'}})
        assert config_subfolder(cfg, 'Alignment/ready_subfolder', 'ready') == 'incoming'

    def test_falls_back_to_default_when_unset(self, make_config):
        cfg = make_config({'Alignment': {}})
        assert config_subfolder(cfg, 'Alignment/ready_subfolder', 'ready') == 'ready'

    def test_falls_back_to_default_when_key_is_empty_string(self, make_config):
        cfg = make_config({'Alignment': {'ready_subfolder': ''}})
        assert config_subfolder(cfg, 'Alignment/ready_subfolder', 'ready') == 'ready'
