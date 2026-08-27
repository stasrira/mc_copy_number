import logging

import yaml

# Used before an app logger exists (ConfigData is instantiated by initialize_run() ahead of
# setup_logger_common(), since the log directory itself comes from config) — with no handler
# configured for this logger name, logging's lastResort handler still prints these to stderr in
# production, but pytest's logging plugin swallows them during tests (unlike a raw print(), which
# always writes straight to the real stream).
_logger = logging.getLogger(__name__)


class ConfigData:

    def __init__(self, cfg_path):
        self.loaded = False
        self.cfg_path = cfg_path
        self.cfg = {}
        self.error = None

        try:
            with open(cfg_path, 'r') as ymlfile:
                self.cfg = yaml.safe_load(ymlfile)
            self.loaded = True
        except FileNotFoundError:
            self.error = f'Config file not found: "{cfg_path}"'
            self.cfg = None
        except Exception as e:
            self.error = f'Failed to load config file "{cfg_path}": {e}'
            self.cfg = None

        if self.error:
            _logger.warning(self.error)

    def get_value(self, yaml_path, delim='/'):
        path_elems = yaml_path.split(delim)

        val = self.cfg
        for el in path_elems:
            if val and el in val:
                try:
                    val = val[el]
                except Exception as e:
                    _logger.warning(
                        f'Failed to resolve config path "{yaml_path}" in '
                        f'"{self.cfg_path}" at element "{el}": {e}',
                    )
                    val = None
                    break
            else:
                val = None

        return val

    def get_item_by_key(self, key_name):
        v = self.get_value(key_name)
        if v is not None:
            return str(v)
        return v

    def get_whole_dictionary(self):
        return self.cfg

    def update(self, dictionary):
        if isinstance(dictionary, dict):
            self.cfg.update(dictionary)
