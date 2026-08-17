from utils.configuration import ConfigData
from utils.log_utils import setup_logger_common
import utils.global_const as gc
from utils.common import get_project_root, clean_email_body, send_status_email
from dotenv import load_dotenv
import time
import os
import traceback

load_dotenv()


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

    # Step 1 - Alignment (includes DB aliquot validation when configured)
    from mc_copy_number_alignment import run_alignment
    file_records = run_alignment(logger)

    # Step 2 - Counts (only when allowed and all validations passed)
    from mc_copy_number_counts import run_counts
    allow_counts = main_cfg.get_value('Alignment/allow_automated_counts_processing')
    validate_enabled = main_cfg.get_value('Alignment/validate_aliquots_against_db')

    successful_records = [r for r in file_records if r.alignment_ok]
    if not successful_records:
        logger.info('No successfully aligned files — skipping Counts step.')
    elif not allow_counts:
        logger.info(
            'Automated Counts processing is disabled (allow_automated_counts_processing=False). '
            'Skipping Counts step.'
        )
        for r in file_records:
            r.counts_ran = False
    elif validate_enabled:
        # Only run counts for files that passed DB validation
        failed_validation = [r for r in successful_records if not r.db_validation_ok]
        validated_records = [r for r in file_records if r.alignment_ok and r.db_validation_ok]
        if failed_validation:
            logger.warning(
                f'Counts processing will be attempted for {len(validated_records)} of '
                f'{len(successful_records)} successfully aligned file(s) '
                f'({len(failed_validation)} failed DB aliquot validation — all aliquots in a file '
                f'must pass validation before Counts processing can proceed for that file).'
            )
        # Mark records that should not run counts
        for r in file_records:
            if r.alignment_ok and not r.db_validation_ok:
                r.counts_ran = False
        # Run counts only for validated records (run_counts skips alignment_output=None)
        if validated_records:
            run_counts(logger, validated_records)
        else:
            logger.warning('No files passed DB validation — Counts step skipped entirely.')
    else:
        run_counts(logger, file_records)

    # Step 3 - Send status email
    send_status_email(logger, file_records, os.path.join(log_dir, log_filename), main_cfg)

    logger.info('MC Copy Number Processing completed.')


if __name__ == '__main__':
    main()

