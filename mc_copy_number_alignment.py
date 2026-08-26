"""mc_copy_number_alignment.py

Step 1 of the MC Copy Number processing pipeline.

Scans all provider run folders under the configured runFolders directory,
picks up Excel files from each provider's "ready" sub-folder, converts them
to the standardized format, and writes the output CSV files to the raw_data
destination directory.
"""

import os
import traceback
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from alignment.aliquot_db_validator import run_aliquot_db_validation
from alignment.file_processor import AlignmentFileProcessor
from providers.provider_factory import create_provider
from utils.common import (
    get_project_root, send_status_email, load_configs, initialize_run, csv_bom_enabled,
    resolve_studies_dir, config_subfolder, build_subject_prefix,
)
from utils.configuration import ConfigData
from utils.issue_collector import FileRecord, CapturingLogHandler
import utils.global_const as gc

load_dotenv()

# Process identification shown in the status email subject and header.
PROCESS_LABEL = 'Alignment'


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
                logger.warning(f'Failed to load provider config: "{config_file}". {cfg.error}')
        else:
            logger.debug(f'No {gc.PROVIDER_CONFIG_FILE_NAME} found in "{entry}", skipping.')

    logger.info(f'Total providers discovered: {len(providers)}')
    return providers


def run_alignment(logger):
    project_root = get_project_root()
    main_cfg, loc_cfg = load_configs(project_root)

    # Resolve study root directory
    studies_dir = resolve_studies_dir(loc_cfg)
    if not studies_dir:
        logger.error('Location/mitCopyN_studies_dir is not set in location_config.yaml. Aborting.')
        return []

    run_folders_dir = studies_dir / config_subfolder(main_cfg, 'Alignment/run_folders_dir', gc.DEFAULT_RUN_FOLDERS_DIR)
    raw_data_dir = studies_dir / config_subfolder(main_cfg, 'Alignment/raw_data_dir', gc.DEFAULT_RAW_DATA_DIR)
    ready_subfolder = config_subfolder(main_cfg, 'Alignment/ready_subfolder', gc.DEFAULT_ALIGNMENT_READY_SUBFOLDER)
    temp_subfolder = config_subfolder(
        main_cfg, 'Alignment/processing_temp_subfolder', gc.DEFAULT_ALIGNMENT_PROCESSING_TEMP_SUBFOLDER
    )
    processed_subfolder = config_subfolder(main_cfg, 'Alignment/processed_subfolder', gc.DEFAULT_ALIGNMENT_PROCESSED_SUBFOLDER)
    reprocess_subfolder = config_subfolder(main_cfg, 'Alignment/reprocess_subfolder', gc.DEFAULT_ALIGNMENT_REPROCESS_SUBFOLDER)

    providers_config_dir_rel = config_subfolder(main_cfg, 'Alignment/providers_config_dir', gc.CONFIG_DIR_PROVIDERS)
    providers_config_dir = project_root / providers_config_dir_rel

    enable_utf8_bom = csv_bom_enabled(main_cfg)

    logger.info(f'Run folders base : {run_folders_dir}')
    logger.info(f'Raw data output  : {raw_data_dir}')
    logger.info(f'Providers config : {providers_config_dir}')

    # Load schema: maps schema_key → canonical column name
    schema_fields = main_cfg.get_value('Alligned_file_schema/fields') or {}
    if not schema_fields:
        logger.warning('Alligned_file_schema/fields is not defined in main_config.yaml. Columns will not be renamed.')

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

        processor = AlignmentFileProcessor(provider, str(raw_data_dir), logger, schema_map=schema_fields,
                                           enable_utf8_bom=enable_utf8_bom)

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
                        df = pd.read_csv(out_path, encoding='utf-8-sig')
                        if aliquot_col in df.columns:
                            record.aliquots = df[aliquot_col].astype(str).tolist()
                            record.alignment_aliquot_count = len(record.aliquots)
                    except Exception:
                        logger.warning(f'[{provider_name}] Could not extract aliquot IDs from "{out_path.name}".')

                    # DB validation of aliquot IDs
                    run_db_validation = bool(
                        main_cfg.get_value('Alignment/validate_aliquots_against_db') and record.aliquots
                    )
                    record.db_validation_skipped = not run_db_validation
                    if run_db_validation:
                        ok = run_aliquot_db_validation(
                            record, main_cfg, logger, log_prefix=f'[{provider_name}] '
                        )
                        if not ok:
                            logger.error(
                                f'[{provider_name}] Aliquot DB validation failed for '
                                f'"{out_path.name}". All aliquots in a file must pass DB validation '
                                f'for Counts processing to proceed — this file will be skipped in the '
                                f'Counts step. Review the errors above for details on what needs to be corrected.'
                            )
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
    run = initialize_run(gc.ALIGNMENT_LOG_NAME, 'alignment')
    logger = run['logger']
    main_cfg = run['main_cfg']
    loc_cfg = run['loc_cfg']
    log_dir = run['log_dir']
    log_filename = run['log_filename']

    logger.info('=== MC Copy Number Alignment started ===')
    file_records = []
    try:
        file_records = run_alignment(logger)
        for r in file_records:
            r.counts_ran = False
    except Exception:
        logger.critical('Unexpected error during alignment:\n' + traceback.format_exc())

    send_status_email(logger, file_records, os.path.join(log_dir, log_filename), main_cfg,
                      subject_prefix=build_subject_prefix(main_cfg, loc_cfg, PROCESS_LABEL),
                      process_label=PROCESS_LABEL)
    logger.info('=== MC Copy Number Alignment finished ===')


if __name__ == '__main__':
    main()
