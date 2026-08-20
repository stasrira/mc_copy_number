"""mc_copy_number_requests.py

Entry point for processing request Excel files.

Scans the configured requests directory, picks up *.xlsx files from the ready
sub-folder, processes each entry, and sends a status email per request file.
"""

import os
import time
import traceback
from pathlib import Path

from dotenv import load_dotenv

from requests.request_processor import _resolve_requests_dirs, _discover_request_files, process_request_file
from utils.common import get_project_root, send_request_status_email
from utils.configuration import ConfigData
from utils.log_utils import setup_logger_common
import utils.global_const as gc

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

    log_filename = 'requests_' + time.strftime('%Y%m%d_%H%M%S') + '.log'
    log_obj = setup_logger_common(gc.REQUEST_LOG_NAME, log_level, log_dir, log_filename, mirror_to_stdout)
    logger = log_obj['logger']

    logger.info('=== MC Copy Number Requests processing started ===')

    try:
        dirs = _resolve_requests_dirs(loc_cfg, main_cfg, project_root)
    except ValueError as e:
        logger.critical(str(e))
        logger.info('=== MC Copy Number Requests processing finished ===')
        return

    logger.info(f'Requests directory: {dirs["requests_dir"]}')
    logger.info(f'Ready folder:       {dirs["ready"]}')

    request_files = _discover_request_files(dirs['ready'])
    if not request_files:
        logger.info('No request files found in ready folder. Nothing to process.')
        logger.info('=== MC Copy Number Requests processing finished ===')
        return

    logger.info('Discovered {} request file(s): {}'.format(len(request_files), ','.join([file_path.name for file_path in request_files])))

    for file_path in request_files:
        request_record = process_request_file(file_path, main_cfg, loc_cfg, logger, dirs)

        # Send one status email per request file
        subject_prefix = (
            f'{main_cfg.get_value("Email/email_subject_prefix") or "MC Copy Number"} - Requests'
        )
        send_request_status_email(
            logger,
            request_record,
            os.path.join(log_dir, log_filename),
            main_cfg,
            subject_prefix=subject_prefix,
        )

    logger.info('=== MC Copy Number Requests processing finished ===')


if __name__ == '__main__':
    main()
