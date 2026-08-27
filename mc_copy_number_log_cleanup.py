"""mc_copy_number_log_cleanup.py

Deletes log files older than the configured retention period (``LogCleanup/retention_days`` in
main_config.yaml, default 21 days) from the logs directory (``Location/logs``).

Meant to run on its own schedule — e.g. a weekly scheduled task — separate from the pipeline
entry points; see run_mc_copy_number_log_cleanup.sh. Uses its own identifiable log file
(``log_cleanup_<timestamp>.log``) rather than piggybacking on another entry point's log. Sends its
own status email (``Email/send_emails`` gated, like every other entry point) listing every log
file that was deleted.
"""
import os
import traceback
from pathlib import Path

from dotenv import load_dotenv

from utils.common import (
    get_project_root, load_configs, resolve_log_dir, initialize_run, build_subject_prefix,
    send_log_cleanup_status_email,
)
from utils.log_cleanup import cleanup_old_logs, LogCleanupResult
import utils.global_const as gc

load_dotenv()

# Process identification shown in the status email subject and header.
PROCESS_LABEL = 'Log Cleanup'


def _resolve_retention_days(main_cfg) -> int:
    retention_days = main_cfg.get_value('LogCleanup/retention_days')
    if retention_days is None:
        retention_days = gc.DEFAULT_LOG_CLEANUP_RETENTION_DAYS
    return int(retention_days)


def run_log_cleanup(logger):
    project_root = get_project_root()
    main_cfg, loc_cfg = load_configs(project_root)

    log_dir = resolve_log_dir(loc_cfg, project_root)
    retention_days = _resolve_retention_days(main_cfg)

    logger.info(f'Log directory   : {log_dir}')
    logger.info(f'Retention period: {retention_days} day(s)')

    result = cleanup_old_logs(Path(log_dir), retention_days, logger)
    logger.info(
        f'Log cleanup complete. Scanned {result.scanned} file(s), deleted {result.deleted_count} '
        f'(freed {result.bytes_freed} bytes), {result.error_count} error(s).'
    )
    return result


def main():
    run = initialize_run(gc.LOG_CLEANUP_LOG_NAME, 'log_cleanup')
    logger = run['logger']
    main_cfg = run['main_cfg']
    loc_cfg = run['loc_cfg']
    log_dir = run['log_dir']
    log_filename = run['log_filename']

    logger.info('=== MC Copy Number Log Cleanup started ===')
    result = LogCleanupResult()
    try:
        result = run_log_cleanup(logger)
    except Exception:
        logger.critical('Unexpected error during log cleanup:\n' + traceback.format_exc())

    send_log_cleanup_status_email(
        logger, result, os.path.join(log_dir, log_filename), main_cfg,
        subject_prefix=build_subject_prefix(main_cfg, loc_cfg, PROCESS_LABEL),
        process_label=PROCESS_LABEL, log_dir=log_dir, retention_days=_resolve_retention_days(main_cfg),
    )
    logger.info('=== MC Copy Number Log Cleanup finished ===')


if __name__ == '__main__':
    main()
