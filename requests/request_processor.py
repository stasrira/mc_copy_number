"""Request file processing for mc_copy_number.

A request file is an Excel workbook (*.xlsx) with one row per count-processing job.
Each row describes an aligned CSV source, optional program code override, and optional
skip flag for aliquot validation.
"""

import os
import shutil
import traceback
from pathlib import Path

import pandas as pd

from mc_copy_number_counts import process_counts_input
from utils.common import claim_file, unique_dest_path
from utils.configuration import ConfigData
from utils.issue_collector import RequestRecord, RequestEntryRecord, CapturingLogHandler
import utils.global_const as gc


def _resolve_requests_dirs(loc_cfg: ConfigData, main_cfg: ConfigData, project_root: Path) -> dict:
    """Return absolute Paths for request lifecycle folders."""
    requests_dir = loc_cfg.get_value('Location/requests_dir')
    if not requests_dir:
        raise ValueError('Location/requests_dir is not set in location_config.yaml.')
    requests_dir = Path(requests_dir)

    def subfolder(key: str, default: str) -> Path:
        return requests_dir / (main_cfg.get_value(f'Requests/{key}') or default)

    return {
        'requests_dir': requests_dir,
        'ready': subfolder('ready_subfolder', 'ready'),
        'processing_temp': subfolder('processing_temp_subfolder', 'processing_temp'),
        'processed': subfolder('processed_subfolder', 'processed'),
        'work': subfolder('reprocess_subfolder', 'work'),
    }


def _parse_bool(value) -> bool:
    """Return True for True/Yes/1 (case-insensitive); anything else is False."""
    if value is None:
        return False
    return str(value).strip().lower() in {'true', 'yes', '1'}


def _discover_request_files(ready_dir: Path):
    """Return sorted list of .xlsx files in ready_dir, excluding Excel lock files."""
    if not ready_dir.is_dir():
        return []
    return sorted(f for f in ready_dir.glob('*.xlsx') if not f.name.startswith('~$'))


def _complete_request_file(temp_file_path: Path, dest_dir: Path, logger) -> Path | None:
    """Move a request file from processing_temp/ to the destination folder.

    Uses (n) suffix collision handling. Returns the final destination path or None on failure.
    """
    try:
        os.makedirs(dest_dir, exist_ok=True)
        final_path = unique_dest_path(dest_dir, temp_file_path.name)
        shutil.move(str(temp_file_path), str(final_path))
        return final_path
    except Exception:
        logger.error(f'Failed to move "{temp_file_path.name}" to "{dest_dir}":\n' + traceback.format_exc())
        return None


def _select_sheet_name(file_path: Path, configured_sheet: str | None, logger) -> str:
    """Choose the Excel sheet to read.

    If configured_sheet is non-empty and exists in the workbook, use it.
    Otherwise fall back to the first sheet and log/email a warning if a sheet was requested.
    """
    try:
        xl = pd.ExcelFile(file_path)
        sheets = xl.sheet_names
    except Exception:
        logger.error(f'Failed to inspect Excel sheets in "{file_path.name}".\n' + traceback.format_exc())
        return ''

    if not sheets:
        return ''

    requested = (configured_sheet or '').strip()
    if requested and requested in sheets:
        return requested

    if requested:
        logger.warning(
            f'Configured request sheet "{requested}" not found in "{file_path.name}". '
            f'Available sheets: {", ".join(sheets)}. Falling back to first sheet "{sheets[0]}".'
        )

    return sheets[0]


def _parse_request_file(file_path: Path, main_cfg: ConfigData, logger) -> tuple[list[dict] | None, str | None]:
    """Parse the request Excel file and return a list of raw entry dicts.

    Each dict contains the original row values with normalized keys.
    Returns (entries, None) on success, (None, error_message) on failure.
    """
    configured_sheet = main_cfg.get_value('Requests/request_sheet_name')
    sheet_name = _select_sheet_name(file_path, configured_sheet, logger)
    if not sheet_name:
        return None, f'No readable sheets found in request file "{file_path.name}".'

    try:
        df = pd.read_excel(file_path, sheet_name=sheet_name)
    except Exception:
        err = f'Failed to read request file "{file_path.name}", sheet "{sheet_name}":\n' + traceback.format_exc()
        logger.error(err)
        return None, err

    if df.empty:
        return None, f'Request file "{file_path.name}" sheet "{sheet_name}" contains no rows.'

    # Normalize column names: strip whitespace and lower-case for matching
    df.columns = [str(c).strip().lower().replace(' ', '_') for c in df.columns]

    # Column aliases for backward compatibility / known typos
    column_aliases = {
        'raw_data_source': ['raw_data_folder'],
        'skip_aliquot_validation': ['skip_aliquot_validatoin'],
    }
    for canonical, aliases in column_aliases.items():
        if canonical in df.columns:
            continue
        for alias in aliases:
            if alias in df.columns:
                df.rename(columns={alias: canonical}, inplace=True)
                logger.info(f'Renamed request column "{alias}" to "{canonical}".')
                break

    if 'raw_data_source' not in df.columns:
        err = (
            f'Request file "{file_path.name}" is missing the required "Raw_data_source" column. '
            f'Found columns: {list(df.columns)}.'
        )
        logger.error(err)
        return None, err

    entries = []
    for idx, row in df.iterrows():
        # pandas uses 0-based index; row 1 in Excel is index 0
        excel_row = int(idx) + 2
        entries.append({
            'row_number': excel_row,
            'raw_data_source': str(row.get('raw_data_source', '')).strip() if pd.notna(row.get('raw_data_source')) else '',
            'program_code': str(row.get('program_code', '')).strip() if pd.notna(row.get('program_code')) else '',
            'skip_aliquot_validation': _parse_bool(row.get('skip_aliquot_validation')),
        })

    logger.info(f'Parsed {len(entries)} request entr{"y" if len(entries) == 1 else "ies"} from "{file_path.name}".')
    return entries, None


def _resolve_allowed_raw_data_root(loc_cfg: ConfigData, main_cfg: ConfigData) -> Path | None:
    """Return the raw_data directory that Raw_data_source values must resolve within.

    This is the same directory the alignment step writes its output CSVs to — Raw_data_source is
    documented as pointing to one of those files. Returns None if the studies directory is not
    configured.
    """
    studies_dir = loc_cfg.get_value('Location/mitCopyN_studies_dir')
    if not studies_dir:
        return None
    return Path(studies_dir) / (main_cfg.get_value('Alignment/raw_data_dir') or 'raw_data')


def _resolve_raw_data_source(
    raw_value: str, allowed_root: Path, logger
) -> tuple[Path | None, str | None, str | None]:
    """Resolve a Raw_data_source value to a single CSV path within *allowed_root*.

    Raw_data_source is a value from a lab-submitted request Excel file, i.e. untrusted input from
    outside the trust boundary of this application. It is resolved relative to *allowed_root* when
    not absolute, and rejected outright if it resolves outside *allowed_root* — otherwise a
    submitter could point this at an arbitrary path on the filesystem (e.g. "../../../etc").

    :returns: (csv_path, launch_param, error_message)
        csv_path is None on error; launch_param is '--input_file' or '--input_dir'.
    """
    if not raw_value:
        return None, None, 'Raw_data_source value is empty.'

    source_path = Path(raw_value)
    if not source_path.is_absolute():
        source_path = allowed_root / source_path

    try:
        resolved = source_path.resolve()
        resolved_root = allowed_root.resolve()
    except (OSError, RuntimeError) as e:
        return None, None, f'Failed to resolve Raw_data_source path "{raw_value}": {e}'

    if not resolved.is_relative_to(resolved_root):
        return None, None, (
            f'Raw_data_source "{raw_value}" resolves outside the allowed raw_data directory '
            f'("{allowed_root}"). Please submit a path within raw_data.'
        )

    if not resolved.exists():
        return None, None, f'Raw_data_source path does not exist: "{raw_value}".'

    if resolved.is_file():
        if resolved.suffix.lower() != '.csv':
            return None, None, f'Raw_data_source file is not a CSV: "{raw_value}".'
        return resolved, '--input_file', None

    if resolved.is_dir():
        csv_files = sorted(f for f in resolved.glob('*.csv') if not f.name.startswith('~$'))
        if len(csv_files) == 0:
            return None, None, f'No CSV files found in Raw_data_source directory: "{raw_value}".'
        if len(csv_files) > 1:
            return (
                None, None,
                f'Expected exactly one CSV file in "{raw_value}", but found {len(csv_files)}: '
                f'{", ".join(f.name for f in csv_files)}. '
                f'Please use --input_file and point to the specific CSV file, '
                f'for example: --input_file {csv_files[0]}'
            )
        return csv_files[0], '--input_dir', None

    return None, None, f'Raw_data_source path is neither a file nor a directory: "{raw_value}".'


def _process_request_entry(
    entry: dict,
    main_cfg: ConfigData,
    loc_cfg: ConfigData,
    logger,
) -> RequestEntryRecord:
    """Process a single request entry by resolving its source and delegating to the counts pipeline."""
    row_number = entry['row_number']
    raw_value = entry['raw_data_source']
    program_override = entry['program_code'] or None
    skip_validation = entry['skip_aliquot_validation']

    logger.info(f'Processing request entry {row_number}: Raw_data_source="{raw_value}".')

    # Resolve Raw_data_source to a CSV path, constrained to the configured raw_data directory
    allowed_root = _resolve_allowed_raw_data_root(loc_cfg, main_cfg)
    if allowed_root is None:
        csv_path, launch_param, err = (
            None, '--input_dir', 'Location/mitCopyN_studies_dir is not set in location_config.yaml.'
        )
    else:
        csv_path, launch_param, err = _resolve_raw_data_source(raw_value, allowed_root, logger)
    if err:
        # Build a minimal record so the error is captured and emailed
        record = RequestEntryRecord(
            source_file='N/A',
            provider_name='request',
            row_number=row_number,
            raw_data_source=raw_value,
            program_code_override=program_override,
            skip_aliquot_validation=skip_validation,
        )
        record.launch_param = launch_param or '--input_dir'
        record.launch_value = raw_value
        record.counts_ran = False
        handler = CapturingLogHandler(record)
        logger.addHandler(handler)
        try:
            logger.error(err)
        finally:
            logger.removeHandler(handler)
        return record

    # Delegate all counts processing to the shared helper in mc_copy_number_counts.py
    record = process_counts_input(
        csv_path,
        main_cfg,
        loc_cfg,
        logger,
        launch_param=launch_param,
        launch_value=raw_value,
        program_code_override=program_override,
        skip_aliquot_validation=skip_validation,
    )

    # Enrich the returned FileRecord with request-specific context for the email
    request_record = RequestEntryRecord(
        source_file=record.source_file,
        provider_name='request',
        row_number=row_number,
        raw_data_source=raw_value,
        program_code_override=program_override,
        skip_aliquot_validation=skip_validation,
    )
    # Copy all relevant attributes from the counts record
    for attr in [
        'alignment_output', 'alignment_ok', 'alignment_ran', 'alignment_aliquot_count',
        'counts_ok', 'counts_output', 'counts_outputs', 'counts_aliquot_count', 'counts_ran',
        'db_validation_ok', 'db_validation_skipped', 'program_code', 'program_groups', 'aliquots',
        'warnings', 'errors', 'launch_param', 'launch_value',
    ]:
        setattr(request_record, attr, getattr(record, attr, None))
    return request_record


def process_request_file(
    file_path: Path,
    main_cfg: ConfigData,
    loc_cfg: ConfigData,
    logger,
    dirs: dict,
) -> RequestRecord:
    """Process one request file end-to-end and return its RequestRecord."""
    record = RequestRecord(str(file_path))
    logger.info(f'Starting processing of request file: "{file_path.name}"')

    # Claim the file
    temp_file_path = claim_file(file_path, dirs['processing_temp'], logger, log_label='Request file ')
    if temp_file_path is None:
        record.errors.append(f'Failed to claim request file "{file_path.name}".')
        return record

    try:
        # Parse request entries
        entries, parse_err = _parse_request_file(temp_file_path, main_cfg, logger)
        if parse_err or entries is None:
            if parse_err:
                record.errors.append(parse_err)
            # Move to work on parse/format failure
            _complete_request_file(temp_file_path, dirs['work'], logger)
            return record

        if not entries:
            record.errors.append(f'No entries found in request file "{file_path.name}".')
            _complete_request_file(temp_file_path, dirs['work'], logger)
            return record

        # Process each entry
        any_failed = False
        for entry in entries:
            entry_record = _process_request_entry(entry, main_cfg, loc_cfg, logger)
            record.entries.append(entry_record)
            if entry_record.errors or not entry_record.counts_ok:
                any_failed = True

        # Move the request file to processed or work
        if any_failed:
            logger.warning(
                f'Request file "{file_path.name}" completed with errors; moving to work folder.'
            )
            _complete_request_file(temp_file_path, dirs['work'], logger)
        else:
            logger.info(f'Request file "{file_path.name}" completed successfully; moving to processed folder.')
            _complete_request_file(temp_file_path, dirs['processed'], logger)
            record.processed_successfully = True

    except Exception:
        err = f'Unexpected error processing request file "{file_path.name}":\n' + traceback.format_exc()
        logger.error(err)
        record.errors.append(err)
        _complete_request_file(temp_file_path, dirs['work'], logger)

    return record
