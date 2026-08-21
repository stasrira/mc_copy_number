import pytest

from providers.excel_provider import ExcelProvider
from providers.provider_factory import create_provider


def test_create_provider_known_type_returns_instance(make_config, logger):
    cfg = make_config({'provider': {'type': 'excel', 'name': 'NairLab'}})

    provider = create_provider(cfg, logger)

    assert isinstance(provider, ExcelProvider)
    assert provider.name == 'NairLab'


def test_create_provider_type_lookup_is_case_insensitive(make_config, logger):
    cfg = make_config({'provider': {'type': 'EXCEL', 'name': 'NairLab'}})

    provider = create_provider(cfg, logger)

    assert isinstance(provider, ExcelProvider)


def test_create_provider_unknown_type_raises(make_config, logger):
    cfg = make_config({'provider': {'type': 'csv', 'name': 'Weird'}})

    with pytest.raises(ValueError, match='Unknown provider type'):
        create_provider(cfg, logger)


def test_create_provider_missing_type_raises(make_config, logger):
    cfg = make_config({'provider': {'name': 'NoType'}})

    with pytest.raises(ValueError, match='Unknown provider type'):
        create_provider(cfg, logger)
