"""Pins the two "defense in depth" regexes that gate untrusted, file/DB-derived
values before they reach a comma-joined SQL parameter (_ALIQUOT_ID_RE) or a
filesystem path component (_PROGRAM_CODE_RE). See the docstrings in
alignment/aliquot_db_validator.py and mc_copy_number_counts.py for the rationale.
"""

from alignment.aliquot_db_validator import _ALIQUOT_ID_RE
from mc_copy_number_counts import _PROGRAM_CODE_RE


class TestAliquotIdRegex:
    def test_accepts_typical_ids(self):
        for value in ['BA.ANFO.S01.ANF010M.M01', 'Sample_123', 'abc-def', 'A1']:
            assert _ALIQUOT_ID_RE.match(value), value

    def test_accepts_accented_unicode_word_characters(self):
        assert _ALIQUOT_ID_RE.match('Naïve-A4')

    def test_rejects_comma_which_would_corrupt_the_joined_list(self):
        assert not _ALIQUOT_ID_RE.match('abc,def')

    def test_rejects_quote_semicolon_and_whitespace(self):
        for value in ["abc'def", 'abc"def', 'abc;def', 'abc def', '']:
            assert not _ALIQUOT_ID_RE.match(value), value


class TestProgramCodeRegex:
    def test_accepts_typical_codes(self):
        for value in ['ECHO_Code', 'abc123', 'A', 'a.b-c_d']:
            assert _PROGRAM_CODE_RE.match(value), value

    def test_rejects_pure_dot_path_traversal_component(self):
        assert not _PROGRAM_CODE_RE.match('..')

    def test_rejects_leading_or_trailing_dot_or_dash(self):
        for value in ['.hidden', 'trailing.', '-leading', 'trailing-']:
            assert not _PROGRAM_CODE_RE.match(value), value

    def test_rejects_path_separators_and_traversal_sequences(self):
        for value in ['../etc', 'a/b', 'a\\b', '../../etc/passwd']:
            assert not _PROGRAM_CODE_RE.match(value), value

    def test_rejects_whitespace_and_empty_string(self):
        for value in ['a b', '']:
            assert not _PROGRAM_CODE_RE.match(value), value
