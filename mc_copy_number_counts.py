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
                      schema_fields: dict, output_path_depth: int, logger,
                      program_code: str = None,
                      aliquot_filter: list[str] = None) -> tuple[bool, Path | None, int]:
    """Transpose a single alignment CSV and write the count table to processed_data.

    :param csv_path: Path to the alignment CSV in raw_data
    :param processed_data_dir: Base processed_data directory for output
    :param schema_fields: Dict of schema_key → canonical_column_name from main_config
    :param output_path_depth: Number of parent folder levels from csv_path to preserve in output path
    :param logger: application logger
    :param program_code: When provided, a sub-directory named after the program code is inserted
                         directly under processed_data_dir (e.g. processed_data/ECHO_Code/<run>/file.csv)
    :param aliquot_filter: When provided, only rows whose aliquot ID is in this list are included
                           in the output (used when splitting by program in multi-program runs)
    :returns: (success, output_path, aliquot_count) tuple
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
        return False, None, 0

    try:
        df = pd.read_csv(csv_path)
    except Exception:
        logger.error(f'Failed to read CSV "{csv_path}":\n' + traceback.format_exc())
        return False, None, 0

    # Resolve canonical names from schema
    aliquot_col = schema_fields.get('aliquot_id', 'aliquot_id')
    measurement_cols = [v for k, v in schema_fields.items() if k != 'aliquot_id']

    required_cols = [aliquot_col] + measurement_cols
    if not _validate_columns(df, required_cols, csv_path, logger):
        return False, None, 0

    # Transpose: set aliquot_id as index, keep only measurement columns, then transpose
    try:
        df_work = df[[aliquot_col] + measurement_cols]
        if aliquot_filter is not None:
            df_work = df_work[df_work[aliquot_col].isin(aliquot_filter)]
            if df_work.empty:
                logger.error(f'No rows remain after filtering to program aliquots in "{csv_path.name}".')
                return False, None, 0
        df_counts = df_work.set_index(aliquot_col).transpose()
        df_counts.index.name = None
    except Exception:
        logger.error(f'Failed to transpose data from "{csv_path.name}":\n' + traceback.format_exc())
        return False, None, 0

    aliquot_count = len(df_counts.columns)

    # Build output path: take (output_path_depth) parent folders + filename from csv_path.
    # If a program_code is available, insert it as a sub-directory under processed_data_dir:
    #   processed_data/<program_code>/<run_folder>/<file>.csv
    relative_path = Path(*csv_path.parts[-(output_path_depth + 1):])
    base_dir = processed_data_dir / program_code if program_code else processed_data_dir
    out_path = base_dir / relative_path

    try:
        os.makedirs(out_path.parent, exist_ok=True)
        df_counts.to_csv(out_path)
        logger.info(
            f'Counts table saved ({aliquot_count} aliquot(s), '
            f'{len(df_counts)} measurement(s)) → "{out_path}"'
        )
    except Exception:
        logger.error(f'Failed to write counts table to "{out_path}":\n' + traceback.format_exc())
        return False, None, 0

    return True, out_path, aliquot_count


def run_counts(logger, file_records: list = None):
    """Run the counts step for a list of file records.

    :param logger: application logger
    :param file_records: list of FileRecord objects produced by run_alignment;
                        if empty or None, logs a warning and returns.
    """
    if not file_records:
        logger.warning('Counts step: no file records provided. Nothing to process.')
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
    logger.info(
        f'Output path depth   : {output_path_depth} '
        f'(number of parent folder levels from the input CSV path to preserve when building the output path '
        f'under processed_data; e.g. depth=1 keeps only the immediate parent folder)'
    )

    total_ok = 0
    total_failed = 0
    for record in file_records:
        if record.alignment_output is None:
            # Alignment failed, skip counts
            continue

        handler = CapturingLogHandler(record)
        logger.addHandler(handler)
        try:
            groups = record.program_groups  # {program_code: [aliquot_ids]} or empty
            if len(groups) > 1:
                # Multi-program: produce one output file per program
                logger.info(
                    f'Multi-program run detected for "{Path(record.alignment_output).name}": '
                    f'creating separate counts files for {len(groups)} program(s): '
                    f'{", ".join(sorted(groups))}.'
                )
                all_ok = True
                for prog_code, prog_aliquots in sorted(groups.items()):
                    logger.info(
                        f'Processing counts for program "{prog_code}" '
                        f'({len(prog_aliquots)} aliquot(s)).'
                    )
                    ok, out_path, aliquot_count = _process_one_file(
                        Path(record.alignment_output), processed_data_dir,
                        schema_fields, output_path_depth, logger,
                        program_code=prog_code,
                        aliquot_filter=prog_aliquots,
                    )
                    record.counts_outputs.append((prog_code, out_path))
                    record.counts_aliquot_count += aliquot_count
                    if not ok:
                        all_ok = False
                record.counts_ok = all_ok
                # Set counts_output to the first successful path for backward compat
                record.counts_output = next(
                    (p for _, p in record.counts_outputs if p is not None), None
                )
                if all_ok:
                    total_ok += 1
                else:
                    total_failed += 1
            else:
                # Single-program (or no program info): existing behaviour
                prog_code = record.program_code or (next(iter(groups)) if groups else None)
                ok, out_path, aliquot_count = _process_one_file(
                    Path(record.alignment_output), processed_data_dir,
                    schema_fields, output_path_depth, logger,
                    program_code=prog_code,
                )
                record.counts_ok = ok
                record.counts_output = out_path
                record.counts_aliquot_count = aliquot_count
                if prog_code and out_path:
                    record.counts_outputs.append((prog_code, out_path))
                if ok:
                    total_ok += 1
                else:
                    total_failed += 1
        finally:
            logger.removeHandler(handler)

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
    file_records = []
    try:
        input_path = Path(args.input)
        dummy_record = FileRecord(source_file=str(input_path), provider_name='standalone')
        dummy_record.alignment_output = input_path
        dummy_record.alignment_ok = True
        dummy_record.alignment_ran = False
        file_records = [dummy_record]

        # Extract aliquot IDs from the input CSV for DB validation
        schema_fields: dict = main_cfg.get_value('Schema/fields') or {}
        aliquot_col = schema_fields.get('aliquot_id', 'aliquot_id')
        try:
            import pandas as pd
            df = pd.read_csv(input_path)
            if aliquot_col in df.columns:
                dummy_record.aliquots = df[aliquot_col].astype(str).tolist()
                dummy_record.alignment_aliquot_count = len(dummy_record.aliquots)
            else:
                logger.warning(
                    f'Aliquot ID column "{aliquot_col}" not found in "{input_path.name}". '
                    f'DB validation will be skipped.'
                )
        except Exception:
            logger.warning(f'Could not read aliquot IDs from "{input_path.name}". DB validation will be skipped.\n'
                           + traceback.format_exc())

        # DB validation — same rules as the alignment step
        handler = CapturingLogHandler(dummy_record)
        logger.addHandler(handler)
        try:
            if main_cfg.get_value('Alignment/validate_aliquots_against_db') and dummy_record.aliquots:
                allow_multiple = bool(main_cfg.get_value('Alignment/allow_multiple_programs'))
                logger.info(f'Validating {len(dummy_record.aliquots)} aliquot(s) against DB.')
                from alignment.aliquot_db_validator import validate_aliquots
                ok, program_groups, val_errors = validate_aliquots(
                    dummy_record.aliquots, main_cfg, logger, allow_multiple_programs=allow_multiple
                )
                dummy_record.db_validation_ok = ok
                dummy_record.program_groups = program_groups or {}
                dummy_record.program_code = (
                    next(iter(program_groups)) if program_groups and len(program_groups) == 1 else None
                )
                for err in val_errors:
                    logger.error(err)
                if not ok:
                    logger.error(
                        f'Aliquot DB validation failed for "{input_path.name}". '
                        f'All aliquots must pass DB validation for Counts processing to proceed — '
                        f'processing will be aborted.'
                    )
                    dummy_record.counts_ran = False
        finally:
            logger.removeHandler(handler)

        if dummy_record.counts_ran:
            run_counts(logger, file_records=file_records)

    except Exception:
        logger.critical('Unexpected error during counts:\n' + traceback.format_exc())

    send_status_email(logger, file_records, os.path.join(log_dir, log_filename), main_cfg,
                      subject_prefix=f'{main_cfg.get_value("Email/email_subject_prefix") or "MC Copy Number"} - Counts')
    logger.info('=== MC Copy Number Counts finished ===')


if __name__ == '__main__':
    main()
