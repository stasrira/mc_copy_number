from utils.configuration import ConfigData
from utils.log_utils import setup_logger_common
import utils.global_const as gc
from utils.common import get_project_root, populate_email_template, clean_email_body
from utils.send_email import send_email
from dotenv import load_dotenv
import time
import os
import traceback

load_dotenv()


def _send_status_email(logger, file_records, log_filename, project_root, main_cfg):
    """Build and send status email for the processing run."""
    try:
        templates_dir = project_root / 'templates'
        
        # Build per-file sections
        file_sections = []
        for record in file_records:
            template_feeder = {
                'source_file': record.source_file,
                'provider_name': record.provider_name,
                'alignment_ok': record.alignment_ok,
                'alignment_output': str(record.alignment_output) if record.alignment_output else '',
                'counts_ok': record.counts_ok,
                'counts_skipped': record.alignment_output is None,
                'counts_output': str(record.counts_output) if record.counts_output else '',
                'warnings': record.warnings,
                'errors': record.errors,
            }
            section_html = populate_email_template('file_status.html', template_feeder, templates_dir)
            file_sections.append(section_html)
        
        # Build final email body
        files_with_errors = sum(1 for r in file_records if r.errors)
        files_with_warnings = sum(1 for r in file_records if r.warnings)
        
        final_feeder = {
            'run_time': time.strftime('%Y-%m-%d %H:%M:%S'),
            'log_file': log_filename,
            'total_files': len(file_records),
            'files_with_errors': files_with_errors,
            'files_with_warnings': files_with_warnings,
            'file_sections': file_sections,
        }
        email_body = populate_email_template('pipeline_status.html', final_feeder, templates_dir)
        email_body = clean_email_body(email_body)
        
        # Build subject line
        prefix = main_cfg.get_value('Email/email_subject_prefix') or 'MC Copy Number Processing'
        if files_with_errors > 0:
            subject = f'{prefix} - ERRORS PRESENT ({files_with_errors}) - {len(file_records)} file(s) processed'
        elif files_with_warnings > 0:
            subject = f'{prefix} - Warnings present ({files_with_warnings}) - {len(file_records)} file(s) processed'
        elif len(file_records) == 0:
            subject = f'{prefix} - no files processed'
        else:
            subject = f'{prefix} - {len(file_records)} file(s) processed successfully'
        
        # Send email if configured
        if main_cfg.get_value('Email/send_emails'):
            email_from = main_cfg.get_value('Email/default_from_email')
            emails_to = main_cfg.get_value('Email/sent_to_emails')
            send_email(emails_to, subject, email_body, email_from=email_from)
            logger.info(f'Status email sent to: {emails_to}')
        else:
            logger.info('Status email sending is disabled in config.')
    
    except Exception:
        logger.error('Failed to send status email:\n' + traceback.format_exc())


def main():
    project_root = get_project_root()

    main_cfg = ConfigData(project_root / gc.CONFIG_FILE_MAIN)
    loc_cfg = ConfigData(project_root / gc.CONFIG_FILE_LOCATION)

    log_dir = loc_cfg.get_value('Location/logs') or 'logs'
    if not os.path.isabs(log_dir):
        log_dir = str(project_root / log_dir)

    log_level = main_cfg.get_value('Logging/log_level') or 'INFO'
    mirror_to_stdout = main_cfg.get_value('Logging/mirror_to_stdout')
    if mirror_to_stdout is None:
        mirror_to_stdout = True

    log_filename = 'mc_copy_number_' + time.strftime('%Y%m%d_%H%M%S') + '.log'
    log_obj = setup_logger_common(gc.MAIN_LOG_NAME, log_level, log_dir, log_filename, mirror_to_stdout)
    logger = log_obj['logger']

    logger.info('MC Copy Number Processing started.')

    # Step 1 - Alignment
    from mc_copy_number_alignment import run_alignment
    file_records = run_alignment(logger)

    # Step 2 - Counts
    from mc_copy_number_counts import run_counts
    run_counts(logger, file_records)

    # Step 3 - Send status email
    _send_status_email(logger, file_records, log_filename, project_root, main_cfg)

    logger.info('MC Copy Number Processing completed.')


if __name__ == '__main__':
    main()

