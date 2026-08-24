from alignment.aliquot_db_validator import validate_aliquots


class FakeMetadataDB:
    def __init__(self, rows=None, err=''):
        self._rows = rows
        self._err = err
        self.closed = False
        self.exec_calls = []

    def exec_query(self, sql, params=None):
        self.exec_calls.append((sql, params))
        return self._rows, self._err

    def close(self):
        self.closed = True


def _install_fake_db(monkeypatch, rows=None, err=''):
    fake = FakeMetadataDB(rows=rows, err=err)
    monkeypatch.setattr('alignment.aliquot_db_validator.MetadataDB', lambda main_cfg, logger: fake)
    return fake


def test_empty_aliquot_list_fails_without_db_call(make_config, monkeypatch, logger):
    fake = _install_fake_db(monkeypatch)

    ok, groups, errors = validate_aliquots([], make_config({}), logger)

    assert ok is False
    assert groups is None
    assert 'no aliquot IDs were extracted' in errors[0]
    assert fake.exec_calls == []


def test_malformed_aliquot_id_fails_without_db_call(make_config, monkeypatch, logger):
    fake = _install_fake_db(monkeypatch)

    ok, groups, errors = validate_aliquots(['A1', 'bad,id'], make_config({}), logger)

    assert ok is False
    assert groups is None
    assert 'unexpected' in errors[0]
    assert 'bad,id' in errors[0]
    assert fake.exec_calls == []


def test_db_query_error_fails(make_config, monkeypatch, logger):
    fake = _install_fake_db(monkeypatch, rows=None, err='connection refused')

    ok, groups, errors = validate_aliquots(['A1'], make_config({}), logger)

    assert ok is False
    assert groups is None
    assert 'database query failed' in errors[0]
    assert fake.closed is True


def test_missing_aliquot_in_db_result_fails(make_config, monkeypatch, logger):
    _install_fake_db(monkeypatch, rows=[{'_aliquot_id': 'A1', '_program_code': 'PROG1'}])

    ok, groups, errors = validate_aliquots(['A1', 'A2'], make_config({}), logger)

    assert ok is False
    assert groups is None
    assert 'were not found' in errors[0]
    assert 'A2' in errors[0]


def test_no_program_code_returned_fails(make_config, monkeypatch, logger):
    _install_fake_db(monkeypatch, rows=[{'_aliquot_id': 'A1', '_program_code': ''}])

    ok, groups, errors = validate_aliquots(['A1'], make_config({}), logger)

    assert ok is False
    assert groups is None
    assert 'no program code' in errors[0]


def test_multiple_programs_rejected_when_not_allowed(make_config, monkeypatch, logger):
    _install_fake_db(monkeypatch, rows=[
        {'_aliquot_id': 'A1', '_program_code': 'PROG1'},
        {'_aliquot_id': 'A2', '_program_code': 'PROG2'},
    ])

    ok, groups, errors = validate_aliquots(['A1', 'A2'], make_config({}), logger, allow_multiple_programs=False)

    assert ok is False
    assert groups is None
    assert 'more than one program' in errors[0]


def test_multiple_programs_grouped_when_allowed(make_config, monkeypatch, logger):
    _install_fake_db(monkeypatch, rows=[
        {'_aliquot_id': 'A1', '_program_code': 'PROG1'},
        {'_aliquot_id': 'A2', '_program_code': 'PROG2'},
    ])

    ok, groups, errors = validate_aliquots(['A1', 'A2'], make_config({}), logger, allow_multiple_programs=True)

    assert ok is True
    assert groups == {'PROG1': ['A1'], 'PROG2': ['A2']}
    assert errors == []


def test_single_program_success_binds_comma_joined_ids_as_one_param(make_config, monkeypatch, logger):
    fake = _install_fake_db(monkeypatch, rows=[
        {'_aliquot_id': 'A1', '_program_code': 'PROG1'},
        {'_aliquot_id': 'A2', '_program_code': 'PROG1'},
    ])

    ok, groups, errors = validate_aliquots(['A1', 'A2'], make_config({}), logger)

    assert ok is True
    assert groups == {'PROG1': ['A1', 'A2']}
    assert errors == []
    sql, params = fake.exec_calls[0]
    assert params == ('A1,A2',)
    assert '?' in sql
    assert fake.closed is True
