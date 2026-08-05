from utils.configuration import ConfigData
from utils.log_utils import setup_logger_common
import utils.global_const as gc
from utils.common import get_project_root
from dotenv import load_dotenv
import time
import os

load_dotenv()


def main():
    project_root = get_project_root()

    # Load main config
    main_cfg = ConfigData(project_root / gc.CONFIG_FILE_MAIN)
    loc_cfg = ConfigData(project_root / gc.CONFIG_FILE_LOCATION)

    # Resolve log directory
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

    # Set runtime globals
    gc.APP_LOG_DIR = log_dir
    gc.INPUT_DATA_DIR = loc_cfg.get_value('Location/input_data') or ''
    gc.OUTPUT_DATA_DIR = loc_cfg.get_value('Location/output_data') or ''

    logger.info('MC Copy Number Processing started.')
    logger.info(f'Input data directory: {gc.INPUT_DATA_DIR}')
    logger.info(f'Output data directory: {gc.OUTPUT_DATA_DIR}')

    # TODO: main processing logic will go here

    logger.info('MC Copy Number Processing completed.')


if __name__ == '__main__':
    main()
