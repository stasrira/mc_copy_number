from pathlib import Path
import os
import time
import traceback
from jinja2 import Environment, FileSystemLoader

from utils.configuration import ConfigData
from utils.log_utils import setup_logger_common
import utils.global_const as gc


def get_project_root():
    """Returns project root folder."""
    return Path(__file__).parent.parent


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
        from utils.send_email import send_email

        project_root = get_project_root()
        templates_dir = project_root / 'templates'

        file_sections = []
        for record in file_records:
            template_feeder = {
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
                'warnings':                record.warnings,
                'errors':                  record.errors,
            }
            section_html = populate_email_template('file_status.html', template_feeder, templates_dir)
            file_sections.append(section_html)

        files_with_errors = sum(1 for r in file_records if r.errors)
        files_with_warnings = sum(1 for r in file_records if r.warnings)

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

        if main_cfg.get_value('Email/send_emails'):
            email_from = main_cfg.get_value('Email/default_from_email')
            emails_to = main_cfg.get_value('Email/sent_to_emails')
            send_email(emails_to, subject, email_body, email_from=email_from)
            logger.info(f'Status email sent to: {emails_to}')
        else:
            logger.info('Status email sending is disabled in config.')

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
        from utils.send_email import send_email

        project_root = get_project_root()
        templates_dir = project_root / 'templates'

        # Render each entry using the existing file_status.html template
        entry_sections = []
        for entry in request_record.entries:
            template_feeder = {
                'source_file':             entry.source_file,
                'provider_name':           entry.provider_name,
                'alignment_ok':            entry.alignment_ok,
                'alignment_ran':           entry.alignment_ran,
                'alignment_output':        str(entry.alignment_output) if entry.alignment_output else '',
                'alignment_aliquot_count': entry.alignment_aliquot_count,
                'counts_ok':               entry.counts_ok,
                'counts_ran':              entry.counts_ran,
                'counts_skipped':          entry.alignment_output is None,
                'db_validation_ok':        entry.db_validation_ok,
                'db_validation_skipped':   getattr(entry, 'db_validation_skipped', False),
                'program_code':            entry.program_code,
                'program_groups':          entry.program_groups,
                'counts_outputs':          entry.counts_outputs,
                'launch_param':            entry.launch_param,
                'launch_value':            entry.launch_value,
                'counts_output':           str(entry.counts_output) if entry.counts_output else '',
                'counts_aliquot_count':    entry.counts_aliquot_count,
                'aliquots':                entry.aliquots,
                'warnings':                entry.warnings,
                'errors':                  entry.errors,
                # Request-specific context for the template
                'raw_data_source':         getattr(entry, 'raw_data_source', ''),
                'program_code_override':   getattr(entry, 'program_code_override', ''),
                'skip_aliquot_validation': getattr(entry, 'skip_aliquot_validation', False),
                'row_number':              getattr(entry, 'row_number', ''),
            }
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

        if main_cfg.get_value('Email/send_emails'):
            email_from = main_cfg.get_value('Email/default_from_email')
            emails_to = main_cfg.get_value('Email/sent_to_emails')
            send_email(emails_to, subject, email_body, email_from=email_from)
            logger.info(f'Request status email sent to: {emails_to}')
        else:
            logger.info('Request status email sending is disabled in config.')

    except Exception:
        logger.error('Failed to send request status email:\n' + traceback.format_exc())
