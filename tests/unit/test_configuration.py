from utils.configuration import ConfigData


def test_load_and_get_value_nested_path(make_config):
    cfg = make_config({'Alignment': {'allow_multiple_programs': True}, 'Email': {'send_emails': False}})
    assert cfg.loaded is True
    assert cfg.error is None
    assert cfg.get_value('Alignment/allow_multiple_programs') is True
    assert cfg.get_value('Email/send_emails') is False


def test_get_value_missing_intermediate_key_returns_none(make_config):
    cfg = make_config({'Alignment': {'allow_multiple_programs': True}})
    assert cfg.get_value('Alignment/does_not_exist') is None
    assert cfg.get_value('Does/Not/Exist/At/All') is None


def test_missing_file_sets_error_and_get_value_returns_none(tmp_path):
    missing_path = tmp_path / 'does_not_exist.yaml'
    cfg = ConfigData(missing_path)
    assert cfg.loaded is False
    assert cfg.cfg is None
    assert 'not found' in cfg.error
    # get_value must not crash even though self.cfg is None
    assert cfg.get_value('Alignment/allow_multiple_programs') is None


def test_malformed_yaml_sets_error(tmp_path):
    bad_path = tmp_path / 'bad.yaml'
    bad_path.write_text('key: [unclosed')
    cfg = ConfigData(bad_path)
    assert cfg.loaded is False
    assert cfg.cfg is None
    assert cfg.error is not None


def test_get_item_by_key_stringifies_value(make_config):
    cfg = make_config({'Counts': {'output_path_depth': 1}})
    assert cfg.get_item_by_key('Counts/output_path_depth') == '1'
    assert cfg.get_item_by_key('Counts/missing') is None


def test_get_whole_dictionary_and_update(make_config):
    cfg = make_config({'A': 1})
    assert cfg.get_whole_dictionary() == {'A': 1}
    cfg.update({'B': 2})
    assert cfg.get_whole_dictionary() == {'A': 1, 'B': 2}
    # update with a non-dict is a no-op, not a crash
    cfg.update('not a dict')
    assert cfg.get_whole_dictionary() == {'A': 1, 'B': 2}
