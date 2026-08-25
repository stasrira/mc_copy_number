"""Guards against configs/location_config_example.yaml and .env.example silently going stale —
missing a key/var that the real config is required to have makes bootstrapping a new environment
from the example produce a broken config with no obvious error (see CLAUDE.md's Config layering
section and utils.configuration.ConfigData.get_value, which returns None silently for a missing
path).
"""
import re
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def _env_var_names(path: Path) -> set[str]:
    names = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=', line)
        if match:
            names.add(match.group(1))
    return names


class TestLocationConfigExampleIsComplete:
    def test_declares_every_location_key_the_codebase_reads(self):
        # Every 'Location/<key>' path actually read via loc_cfg.get_value(...) in the codebase
        # (utils/common.py: logs, mitCopyN_studies_dir; requests/request_processor.py: requests_dir).
        required_keys = {'mitCopyN_studies_dir', 'requests_dir', 'logs'}

        example = _load_yaml(PROJECT_ROOT / 'configs' / 'location_config_example.yaml')

        assert required_keys.issubset(example['Location'].keys())


class TestEnvExampleIsComplete:
    def test_declares_every_required_env_var(self):
        main_cfg = _load_yaml(PROJECT_ROOT / 'configs' / 'main_config.yaml')
        db_conn = main_cfg['Database']['connection']
        # The DB env var *names* are indirected through main_config.yaml's env_db_* values (see
        # db/db_connection.py); the SMTP and status-email var names are hardcoded directly in
        # utils/send_email.py and utils/common.py (_resolve_email_recipients) respectively.
        # MC_EMAIL_ADDITIONAL_TO is intentionally excluded: it's only consulted when
        # Email/include_additional_emails is on, so it's optional rather than required.
        required_vars = {
            db_conn['env_db_driver'], db_conn['env_db_server'], db_conn['env_db_name'],
            db_conn['env_db_user_name'], db_conn['env_db_user_pwd'],
            'SRD_SMTP_SERVER', 'SRD_SMTP_SERVER_PORT',
            'MC_EMAIL_FROM', 'MC_EMAIL_TO',
        }

        declared = _env_var_names(PROJECT_ROOT / '.env.example')

        assert required_vars.issubset(declared)
