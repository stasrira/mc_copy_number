"""mc_copy_number_counts.py

Step 2 of the MC Copy Number processing pipeline.

Reads a standardized alignment CSV from raw_data, validates its columns against
the schema defined in main_config.yaml, transposes the data so that aliquot IDs
become columns and measurement fields become rows, and writes the result to the
processed_data directory mirroring the raw_data folder structure.

Can be called from the pipeline (receives paths from run_alignment) or run
standalone with --input pointing to an alignment CSV in raw_data.
"""

import argparse
import os
import time
import traceback
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from utils.common import get_project_root
from utils.configuration import ConfigData
from utils.log_utils import setup_logger_common
import utils.global_const as gc

load_dotenv()


def _load_configs(project_root: Path):
    main_cfg = ConfigData(project_root / gc.CONFIG_FILE_MAIN)
    loc_cfg = ConfigData(project_root / gc.CONFIG_FILE_LOCATION)
    return main_cfg, loc_cfg


def _validate_columns(df: pd.DataFrame, required_columns: list[str], csv_path: Path, logger) -> bool:
    """Check that all required canonical columns are present in the DataFrame."""
    missing = [c for c in required_columns if c not in df.columns]
    if missing:
        logger.error(
            f'Column validation failed for "{csv_path.name}": '
            f'missing required column(s): {missing}. '
            f'Found columns: {list(df.columns)}'
        )
        return False
    return True


def _process_one_file(csv_path: Path, processed_data_dir: Path,
                      schema_fields: dict, output_path_depth: int, logger) -> bool:
    """Transpose a single alignment CSV and write the count table to processed_data.

    :param csv_path: Path to the alignment CSV in raw_data
    :param processed_data_dir: Base processed_data directory for output
    :param schema_fields: Dict of schema_key → canonical_column_name from main_config
    :param output_path_depth: Number of parent folder levels from csv_path to preserve in output path
    :param logger: application logger
    :returns: True on success, False on failure
    """
    logger.info(f'Processing counts for: "{csv_path}"')

    # Validate that the path has enough parent folders for the configured depth
    # csv_path.parts includes the filename itself, so we need depth+1 parts minimum
    available_depth = len(csv_path.parts) - 1  # exclude the filename part
    if available_depth < output_path_depth:
        logger.error(
            f'Cannot build output path for "{csv_path}": '
            f'output_path_depth={output_path_depth} but the path only has '
            f'{available_depth} parent folder(s).'
        )
        return False

    try:
        df = pd.read_csv(csv_path)
    except Exception:
        logger.error(f'Failed to read CSV "{csv_path}":\n' + traceback.format_exc())
        return False

    # Resolve canonical names from schema
    aliquot_col = schema_fields.get('aliquot_id', 'aliquot_id')
    measurement_cols = [v for k, v in schema_fields.items() if k != 'aliquot_id']

    required_cols = [aliquot_col] + measurement_cols
    if not _validate_columns(df, required_cols, csv_path, logger):
        return False

    # Transpose: set aliquot_id as index, keep only measurement columns, then transpose
    try:
        df_counts = df[[aliquot_col] + measurement_cols].set_index(aliquot_col).transpose()
        df_counts.index.name = None
    except Exception:
        logger.error(f'Failed to transpose data from "{csv_path.name}":\n' + traceback.format_exc())
        return False

    # Build output path: take (output_path_depth) parent folders + filename from csv_path
    relative_path = Path(*csv_path.parts[-(output_path_depth + 1):])
    out_path = processed_data_dir / relative_path

    try:
        os.makedirs(out_path.parent, exist_ok=True)
        df_counts.to_csv(out_path)
        logger.info(
            f'Counts table saved ({len(df_counts.columns)} aliquot(s), '
            f'{len(df_counts)} measurement(s)) → "{out_path}"'
        )
    except Exception:
        logger.error(f'Failed to write counts table to "{out_path}":\n' + traceback.format_exc())
        return False

    return True


def run_counts(logger, aligned_csv_paths: list = None):
    """Run the counts step for a list of aligned CSV paths.

    :param logger: application logger
    :param aligned_csv_paths: list of Path objects produced by run_alignment;
                              if empty or None, logs a warning and returns.
    """
    if not aligned_csv_paths:
        logger.warning('Counts step: no aligned CSV paths provided. Nothing to process.')
        return

    project_root = get_project_root()
    main_cfg, loc_cfg = _load_configs(project_root)

    studies_dir = loc_cfg.get_value('Location/mitCopyN_studies_dir')
    if not studies_dir:
        logger.error('Location/mitCopyN_studies_dir is not set in location_config.yaml. Aborting.')
        return

    studies_dir = Path(studies_dir)
    processed_data_dir = studies_dir / (main_cfg.get_value('Counts/processed_data_dir') or 'processed_data')
    output_path_depth = int(main_cfg.get_value('Counts/output_path_depth') or 1)

    schema_fields: dict = main_cfg.get_value('Schema/fields') or {}
    if not schema_fields:
        logger.error('Schema/fields is not defined in main_config.yaml. Cannot validate columns. Aborting.')
        return

    logger.info(f'Counts output dir   : {processed_data_dir}')
    logger.info(f'Output path depth   : {output_path_depth}')

    total_ok = 0
    total_failed = 0
    for csv_path in aligned_csv_paths:
        ok = _process_one_file(Path(csv_path), processed_data_dir, schema_fields, output_path_depth, logger)
        if ok:
            total_ok += 1
        else:
            total_failed += 1

    logger.info(f'Counts run complete. Files written: {total_ok}, failed: {total_failed}.')


def main():
    parser = argparse.ArgumentParser(
        description='MC Copy Number — Counts step. Transposes an alignment CSV into a count table.'
    )
    parser.add_argument(
        '--input', '-i',
        required=True,
        metavar='CSV_PATH',
        help='Path to an aligned CSV file produced by the alignment step (in raw_data).'
    )
    args = parser.parse_args()

    project_root = get_project_root()
    main_cfg, loc_cfg = _load_configs(project_root)

    log_dir = loc_cfg.get_value('Location/logs') or 'logs'
    if not os.path.isabs(log_dir):
        log_dir = str(project_root / log_dir)

    log_level = main_cfg.get_value('Logging/log_level') or 'INFO'
    mirror_to_stdout = main_cfg.get_value('Logging/mirror_to_stdout')
    if mirror_to_stdout is None:
        mirror_to_stdout = True

    log_filename = 'counts_' + time.strftime('%Y%m%d_%H%M%S') + '.log'
    log_obj = setup_logger_common(gc.COUNTS_LOG_NAME, log_level, log_dir, log_filename, mirror_to_stdout)
    logger = log_obj['logger']

    logger.info('=== MC Copy Number Counts started (standalone) ===')
    try:
        run_counts(logger, aligned_csv_paths=[Path(args.input)])
    except Exception:
        logger.critical('Unexpected error during counts:\n' + traceback.format_exc())
    logger.info('=== MC Copy Number Counts finished ===')


if __name__ == '__main__':
    main()
