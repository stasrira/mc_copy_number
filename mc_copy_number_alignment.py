"""mc_copy_number_alignment.py

Step 1 of the MC Copy Number processing pipeline.

Scans all provider run folders under the configured runFolders directory,
picks up Excel files from each provider's "ready" sub-folder, converts them
to the standardized format, and writes the output CSV files to the raw_data
destination directory.
"""

import glob as glob_module
import os
import time
from pathlib import Path

from dotenv import load_dotenv

from alignment.file_processor import AlignmentFileProcessor
from providers.provider_factory import create_provider
from utils.common import get_project_root, send_status_email
from utils.configuration import ConfigData
from utils.log_utils import setup_logger_common
from utils.issue_collector import FileRecord, CapturingLogHandler
import utils.global_const as gc

load_dotenv()


def _load_configs(project_root: Path):
    main_cfg = ConfigData(project_root / gc.CONFIG_FILE_MAIN)
    loc_cfg = ConfigData(project_root / gc.CONFIG_FILE_LOCATION)
    return main_cfg, loc_cfg


def _resolve_log_dir(loc_cfg: ConfigData, project_root: Path) -> str:
    log_dir = loc_cfg.get_value('Location/logs') or 'logs'
    if not os.path.isabs(log_dir):
        log_dir = str(project_root / log_dir)
    return log_dir


def _discover_providers(providers_config_dir: Path, logger):
    """Scan providers_config_dir for sub-folders containing a provider_config.yaml.

    :returns: list of ConfigData objects, one per discovered provider
    """
    providers = []
    if not providers_config_dir.is_dir():
        logger.warning(f'Providers config directory not found: "{providers_config_dir}"')
        return providers

    for entry in sorted(providers_config_dir.iterdir()):
        if not entry.is_dir():
            continue
        config_file = entry / gc.PROVIDER_CONFIG_FILE_NAME
        if config_file.is_file():
            cfg = ConfigData(config_file)
            if cfg.loaded:
                providers.append(cfg)
                logger.info(f'Discovered provider config: "{config_file}"')
            else:
                logger.warning(f'Failed to load provider config: "{config_file}"')
        else:
            logger.debug(f'No {gc.PROVIDER_CONFIG_FILE_NAME} found in "{entry}", skipping.')

    logger.info(f'Total providers discovered: {len(providers)}')
    return providers


def run_alignment(logger):
    project_root = get_project_root()
    main_cfg, loc_cfg = _load_configs(project_root)

    # Resolve study root directory
    studies_dir = loc_cfg.get_value('Location/mitCopyN_studies_dir')
    if not studies_dir:
        logger.error('Location/mitCopyN_studies_dir is not set in location_config.yaml. Aborting.')
        return []

    studies_dir = Path(studies_dir)
    run_folders_dir = studies_dir / (main_cfg.get_value('Alignment/run_folders_dir') or 'runFolders')
    raw_data_dir = studies_dir / (main_cfg.get_value('Alignment/raw_data_dir') or 'raw_data')
    ready_subfolder = main_cfg.get_value('Alignment/ready_subfolder') or 'ready'
    temp_subfolder = main_cfg.get_value('Alignment/processing_temp_subfolder') or 'temp_processing'
    processed_subfolder = main_cfg.get_value('Alignment/processed_subfolder') or 'processed'
    reprocess_subfolder = main_cfg.get_value('Alignment/reprocess_subfolder') or 'reprocess'

    providers_config_dir_rel = main_cfg.get_value('Alignment/providers_config_dir') or gc.CONFIG_DIR_PROVIDERS
    providers_config_dir = project_root / providers_config_dir_rel

    logger.info(f'Run folders base : {run_folders_dir}')
    logger.info(f'Raw data output  : {raw_data_dir}')
    logger.info(f'Providers config : {providers_config_dir}')

    # Load schema: maps schema_key → canonical column name
    schema_fields = main_cfg.get_value('Schema/fields') or {}
    if not schema_fields:
        logger.warning('Schema/fields is not defined in main_config.yaml. Columns will not be renamed.')

    # Discover all provider configs
    provider_configs = _discover_providers(providers_config_dir, logger)
    if not provider_configs:
        logger.warning('No provider configs found. Nothing to process.')
        return []

    file_records = []
    total_processed = 0
    total_failed = 0

    for provider_cfg in provider_configs:
        provider_name = provider_cfg.get_value('provider/name') or 'unknown'
        source_folder_name = provider_cfg.get_value('provider/source_folder_name') or provider_name
        file_pattern = provider_cfg.get_value('provider/file_pattern') or '*.xlsx'

        provider_run_dir = run_folders_dir / source_folder_name
        ready_dir = provider_run_dir / ready_subfolder
        temp_dir = provider_run_dir / temp_subfolder
        processed_dir = provider_run_dir / processed_subfolder
        reprocess_dir = provider_run_dir / reprocess_subfolder

        if not ready_dir.is_dir():
            logger.warning(f'[{provider_name}] Ready folder not found, skipping: "{ready_dir}"')
            continue

        # Find all matching files in the ready folder, silently ignoring Excel lock files (~$...)
        ready_files = sorted(
            f for f in ready_dir.glob(file_pattern) if not f.name.startswith('~$')
        )
        if not ready_files:
            all_files = [f for f in ready_dir.iterdir() if not f.name.startswith('~$')]
            if all_files:
                logger.warning(
                    f'[{provider_name}] No files matching "{file_pattern}" in ready folder '
                    f'({len(all_files)} file(s) present).'
                )
            else:
                logger.info(f'[{provider_name}] Ready folder is empty, nothing to process.')
            continue

        file_list = ', '.join(f.name for f in ready_files)
        logger.info(f'[{provider_name}] Found {len(ready_files)} file(s) in ready folder: {file_list}')

        try:
            provider = create_provider(provider_cfg, logger)
        except ValueError as e:
            logger.error(f'[{provider_name}] Cannot create provider: {e}')
            continue

        processor = AlignmentFileProcessor(provider, str(raw_data_dir), logger, schema_map=schema_fields)

        for file_path in ready_files:
            record = FileRecord(source_file=str(file_path), provider_name=provider_name)
            handler = CapturingLogHandler(record)
            logger.addHandler(handler)
            try:
                out_path = processor.process_file(file_path, temp_dir, processed_dir, reprocess_dir)
                record.alignment_output = out_path
                record.alignment_ok = out_path is not None
                if out_path is not None:
                    total_processed += 1
                    # Extract aliquot IDs from the aligned CSV
                    aliquot_col = schema_fields.get('aliquot_id', 'aliquot_id')
                    try:
                        import pandas as pd
                        df = pd.read_csv(out_path)
                        if aliquot_col in df.columns:
                            record.aliquots = df[aliquot_col].astype(str).tolist()
                            record.alignment_aliquot_count = len(record.aliquots)
                    except Exception:
                        logger.warning(f'[{provider_name}] Could not extract aliquot IDs from "{out_path.name}".')
                else:
                    total_failed += 1
            finally:
                logger.removeHandler(handler)
            file_records.append(record)

    logger.info(
        f'Alignment run complete. Files processed successfully: {total_processed}, '
        f'failed/skipped: {total_failed}.'
    )
    return file_records


def main():
    project_root = get_project_root()
    main_cfg, loc_cfg = _load_configs(project_root)

    log_dir = _resolve_log_dir(loc_cfg, project_root)
    log_level = main_cfg.get_value('Logging/log_level') or 'INFO'
    mirror_to_stdout = main_cfg.get_value('Logging/mirror_to_stdout')
    if mirror_to_stdout is None:
        mirror_to_stdout = True

    log_filename = 'alignment_' + time.strftime('%Y%m%d_%H%M%S') + '.log'
    log_obj = setup_logger_common(gc.ALIGNMENT_LOG_NAME, log_level, log_dir, log_filename, mirror_to_stdout)
    logger = log_obj['logger']

    logger.info('=== MC Copy Number Alignment started ===')
    file_records = []
    try:
        file_records = run_alignment(logger)
        for r in file_records:
            r.counts_ran = False
    except Exception:
        import traceback
        logger.critical('Unexpected error during alignment:\n' + traceback.format_exc())

    send_status_email(logger, file_records, os.path.join(log_dir, log_filename), main_cfg,
                      subject_prefix=f'{main_cfg.get_value("Email/email_subject_prefix") or "MC Copy Number"} - Alignment')
    logger.info('=== MC Copy Number Alignment finished ===')


if __name__ == '__main__':
    main()
