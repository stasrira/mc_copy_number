import os
import sys
import logging


def setup_logger_common(lg_name, lg_level, log_path, filename, mirror_to_stdout=True):
    os.makedirs(log_path, exist_ok=True)
    log_file = os.path.join(log_path, filename)

    if not lg_name:
        lg_name = __name__

    mlog = logging.getLogger(lg_name)

    lev = getattr(logging, lg_level, None)
    if not isinstance(lev, int):
        print(f'Invalid log level "{lg_level}", falling back to INFO.', file=sys.stderr)
        lev = logging.INFO

    mlog.setLevel(lev)

    handler = logging.FileHandler(log_file)
    handler.setLevel(lev)

    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)

    mlog.addHandler(handler)

    console_handler = None
    if mirror_to_stdout:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(lev)
        console_handler.setFormatter(formatter)
        mlog.addHandler(console_handler)

    return {'logger': mlog, 'handler': handler, 'console_handler': console_handler}


def deactivate_logger_common(logger, handler, console_handler=None):
    logger.removeHandler(handler)
    if console_handler is not None:
        logger.removeHandler(console_handler)
