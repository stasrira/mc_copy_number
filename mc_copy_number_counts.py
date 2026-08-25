"""mc_copy_number_counts.py

Step 2 of the MC Copy Number processing pipeline.

Reads a standardized alignment CSV from raw_data, validates its columns against
the schema defined in main_config.yaml, transposes the data so that aliquot IDs
become columns and measurement fields become rows, and writes the result to the
processed_data directory mirroring the raw_data folder structure.

Can be called from the pipeline (receives paths from run_alignment) or run
standalone with --input_file/--input_dir pointing to an alignment CSV in raw_data.
"""

import argparse
import os
import re
import traceback
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from alignment.aliquot_db_validator import run_aliquot_db_validation
from utils.common import (
    get_project_root, send_status_email, load_configs, initialize_run,
    resolve_csv_write_encoding, csv_bom_enabled, resolve_studies_dir, config_subfolder,
)
from utils.configuration import ConfigData
from utils.issue_collector import FileRecord, CapturingLogHandler
import utils.global_const as gc

load_dotenv()

# Process identification shown in the status email subject and header.
PROCESS_LABEL = 'Counts'

# A Program_code override comes directly from a lab-submitted request Excel column and is used
# as a filesystem path component (processed_data_dir/<program_code>/...). Restrict to a safe
# charset and require the value to start/end with an alphanumeric or underscore character, so a
# pure-dot component like ".." (a path-traversal component) can never validate.
_PROGRAM_CODE_RE = re.compile(r'^[A-Za-z0-9_](?:[A-Za-z0-9_.-]*[A-Za-z0-9_])?$')


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


def validate_aligned_csv(csv_path: Path, schema_fields: dict, logger) -> tuple[pd.DataFrame | None, str | None]:
    """Read an aligned CSV and validate that it contains all required schema columns.

    :returns: ``(df, error_message)``. ``df`` is ``None`` and ``error_message`` is set
              on any validation or read failure.
    """
    try:
        df = pd.read_csv(csv_path, encoding='utf-8-sig')
    except Exception:
        err = (
            f'Failed to read CSV "{csv_path}". '
            f'Please verify the file is a valid CSV in the aligned format.\n'
            + traceback.format_exc()
        )
        logger.error(err)
        return None, err

    aliquot_col = schema_fields.get('aliquot_id', 'aliquot_id')
    measurement_cols = [v for k, v in schema_fields.items() if k != 'aliquot_id']
    required_cols = [aliquot_col] + measurement_cols

    missing_cols = [c for c in required_cols if c not in df.columns]
    if missing_cols:
        err = (
            f'Aligned file format validation failed for "{csv_path.name}": '
            f'missing required column(s): {missing_cols}. '
            f'Found columns: {list(df.columns)}. '
            f'Please ensure the file was produced by the alignment step and matches '
            f'the Alligned_file_schema/fields defined in main_config.yaml.'
        )
        logger.error(err)
        return None, err

    logger.info(f'Aligned file format validated: all required columns present in "{csv_path.name}".')
    return df, None


def extract_aliquots_from_csv(df: pd.DataFrame, aliquot_col: str = 'aliquot_id') -> list[str]:
    """Return aliquot IDs from the aligned CSV DataFrame as strings."""
    if aliquot_col not in df.columns:
        return []
    return df[aliquot_col].astype(str).tolist()


def _process_one_file(csv_path: Path, processed_data_dir: Path,
                      schema_fields: dict, output_path_depth: int, logger,
                      program_code: str = None,
                      aliquot_filter: list[str] = None,
                      enable_utf8_bom: bool = True) -> tuple[bool, Path | None, int]:
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
    :param enable_utf8_bom: Csv_output/enable_utf8_bom config flag; see resolve_csv_write_encoding().
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
        df = pd.read_csv(csv_path, encoding='utf-8-sig')
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
        # Only add a UTF-8 BOM (via utf-8-sig) when the data actually needs it — see
        # resolve_csv_write_encoding()'s docstring for why this is conditional rather than
        # always-on. Each program's split-off Counts file is checked independently, since only
        # some programs may contain non-ASCII aliquot IDs in a multi-program run.
        encoding, non_ascii_example = resolve_csv_write_encoding(df_counts, enable_utf8_bom=enable_utf8_bom)
        if non_ascii_example:
            # Logged at INFO (not WARNING) and tagged via `extra` so the status email aggregates
            # this into a single BOM note per record instead of one confusingly similar warning
            # per output file — output files across programs can share the same name, differing
            # only by parent folder, which made per-file warnings hard to tell apart.
            logger.info(
                f'"{out_path.name}" saved with a UTF-8 BOM (non-ASCII character(s) found, '
                f'e.g. aliquot ID "{non_ascii_example}").',
                extra={'bom_path': str(out_path), 'bom_example': non_ascii_example},
            )
        df_counts.to_csv(out_path, encoding=encoding)
        logger.info(
            f'Counts table saved ({aliquot_count} aliquot(s), '
            f'{len(df_counts)} measurement(s)) → "{out_path}"'
        )
    except Exception:
        logger.error(f'Failed to write counts table to "{out_path}":\n' + traceback.format_exc())
        return False, None, 0

    return True, out_path, aliquot_count


def run_counts(logger, file_records: list = None, main_cfg: ConfigData = None, loc_cfg: ConfigData = None):
    """Run the counts step for a list of file records.

    :param logger: application logger
    :param file_records: list of FileRecord objects produced by run_alignment;
                        if empty or None, logs a warning and returns.
    :param main_cfg: optional pre-loaded main configuration; loaded from disk if omitted.
    :param loc_cfg: optional pre-loaded location configuration; loaded from disk if omitted.
    """
    if not file_records:
        logger.warning('Counts step: no file records provided. Nothing to process.')
        return

    project_root = get_project_root()
    if main_cfg is None or loc_cfg is None:
        main_cfg, loc_cfg = load_configs(project_root)

    studies_dir = resolve_studies_dir(loc_cfg)
    if not studies_dir:
        logger.error('Location/mitCopyN_studies_dir is not set in location_config.yaml. Aborting.')
        return

    processed_data_dir = studies_dir / config_subfolder(main_cfg, 'Counts/processed_data_dir', 'processed_data')
    output_path_depth = int(main_cfg.get_value('Counts/output_path_depth') or 1)

    schema_fields: dict = main_cfg.get_value('Alligned_file_schema/fields') or {}
    if not schema_fields:
        logger.error('Alligned_file_schema/fields is not defined in main_config.yaml. Cannot validate columns. Aborting.')
        return

    enable_utf8_bom = csv_bom_enabled(main_cfg)

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
                        enable_utf8_bom=enable_utf8_bom,
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
                    enable_utf8_bom=enable_utf8_bom,
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


def process_counts_input(
    input_path: Path,
    main_cfg: ConfigData,
    loc_cfg: ConfigData,
    logger,
    launch_param: str,
    launch_value: str,
    program_code_override: str | None = None,
    skip_aliquot_validation: bool = False,
) -> FileRecord:
    """Process a single aligned CSV end-to-end using the counts pipeline.

    This is the shared implementation used by both the standalone CLI and the
    request processor. It validates the CSV format, extracts aliquots, runs DB
    validation (unless skipped), applies any program-code override, and executes
    the counts step.

    :param input_path: resolved Path to the aligned CSV file
    :param main_cfg: main configuration
    :param loc_cfg: location configuration
    :param logger: application logger
    :param launch_param: CLI parameter name that produced this input (for reporting)
    :param launch_value: CLI parameter value that produced this input (for reporting)
    :param program_code_override: optional program code to override DB-derived value
    :param skip_aliquot_validation: if True, skip DB aliquot validation
    :returns: populated FileRecord
    """
    record = FileRecord(source_file=str(input_path), provider_name='standalone')
    record.alignment_output = input_path
    record.alignment_ok = True
    record.alignment_ran = False
    record.launch_param = launch_param
    record.launch_value = launch_value

    handler = CapturingLogHandler(record)
    logger.addHandler(handler)
    try:
        if program_code_override and not _PROGRAM_CODE_RE.match(program_code_override):
            logger.error(
                f'Invalid Program_code override "{program_code_override}": only letters, digits, '
                f'".", "-" and "_" are allowed, and the value cannot start or end with "." or "-" '
                f'(this value is used as an output folder name). Please correct it and resubmit.'
            )
            record.counts_ran = False
            return record

        # Validate aligned CSV format and extract aliquots
        schema_fields: dict = main_cfg.get_value('Alligned_file_schema/fields') or {}
        df, fmt_err = validate_aligned_csv(input_path, schema_fields, logger)
        if fmt_err or df is None:
            record.counts_ran = False
            return record

        aliquot_col = schema_fields.get('aliquot_id', 'aliquot_id')
        record.aliquots = extract_aliquots_from_csv(df, aliquot_col)
        record.alignment_aliquot_count = len(record.aliquots)
        logger.info(f'Extracted {len(record.aliquots)} aliquot(s) from "{input_path.name}".')

        # Determine validation behaviour
        global_validation_enabled = bool(main_cfg.get_value('Alignment/validate_aliquots_against_db'))
        run_validation = global_validation_enabled and not skip_aliquot_validation
        record.db_validation_skipped = not run_validation

        if skip_aliquot_validation:
            logger.info('Aliquot DB validation skipped per request entry.')

        if program_code_override:
            logger.info(f'Program_code override applied: "{program_code_override}".')

        # Run DB validation if needed
        if run_validation:
            db_ok = run_aliquot_db_validation(record, main_cfg, logger)
            if not db_ok:
                logger.error(
                    f'Aliquot DB validation failed for "{input_path.name}". '
                    f'All aliquots must pass DB validation for Counts processing to proceed — '
                    f'processing will be aborted.'
                )
                record.counts_ran = False
                return record

        # Apply program code override if provided (validation passed or was skipped)
        if program_code_override:
            record.program_code = program_code_override
            record.program_groups = {program_code_override: record.aliquots}
            record.db_validation_ok = True

        # Enforce program folder requirement
        if not record.program_groups:
            logger.error(
                f'Cannot determine program folder for "{input_path.name}". '
                f'Provide a Program_code override or enable aliquot DB validation.'
            )
            record.counts_ran = False
            return record

        # Run counts
        run_counts(logger, [record], main_cfg=main_cfg, loc_cfg=loc_cfg)
        return record

    except Exception:
        logger.error(f'Unexpected error processing "{input_path.name}":\n' + traceback.format_exc())
        record.counts_ran = False
        return record
    finally:
        logger.removeHandler(handler)


def main():
    parser = argparse.ArgumentParser(
        description='MC Copy Number — Counts step. Transposes an alignment CSV into a count table.'
    )
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        '--input_file', '-i',
        metavar='CSV_PATH',
        help='Path to an aligned CSV file produced by the alignment step (in raw_data).'
    )
    input_group.add_argument(
        '--input_dir', '-d',
        metavar='DIR_PATH',
        help=(
            'Path to a directory containing exactly one aligned CSV file. '
            'An error is reported if the directory contains zero or more than one CSV file.'
        )
    )
    args = parser.parse_args()

    run = initialize_run(gc.COUNTS_LOG_NAME, 'counts')
    logger = run['logger']
    main_cfg = run['main_cfg']
    loc_cfg = run['loc_cfg']
    log_dir = run['log_dir']
    log_filename = run['log_filename']

    logger.info('=== MC Copy Number Counts started (standalone) ===')
    try:
        launch_param = '--input_file' if args.input_file else '--input_dir'
        raw_source = args.input_file or args.input_dir
        input_path = None

        # Build a placeholder record so parameter-level errors are captured and emailed.
        # source_file stays N/A until a CSV has actually been resolved.
        placeholder = FileRecord(source_file='N/A', provider_name='standalone')
        placeholder.alignment_ran = False
        placeholder.launch_param = launch_param
        placeholder.launch_value = raw_source

        handler = CapturingLogHandler(placeholder)
        logger.addHandler(handler)
        try:
            if args.input_file:
                candidate = Path(args.input_file)
                if not candidate.exists():
                    logger.error(
                        f'--input_file path does not exist: "{candidate}". '
                        f'Please provide a valid path to an aligned CSV file.'
                    )
                elif candidate.is_dir():
                    logger.error(
                        f'--input_file points to a directory, not a file: "{candidate}". '
                        f'To process a CSV inside a directory use --input_dir instead.'
                    )
                elif not candidate.is_file():
                    logger.error(
                        f'--input_file path is not a regular file: "{candidate}". '
                        f'Please provide a valid path to an aligned CSV file.'
                    )
                else:
                    input_path = candidate
                    placeholder.source_file = str(input_path)
            else:
                input_dir = Path(args.input_dir)
                if not input_dir.is_dir():
                    logger.error(
                        f'--input_dir path does not exist or is not a directory: "{input_dir}". '
                        f'Please provide a valid directory path.'
                    )
                else:
                    csv_files = sorted(f for f in input_dir.glob('*.csv') if not f.name.startswith('~$'))
                    if len(csv_files) == 0:
                        logger.error(
                            f'No CSV files found in directory: "{input_dir}". '
                            f'Please ensure an aligned CSV file is present.'
                        )
                    elif len(csv_files) > 1:
                        logger.error(
                            f'Expected exactly one CSV file in "{input_dir}", '
                            f'but found {len(csv_files)}: {", ".join(f.name for f in csv_files)}. '
                            f'Please use --input_file and point to the specific CSV file you want to process, '
                            f'for example: --input_file {csv_files[0]}'
                        )
                    else:
                        input_path = csv_files[0]
                        placeholder.source_file = str(input_path)
                        logger.info(f'Found CSV file in directory: "{input_path}"')
        finally:
            logger.removeHandler(handler)

        if input_path is None:
            placeholder.counts_ran = False
            file_records = [placeholder]
        else:
            # Delegate all end-to-end counts processing to the shared helper.
            file_records = [
                process_counts_input(
                    input_path,
                    main_cfg,
                    loc_cfg,
                    logger,
                    launch_param=launch_param,
                    launch_value=raw_source,
                )
            ]

    except Exception:
        logger.error('Unexpected error during counts:\n' + traceback.format_exc())
        placeholder = FileRecord(source_file='N/A', provider_name='standalone')
        placeholder.counts_ran = False
        file_records = [placeholder]

    send_status_email(
        logger, file_records, os.path.join(log_dir, log_filename), main_cfg,
        subject_prefix=f'{main_cfg.get_value("Email/email_subject_prefix") or "MC Copy Number"} - {PROCESS_LABEL}',
        process_label=PROCESS_LABEL,
    )
    logger.info('=== MC Copy Number Counts finished ===')


if __name__ == '__main__':
    main()
