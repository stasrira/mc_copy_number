from pathlib import Path
import os
import re
import time
import traceback
import pandas as pd
from jinja2 import Environment, FileSystemLoader

from utils.configuration import ConfigData
from utils.log_utils import setup_logger_common
import utils.global_const as gc


def get_project_root():
    """Returns project root folder."""
    return Path(__file__).parent.parent


def csv_bom_enabled(main_cfg: ConfigData) -> bool:
    """Return whether the UTF-8 BOM feature (``Csv_output/enable_utf8_bom``) is enabled.

    Defaults to ``True`` (enabled) when the key is not present in main_config.yaml.
    """
    val = main_cfg.get_value('Csv_output/enable_utf8_bom')
    return True if val is None else bool(val)


def resolve_csv_write_encoding(df: pd.DataFrame, enable_utf8_bom: bool = True) -> tuple[str, str | None]:
    """Decide the encoding to use when writing *df* to CSV.

    :param enable_utf8_bom: When False (the ``Csv_output/enable_utf8_bom`` config flag is off),
                            always returns plain ``'utf-8'`` — see :func:`csv_bom_enabled`.

    Returns ``(encoding, example)``:

    * *encoding* is ``'utf-8-sig'`` (adds a UTF-8 BOM) only when *enable_utf8_bom* is True and the
      data actually contains non-ASCII characters (in column names, the index, or any string
      cell), so Excel renders those correctly instead of misreading the file as the system
      codepage. Plain ``'utf-8'`` (no BOM) is returned otherwise — the BOM is unnecessary for
      pure-ASCII data and can otherwise trip up tools that aren't BOM-aware, e.g. base R's
      ``read.csv()`` mangles the first column name unless ``fileEncoding='UTF-8-BOM'`` is passed
      explicitly (``readr::read_csv()`` and pandas handle it automatically either way).
    * *example* is the first non-ASCII value found (handy for a log message explaining the
      choice), or ``None`` when *encoding* is ``'utf-8'``.
    """
    if not enable_utf8_bom:
        return 'utf-8', None

    def _non_ascii(value):
        return value if isinstance(value, str) and not value.isascii() else None

    for v in list(df.columns) + list(df.index):
        hit = _non_ascii(v)
        if hit is not None:
            return 'utf-8-sig', hit
    for col in df.select_dtypes(include='object').columns:
        for v in df[col]:
            hit = _non_ascii(v)
            if hit is not None:
                return 'utf-8-sig', hit
    return 'utf-8', None


def load_configs(project_root: Path | None = None) -> tuple[ConfigData, ConfigData]:
    """Load main and location config files.

    :param project_root: optional project root; auto-detected if omitted.
    :returns: (main_cfg, loc_cfg) tuple.
    """
    if project_root is None:
        project_root = get_project_root()
    main_cfg = ConfigData(project_root / gc.CONFIG_FILE_MAIN)
    loc_cfg = ConfigData(project_root / gc.CONFIG_FILE_LOCATION)
    return main_cfg, loc_cfg


def resolve_log_dir(loc_cfg: ConfigData, project_root: Path | None = None) -> str:
    """Resolve the log directory from location config, defaulting to project_root/logs."""
    if project_root is None:
        project_root = get_project_root()
    log_dir = loc_cfg.get_value('Location/logs') or 'logs'
    if not os.path.isabs(log_dir):
        log_dir = str(project_root / log_dir)
    return log_dir


def build_subject_prefix(main_cfg: ConfigData, loc_cfg: ConfigData, process_label: str) -> str:
    """Build the status-email subject prefix: "[env_name] base_prefix - process_label".

    ``[env_name]`` is taken from ``Location/environment_name`` and included only when set.
    """
    base_prefix = main_cfg.get_value('Email/email_subject_prefix') or 'MC Copy Number'
    env_name = loc_cfg.get_value('Location/environment_name')
    env_tag = f'[{env_name}] ' if env_name else ''
    return f'{env_tag}{base_prefix} - {process_label}'


def resolve_studies_dir(loc_cfg: ConfigData) -> Path | None:
    """Resolve ``Location/mitCopyN_studies_dir`` from location config.

    This directory is required by the alignment, counts and request-processing entry points, each
    of which reacts differently when it's missing (log-and-abort, raise, or fail silently) — so
    this only resolves the value; the missing-value branch stays with the caller.

    :returns: the studies directory as a Path, or None if the key is not set.
    """
    studies_dir = loc_cfg.get_value('Location/mitCopyN_studies_dir')
    return Path(studies_dir) if studies_dir else None


def config_subfolder(cfg: ConfigData, key: str, default: str) -> str:
    """Resolve a subfolder name from *cfg*, falling back to *default* when unset.

    Centralizes the ``cfg.get_value(key) or default`` idiom repeated throughout the pipeline's
    folder-lifecycle boilerplate (ready/temp/processed/reprocess subfolder names, output dirs).
    """
    return cfg.get_value(key) or default


def initialize_run(log_name: str, log_filename_prefix: str):
    """Load configs, resolve the log directory, and initialize the run logger.

    This is the common bootstrapping routine used by all entry-point scripts.

    :param log_name: logger name passed to setup_logger_common (e.g. gc.MAIN_LOG_NAME).
    :param log_filename_prefix: prefix for the timestamped log file name.
    :returns: dict with project_root, main_cfg, loc_cfg, log_dir, log_filename, logger.
    """
    project_root = get_project_root()
    main_cfg, loc_cfg = load_configs(project_root)

    log_dir = resolve_log_dir(loc_cfg, project_root)
    log_level = main_cfg.get_value('Logging/log_level') or 'INFO'
    mirror_to_stdout = main_cfg.get_value('Logging/mirror_to_stdout')
    if mirror_to_stdout is None:
        mirror_to_stdout = True

    log_filename = f'{log_filename_prefix}_{time.strftime("%Y%m%d_%H%M%S")}.log'
    log_obj = setup_logger_common(log_name, log_level, log_dir, log_filename, mirror_to_stdout)

    return {
        'project_root': project_root,
        'main_cfg': main_cfg,
        'loc_cfg': loc_cfg,
        'log_dir': log_dir,
        'log_filename': log_filename,
        'logger': log_obj['logger'],
    }


def file_exists(fn):
    try:
        with open(fn, 'r'):
            return True
    except IOError:
        return False


def get_environment_variable(var_name):
    if var_name:
        return os.environ.get(var_name.strip())
    return None


def claim_file(file_path: Path, temp_dir: Path, logger, log_label: str = '') -> Path | None:
    """Atomically claim *file_path* by ``os.rename``-ing it into *temp_dir*.

    This is the shared "ready/ -> temp/" claim step of the folder lifecycle pattern (see
    CLAUDE.md) used by both the alignment file processor and the request-file processor: an
    atomic rename is how concurrent/repeated runs avoid double-processing the same file.
    ``FileNotFoundError`` means another process already claimed it; ``PermissionError`` means the
    file is open elsewhere (e.g. in Excel) — both are expected, transient conditions rather than
    real errors, so they're logged at WARNING and the file is simply left for a later run.

    :param log_label: optional prefix for log messages, e.g. ``'[ProviderA] '`` or ``'Request file '``.
    :returns: the claimed path inside *temp_dir* on success, ``None`` on failure (file stays put).
    """
    temp_path = temp_dir / file_path.name
    try:
        os.makedirs(temp_dir, exist_ok=True)
        os.rename(file_path, temp_path)
        logger.info(f'{log_label}Claimed "{file_path.name}" -> "{temp_dir}".')
        return temp_path
    except FileNotFoundError:
        logger.warning(f'{log_label}"{file_path.name}" was already claimed by another process. Skipping.')
        return None
    except PermissionError:
        logger.warning(
            f'{log_label}"{file_path.name}" is locked by another process (e.g. open in Excel) and cannot '
            f'be moved. It remains in place and will be attempted again on the next run.'
        )
        return None
    except Exception:
        logger.error(f'{log_label}Failed to claim "{file_path.name}":\n' + traceback.format_exc())
        return None


def sanitize_output_name(name: str) -> str:
    """Collapse whitespace in *name* to single underscores, for building pipeline-generated
    output file/folder names (e.g. the raw_data CSV stem derived from a provider's source file
    name).

    Only ever applied to names used for output the pipeline itself creates — never to the
    original source file name, which keeps its own name unchanged when moved to processed/ or
    reprocess/ (see unique_dest_path, used for that instead).
    """
    return re.sub(r'\s+', '_', name.strip())


def unique_dest_path(dest_dir: Path, file_name: str) -> Path:
    """Return a destination path that does not conflict with existing files.

    If dest_dir/file_name already exists, appends "(n)" before the suffix, incrementing n from 1
    until a free name is found. E.g. "file.xlsx" -> "file(1).xlsx" -> "file(2).xlsx" ...

    Used alongside :func:`claim_file` when completing the folder lifecycle pattern (moving a
    claimed file out to its final processed/reprocess destination) by both the alignment file
    processor and the request-file processor.
    """
    candidate = dest_dir / file_name
    if not candidate.exists():
        return candidate
    stem = Path(file_name).stem
    suffix = Path(file_name).suffix
    n = 1
    while True:
        candidate = dest_dir / f'{stem}({n}){suffix}'
        if not candidate.exists():
            return candidate
        n += 1


def populate_email_template(template_name: str, template_feeder: dict, templates_dir: Path) -> str:
    """Render a Jinja2 template from templates_dir with template_feeder exposed as 'process'."""
    file_loader = FileSystemLoader(str(templates_dir))
    env = Environment(loader=file_loader)
    env.trim_blocks = True
    env.lstrip_blocks = True
    env.rstrip_blocks = True
    template = env.get_template(template_name)
    return template.render(process=template_feeder)


def clean_email_body(email_body: str) -> str:
    """Strip newlines from email HTML to prevent Outlook rendering issues."""
    return email_body.replace('\r', '').replace('\n', '')


def _bom_note(record) -> str | None:
    """Build one aggregated note about *record*'s BOM-affected output file(s), if any.

    Multiple output files (e.g. one per program in a multi-program Counts run) can share the
    same file name, differing only by parent folder — a separate warning per file was confusing
    to tell apart. This produces a single explanatory note instead; the affected file(s) are
    marked with "*" next to their path in the email body (see file_status.html).
    """
    if not record.bom_applied_paths:
        return None
    count = len(record.bom_applied_paths)
    if count == 1:
        return (
            f'The output file marked with "*" contains non-ASCII character(s) '
            f'(e.g. "{record.bom_example}") and was saved with a UTF-8 BOM so it displays '
            f'correctly when opened directly in Excel.'
        )
    return (
        f'The {count} output files marked with "*" contain non-ASCII character(s) '
        f'(e.g. "{record.bom_example}") and were saved with a UTF-8 BOM so they display '
        f'correctly when opened directly in Excel.'
    )


def _build_entry_feeder(record) -> dict:
    """Build the per-file/entry Jinja2 feeder dict shared by the pipeline and request status emails.

    :param record: a FileRecord or RequestEntryRecord (utils/issue_collector.py) — both expose the
                    same alignment/counts/db-validation attributes rendered by file_status.html.
    """
    bom_note = _bom_note(record)
    return {
        'source_file':             record.source_file,
        'provider_name':           record.provider_name,
        'alignment_ok':            record.alignment_ok,
        'alignment_ran':           record.alignment_ran,
        'alignment_output':        str(record.alignment_output) if record.alignment_output else '',
        'alignment_aliquot_count': record.alignment_aliquot_count,
        'counts_ok':               record.counts_ok,
        'counts_ran':              record.counts_ran,
        'counts_skipped':          record.alignment_output is None,
        'db_validation_ok':        record.db_validation_ok,
        'db_validation_skipped':   getattr(record, 'db_validation_skipped', False),
        'program_code':            record.program_code,
        'program_groups':          record.program_groups,
        'counts_outputs':          record.counts_outputs,
        'launch_param':            record.launch_param,
        'launch_value':            record.launch_value,
        'counts_output':           str(record.counts_output) if record.counts_output else '',
        'counts_aliquot_count':    record.counts_aliquot_count,
        'aliquots':                record.aliquots,
        'warnings':                record.warnings + [bom_note] if bom_note else record.warnings,
        'errors':                  record.errors,
        'bom_applied_paths':       set(record.bom_applied_paths),
    }


def _split_emails(value: str | None) -> list[str]:
    """Split a comma-separated env var value into a list of trimmed, non-empty addresses."""
    if not value:
        return []
    return [addr.strip() for addr in value.split(',') if addr.strip()]


def _resolve_email_recipients(main_cfg: ConfigData, has_results: bool) -> list[str]:
    """Build the status-email recipient list from ``MC_EMAIL_TO``, optionally extended with
    ``MC_EMAIL_ADDITIONAL_TO`` when ``Email/include_additional_emails`` is on.

    The additional recipients are only added when there was actually something to process
    (*has_results* — i.e. at least one file/entry was attempted, regardless of whether it
    succeeded or errored). A run that found nothing to process at all (e.g. an empty ``ready/``
    folder) goes to the main recipients only, even with ``Email/include_additional_emails`` on —
    but a run where files were attempted and failed still reaches the additional recipients, since
    that's exactly the kind of thing they'd want to be aware of.

    Recipient addresses live in env vars rather than main_config.yaml since this file is checked
    into git and email addresses are environment-specific/personal data.
    """
    recipients = _split_emails(get_environment_variable('MC_EMAIL_TO'))
    if has_results and main_cfg.get_value('Email/include_additional_emails'):
        recipients += _split_emails(get_environment_variable('MC_EMAIL_ADDITIONAL_TO'))
    return recipients


def _send_email_if_enabled(logger, main_cfg, subject: str, email_body: str, kind_label: str,
                            has_results: bool) -> None:
    """Send *email_body* via SMTP if ``Email/send_emails`` is on; otherwise just log that it's off.

    :param kind_label: prefix for the log line (e.g. "Status" / "Request status").
    :param has_results: whether the run produced any results, per ``_resolve_email_recipients``.
    """
    if main_cfg.get_value('Email/send_emails'):
        from utils.send_email import send_email

        email_from = get_environment_variable('MC_EMAIL_FROM')
        emails_to = _resolve_email_recipients(main_cfg, has_results)
        send_email(emails_to, subject, email_body, email_from=email_from)
        logger.info(f'{kind_label} email sent to: {emails_to}')
    else:
        logger.info(f'{kind_label} email sending is disabled in config.')


def send_status_email(logger, file_records, log_filename, main_cfg, subject_prefix: str = None,
                       process_label: str = None):
    """Build and send a processing status email.

    :param logger: application logger
    :param file_records: list of FileRecord objects
    :param log_filename: log filename to include in the email body
    :param main_cfg: ConfigData instance for the main config
    :param subject_prefix: overrides Email/email_subject_prefix when provided
    :param process_label: entry-point process identification (e.g. "Alignment/Counts"),
                          shown in the email header
    """
    try:
        project_root = get_project_root()
        templates_dir = project_root / 'templates'

        file_sections = []
        for record in file_records:
            template_feeder = _build_entry_feeder(record)
            section_html = populate_email_template('file_status.html', template_feeder, templates_dir)
            file_sections.append(section_html)

        files_with_errors = sum(1 for r in file_records if r.errors)
        files_with_warnings = sum(1 for r in file_records if r.warnings or r.bom_applied_paths)

        final_feeder = {
            'run_time':           time.strftime('%Y-%m-%d %H:%M:%S'),
            'log_file':           log_filename,
            'total_files':        len(file_records),
            'files_with_errors':  files_with_errors,
            'files_with_warnings': files_with_warnings,
            'file_sections':      file_sections,
            'process_label':      process_label,
        }
        email_body = populate_email_template('pipeline_status.html', final_feeder, templates_dir)
        email_body = clean_email_body(email_body)

        prefix = subject_prefix or main_cfg.get_value('Email/email_subject_prefix') or 'MC Copy Number Processing'
        if files_with_errors > 0:
            subject = f'{prefix} - ERRORS PRESENT ({files_with_errors}) - {len(file_records)} file(s) processed'
        elif files_with_warnings > 0:
            subject = f'{prefix} - Warnings present ({files_with_warnings}) - {len(file_records)} file(s) processed'
        elif len(file_records) == 0:
            subject = f'{prefix} - no files processed'
        else:
            subject = f'{prefix} - {len(file_records)} file(s) processed successfully'

        _send_email_if_enabled(logger, main_cfg, subject, email_body, 'Status',
                                has_results=len(file_records) > 0)

    except Exception:
        logger.error('Failed to send status email:\n' + traceback.format_exc())


def send_request_status_email(logger, request_record, log_filename, main_cfg, subject_prefix: str = None,
                               process_label: str = None):
    """Build and send a status email for a single request file.

    :param logger: application logger
    :param request_record: RequestRecord object
    :param log_filename: log filename to include in the email body
    :param main_cfg: ConfigData instance for the main config
    :param subject_prefix: overrides Email/email_subject_prefix when provided
    :param process_label: entry-point process identification (e.g. "Request/Counts"),
                          shown in the email header
    """
    try:
        project_root = get_project_root()
        templates_dir = project_root / 'templates'

        # Render each entry using the existing file_status.html template
        entry_sections = []
        for entry in request_record.entries:
            template_feeder = _build_entry_feeder(entry)
            template_feeder.update({
                # Request-specific context for the template
                'raw_data_source':         getattr(entry, 'raw_data_source', ''),
                'program_code_override':   getattr(entry, 'program_code_override', ''),
                'skip_aliquot_validation': getattr(entry, 'skip_aliquot_validation', False),
                'row_number':              getattr(entry, 'row_number', ''),
            })
            section_html = populate_email_template('file_status.html', template_feeder, templates_dir)
            entry_sections.append(section_html)

        final_feeder = {
            'run_time':                 time.strftime('%Y-%m-%d %H:%M:%S'),
            'log_file':                 log_filename,
            'request_file':             request_record.request_file,
            'processed_successfully':   request_record.processed_successfully,
            'has_errors':               request_record.has_errors,
            'has_warnings':             request_record.has_warnings,
            'errors':                   request_record.errors,
            'warnings':                 request_record.warnings,
            'entries':                  request_record.entries,
            'entry_sections':           entry_sections,
            'total_entries':            len(request_record.entries),
            'process_label':            process_label,
        }
        email_body = populate_email_template('request_status.html', final_feeder, templates_dir)
        email_body = clean_email_body(email_body)

        prefix = subject_prefix or main_cfg.get_value('Email/email_subject_prefix') or 'MC Copy Number Processing'
        if request_record.has_errors:
            subject = f'{prefix} - Request ERRORS - "{Path(request_record.request_file).name}"'
        elif request_record.has_warnings:
            subject = f'{prefix} - Request warnings - "{Path(request_record.request_file).name}"'
        elif len(request_record.entries) == 0:
            subject = f'{prefix} - Request no entries - "{Path(request_record.request_file).name}"'
        else:
            subject = f'{prefix} - Request completed successfully - "{Path(request_record.request_file).name}"'

        _send_email_if_enabled(logger, main_cfg, subject, email_body, 'Request status',
                                has_results=len(request_record.entries) > 0)

    except Exception:
        logger.error('Failed to send request status email:\n' + traceback.format_exc())
