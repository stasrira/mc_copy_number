import os

from dotenv import load_dotenv

from utils.common import send_status_email, initialize_run, build_subject_prefix
import utils.global_const as gc

load_dotenv()

# Process identification shown in the status email subject and header.
PROCESS_LABEL = 'Alignment/Counts'


def main():
    run = initialize_run(gc.MAIN_LOG_NAME, 'mc_copy_number')
    logger = run['logger']
    main_cfg = run['main_cfg']
    loc_cfg = run['loc_cfg']
    log_dir = run['log_dir']
    log_filename = run['log_filename']

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
    send_status_email(
        logger, file_records, os.path.join(log_dir, log_filename), main_cfg,
        subject_prefix=build_subject_prefix(main_cfg, loc_cfg, PROCESS_LABEL),
        process_label=PROCESS_LABEL,
    )

    logger.info('MC Copy Number Processing completed.')


if __name__ == '__main__':
    main()

