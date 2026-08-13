from abc import ABC, abstractmethod
import pandas as pd


class BaseProvider(ABC):
    """Abstract base class for all assay data providers.

    Subclasses implement extraction logic for a specific provider's file format.
    All providers must return a DataFrame with the standardized output columns.
    """

    def __init__(self, provider_config, logger):
        """
        :param provider_config: ConfigData instance loaded from this provider's provider_config.yaml
        :param logger: application logger
        """
        self.config = provider_config
        self.logger = logger

    @property
    def name(self) -> str:
        return self.config.get_value('provider/name') or 'UnknownProvider'

    @property
    def source_folder_name(self) -> str:
        return self.config.get_value('provider/source_folder_name') or self.name

    @property
    def file_pattern(self) -> str:
        return self.config.get_value('provider/file_pattern') or '*.xlsx'

    @abstractmethod
    def extract(self, file_path) -> pd.DataFrame:
        """Extract data from the source file and return a standardized DataFrame.

        :param file_path: Path to the source file (already moved to temp_processing)
        :returns: DataFrame with standardized column names ready to be saved as CSV
        :raises: Exception if the file cannot be read or critical columns are missing
        """
        pass
