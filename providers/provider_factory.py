from utils.configuration import ConfigData
from providers.excel_provider import ExcelProvider


# Registry mapping provider type string → provider class
_PROVIDER_REGISTRY = {
    'excel': ExcelProvider,
}


def create_provider(provider_config: ConfigData, logger):
    """Instantiate the correct provider class based on the 'provider/type' config value.

    :param provider_config: ConfigData loaded from a provider_config.yaml
    :param logger: application logger
    :returns: An instance of a BaseProvider subclass
    :raises ValueError: if the provider type is unknown
    """
    provider_type = (provider_config.get_value('provider/type') or '').lower()
    provider_name = provider_config.get_value('provider/name') or 'unknown'

    cls = _PROVIDER_REGISTRY.get(provider_type)
    if cls is None:
        raise ValueError(
            f'Unknown provider type "{provider_type}" for provider "{provider_name}". '
            f'Supported types: {list(_PROVIDER_REGISTRY.keys())}'
        )

    logger.info(f'Creating provider "{provider_name}" of type "{provider_type}".')
    return cls(provider_config, logger)
