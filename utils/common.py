from pathlib import Path
import os


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
