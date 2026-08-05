import yaml


class ConfigData:

    def __init__(self, cfg_path):
        self.loaded = False
        self.cfg_path = cfg_path
        self.cfg = {}

        try:
            with open(cfg_path, 'r') as ymlfile:
                self.cfg = yaml.safe_load(ymlfile)
            self.loaded = True
        except FileNotFoundError:
            self.cfg = None
        except Exception:
            self.cfg = None

    def get_value(self, yaml_path, delim='/'):
        path_elems = yaml_path.split(delim)

        val = self.cfg
        for el in path_elems:
            if val and el in val:
                try:
                    val = val[el]
                except Exception:
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
