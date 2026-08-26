"""mc_copy_number_requests.py

Entry point for processing request Excel files.

Scans the configured requests directory, picks up *.xlsx files from the ready
sub-folder, processes each entry, and sends a status email per request file.
"""

import os

from dotenv import load_dotenv

from requests.request_processor import _resolve_requests_dirs, _discover_request_files, process_request_file
from utils.common import send_request_status_email, initialize_run, build_subject_prefix
import utils.global_const as gc

load_dotenv()

# Process identification shown in the status email subject and header.
PROCESS_LABEL = 'Request/Counts'


def main():
    run = initialize_run(gc.REQUEST_LOG_NAME, 'requests')
    logger = run['logger']
    main_cfg = run['main_cfg']
    loc_cfg = run['loc_cfg']
    log_dir = run['log_dir']
    log_filename = run['log_filename']
    project_root = run['project_root']

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

    logger.info(f'Discovered {len(request_files)} request file(s): {", ".join(f.name for f in request_files)}')

    for file_path in request_files:
        request_record = process_request_file(file_path, main_cfg, loc_cfg, logger, dirs)

        # Send one status email per request file
        subject_prefix = build_subject_prefix(main_cfg, loc_cfg, PROCESS_LABEL)
        send_request_status_email(
            logger,
            request_record,
            os.path.join(log_dir, log_filename),
            main_cfg,
            subject_prefix=subject_prefix,
            process_label=PROCESS_LABEL,
        )

    logger.info('=== MC Copy Number Requests processing finished ===')


if __name__ == '__main__':
    main()
