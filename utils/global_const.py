# ========== Config file names
CONFIG_FILE_MAIN = 'configs/main_config.yaml'
CONFIG_FILE_LOCATION = 'configs/location_config.yaml'
CONFIG_DIR_PROVIDERS = 'configs/providers'
PROVIDER_CONFIG_FILE_NAME = 'provider_config.yaml'

# Log names
MAIN_LOG_NAME = 'main_log'
ALIGNMENT_LOG_NAME = 'alignment_log'
COUNTS_LOG_NAME = 'counts_log'
REQUEST_LOG_NAME = 'request_log'
LOG_CLEANUP_LOG_NAME = 'log_cleanup_log'

# ========== Default sub-folder / directory names
# Fallbacks used when the corresponding main_config.yaml key is absent. Keep these in sync with
# the checked-in configs/main_config.yaml — tests/unit/test_default_constants_match_main_config.py
# fails loudly if the two ever drift apart.
DEFAULT_RUN_FOLDERS_DIR = 'runFolders'
DEFAULT_RAW_DATA_DIR = 'raw_data'
DEFAULT_ALIGNMENT_READY_SUBFOLDER = 'ready'
DEFAULT_ALIGNMENT_PROCESSING_TEMP_SUBFOLDER = 'temp_processing'
DEFAULT_ALIGNMENT_PROCESSED_SUBFOLDER = 'processed'
DEFAULT_ALIGNMENT_REPROCESS_SUBFOLDER = 'work'

DEFAULT_PROCESSED_DATA_DIR = 'processed_data'

DEFAULT_REQUESTS_READY_SUBFOLDER = 'ready'
DEFAULT_REQUESTS_PROCESSING_TEMP_SUBFOLDER = 'processing_temp'
DEFAULT_REQUESTS_PROCESSED_SUBFOLDER = 'processed'
DEFAULT_REQUESTS_REPROCESS_SUBFOLDER = 'work'

# Fallback used when LogCleanup/retention_days is absent from main_config.yaml. Unlike the
# defaults above, this one is intentionally *not* required to match the checked-in
# main_config.yaml — that value is expected to be tuned per-site over time, independently of
# this fallback.
DEFAULT_LOG_CLEANUP_RETENTION_DAYS = 60
