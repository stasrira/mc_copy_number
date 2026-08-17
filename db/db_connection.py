"""Database connection for mc_copy_number_processing.

Adapted from the submission project's DataRetrievalDB class.
Manages a pyodbc connection to the metadata MS SQL Server database.
Connection parameters are resolved from environment variables whose
names are stored in main_config.yaml under the Database/connection section.
"""

import os
import traceback

import pyodbc


class MetadataDB:
    """Lightweight pyodbc wrapper for the metadata SQL Server database."""

    def __init__(self, main_cfg, logger):
        self.logger = logger
        self._conn_str = self._build_conn_str(main_cfg)
        self._conn = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_conn_str(main_cfg) -> str:
        def cfg(key):
            return (main_cfg.get_value(key) or '').strip()

        def env(var_name):
            return os.environ.get(var_name.strip(), '')

        template = cfg('Database/connection/mdb_conn_str')
        replacements = {
            cfg('Database/connection/db_plh_driver'):    env(cfg('Database/connection/env_db_driver')),
            cfg('Database/connection/db_plh_server'):    env(cfg('Database/connection/env_db_server')),
            cfg('Database/connection/db_plh_db_name'):   env(cfg('Database/connection/env_db_name')),
            cfg('Database/connection/db_plh_user_name'): env(cfg('Database/connection/env_db_user_name')),
            cfg('Database/connection/db_plh_user_pwd'):  env(cfg('Database/connection/env_db_user_pwd')),
        }
        for placeholder, value in replacements.items():
            if placeholder:
                template = template.replace(placeholder, value)
        return template

    def _open(self) -> bool:
        if self._conn:
            return True
        self.logger.info('Opening connection to metadata DB.')
        try:
            self._conn = pyodbc.connect(self._conn_str, autocommit=True)
            self.logger.info('Database connection established.')
            return True
        except Exception:
            self.logger.error('Failed to open database connection:\n' + traceback.format_exc())
            return False

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def exec_query(self, sql: str) -> tuple[list[dict] | None, str]:
        """Execute *sql* and return ``(rows, error_string)``.

        *rows* is a list of dicts (column → value) or ``None`` on failure.
        *error_string* is empty on success.
        """
        if not self._open():
            return None, 'Database connection could not be established.'
        self.logger.info(f'Executing SQL: {sql}')
        try:
            cursor = self._conn.cursor()
            cursor.execute(sql)
            columns = [col[0] for col in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
            return rows, ''
        except Exception:
            err = traceback.format_exc()
            self.logger.error(f'SQL execution failed:\n{err}')
            return None, err

    def close(self):
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
