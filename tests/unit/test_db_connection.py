import pytest

import db.db_connection as db_connection
from db.db_connection import MetadataDB

CONNECTION_CFG = {
    'Database': {
        'connection': {
            'mdb_conn_str': (
                'Driver={!driver!};Server={!server!};Database={!db_name!};'
                'UID={!db_user_name!};PWD={!db_user_pwd!};TrustServerCertificate=yes;'
            ),
            'db_plh_driver': '{!driver!}',
            'db_plh_server': '{!server!}',
            'db_plh_db_name': '{!db_name!}',
            'db_plh_user_name': '{!db_user_name!}',
            'db_plh_user_pwd': '{!db_user_pwd!}',
            'env_db_driver': 'MC_DB_DRIVER',
            'env_db_server': 'MC_DB_SERVER',
            'env_db_name': 'MC_DB_NAME',
            'env_db_user_name': 'MC_DB_USER_NAME',
            'env_db_user_pwd': 'MC_DB_USER_PWD',
        }
    }
}


class FakeCursor:
    def __init__(self, description=None, rows=None, execute_error=None):
        self.description = description or []
        self._rows = rows or []
        self._execute_error = execute_error
        self.executed_sql = None
        self.executed_params = None

    def execute(self, sql, params=None):
        self.executed_sql = sql
        self.executed_params = params
        if self._execute_error:
            raise self._execute_error

    def fetchall(self):
        return self._rows


class FakeConnection:
    def __init__(self, cursor: FakeCursor, close_error=None):
        self._cursor = cursor
        self._close_error = close_error
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        if self._close_error:
            raise self._close_error
        self.closed = True


def test_build_conn_str_substitutes_env_vars(make_config, monkeypatch, logger):
    monkeypatch.setenv('MC_DB_DRIVER', 'ODBC Driver 17 for SQL Server')
    monkeypatch.setenv('MC_DB_SERVER', 'sql.example.org')
    monkeypatch.setenv('MC_DB_NAME', 'metadata_db')
    monkeypatch.setenv('MC_DB_USER_NAME', 'svc_user')
    monkeypatch.setenv('MC_DB_USER_PWD', 's3cr3t')
    main_cfg = make_config(CONNECTION_CFG)

    conn_str = MetadataDB._build_conn_str(main_cfg)

    assert 'ODBC Driver 17 for SQL Server' in conn_str
    assert 'sql.example.org' in conn_str
    assert 'metadata_db' in conn_str
    assert 'svc_user' in conn_str
    assert 's3cr3t' in conn_str
    assert '{!driver!}' not in conn_str


def test_build_conn_str_missing_env_var_substitutes_empty_string(make_config, monkeypatch):
    monkeypatch.delenv('MC_DB_USER_PWD', raising=False)
    main_cfg = make_config(CONNECTION_CFG)

    conn_str = MetadataDB._build_conn_str(main_cfg)

    assert 'PWD=;' in conn_str


def test_exec_query_success_returns_rows_as_dicts(make_config, monkeypatch, logger):
    cursor = FakeCursor(description=[('_aliquot_id',), ('_program_code',)], rows=[('A1', 'PROG1')])
    monkeypatch.setattr(db_connection.pyodbc, 'connect', lambda *a, **kw: FakeConnection(cursor))
    db = MetadataDB(make_config(CONNECTION_CFG), logger)

    rows, err = db.exec_query('exec dbo.some_proc @ids = ?', params=('A1',))

    assert err == ''
    assert rows == [{'_aliquot_id': 'A1', '_program_code': 'PROG1'}]
    assert cursor.executed_params == ('A1',)


def test_exec_query_connection_failure_returns_error(make_config, monkeypatch, logger):
    def raise_connect(*a, **kw):
        raise RuntimeError('driver not found')

    monkeypatch.setattr(db_connection.pyodbc, 'connect', raise_connect)
    db = MetadataDB(make_config(CONNECTION_CFG), logger)

    rows, err = db.exec_query('select 1')

    assert rows is None
    assert 'could not be established' in err


def test_exec_query_execute_exception_returns_error(make_config, monkeypatch, logger):
    cursor = FakeCursor(execute_error=RuntimeError('bad SQL'))
    monkeypatch.setattr(db_connection.pyodbc, 'connect', lambda *a, **kw: FakeConnection(cursor))
    db = MetadataDB(make_config(CONNECTION_CFG), logger)

    rows, err = db.exec_query('select 1')

    assert rows is None
    assert err  # traceback text, non-empty


def test_close_swallows_errors(make_config, monkeypatch, logger):
    cursor = FakeCursor()
    fake_conn = FakeConnection(cursor, close_error=RuntimeError('already closed'))
    monkeypatch.setattr(db_connection.pyodbc, 'connect', lambda *a, **kw: fake_conn)
    db = MetadataDB(make_config(CONNECTION_CFG), logger)
    db.exec_query('select 1')  # opens the connection

    db.close()  # must not raise despite close_error

    assert db._conn is None
