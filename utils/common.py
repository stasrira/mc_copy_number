from pathlib import Path
import os
import time
import traceback
from jinja2 import Environment, FileSystemLoader


def get_project_root():
    """Returns project root folder."""
    return Path(__file__).parent.parent


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


def send_status_email(logger, file_records, log_filename, main_cfg, subject_prefix: str = None):
    """Build and send a processing status email.

    :param logger: application logger
    :param file_records: list of FileRecord objects
    :param log_filename: log filename to include in the email body
    :param main_cfg: ConfigData instance for the main config
    :param subject_prefix: overrides Email/email_subject_prefix when provided
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
