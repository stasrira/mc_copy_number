"""Tests for mc_copy_number.py's Alignment -> Counts wiring logic.

run_alignment()/run_counts() are mocked out (they're covered by their own dedicated test
suites) so these tests isolate just the conditional branching that decides whether/how
Counts runs after Alignment.
"""
import logging

import mc_copy_number
import mc_copy_number_alignment
import mc_copy_number_counts
from utils.issue_collector import FileRecord


def _main_cfg(make_config, allow_automated_counts_processing=True, validate_aliquots_against_db=False):
    return make_config({
        'Alignment': {
            'allow_automated_counts_processing': allow_automated_counts_processing,
            'validate_aliquots_against_db': validate_aliquots_against_db,
        },
    }, filename='main.yaml')


def _stub_initialize_run(monkeypatch, main_cfg):
    monkeypatch.setattr(mc_copy_number, 'initialize_run', lambda *a, **k: {
        'logger': logging.getLogger('test'),
        'main_cfg': main_cfg,
        'log_dir': '/tmp',
        'log_filename': 'main_test.log',
    })


def _stub_send_status_email(monkeypatch):
    sent = {}

    def fake(logger, file_records, log_filename, main_cfg, subject_prefix=None, process_label=None):
        sent['file_records'] = file_records

    monkeypatch.setattr(mc_copy_number, 'send_status_email', fake)
    return sent


def _stub_run_alignment(monkeypatch, records):
    monkeypatch.setattr(mc_copy_number_alignment, 'run_alignment', lambda logger: records)


def _record(alignment_ok=True, db_validation_ok=True):
    record = FileRecord('src.xlsx', 'ProviderA')
    record.alignment_ok = alignment_ok
    record.alignment_output = 'out.csv' if alignment_ok else None
    record.db_validation_ok = db_validation_ok
    return record


class TestSkipCountsWhenNothingAligned:
    def test_run_counts_never_called(self, make_config, monkeypatch):
        main_cfg = _main_cfg(make_config)
        _stub_initialize_run(monkeypatch, main_cfg)
        records = [_record(alignment_ok=False)]
        _stub_run_alignment(monkeypatch, records)
        sent = _stub_send_status_email(monkeypatch)
        run_counts_calls = []
        monkeypatch.setattr(mc_copy_number_counts, 'run_counts', lambda *a, **k: run_counts_calls.append(1))

        mc_copy_number.main()

        assert run_counts_calls == []
        assert sent['file_records'] is records
        # Pins a known quirk: counts_ran is never explicitly reset to False in this branch,
        # so it stays at FileRecord's default of True even though Counts never ran for it.
        assert records[0].counts_ran is True


class TestCountsDisabledGlobally:
    def test_counts_skipped_and_all_records_marked_not_ran(self, make_config, monkeypatch):
        main_cfg = _main_cfg(make_config, allow_automated_counts_processing=False)
        _stub_initialize_run(monkeypatch, main_cfg)
        records = [_record(alignment_ok=True), _record(alignment_ok=True)]
        _stub_run_alignment(monkeypatch, records)
        _stub_send_status_email(monkeypatch)
        run_counts_calls = []
        monkeypatch.setattr(mc_copy_number_counts, 'run_counts', lambda *a, **k: run_counts_calls.append(1))

        mc_copy_number.main()

        assert run_counts_calls == []
        assert all(r.counts_ran is False for r in records)


class TestDbValidationPartialFailure:
    def test_only_validated_records_passed_to_run_counts(self, make_config, monkeypatch, caplog):
        main_cfg = _main_cfg(make_config, validate_aliquots_against_db=True)
        _stub_initialize_run(monkeypatch, main_cfg)
        good = _record(alignment_ok=True, db_validation_ok=True)
        bad = _record(alignment_ok=True, db_validation_ok=False)
        records = [good, bad]
        _stub_run_alignment(monkeypatch, records)
        _stub_send_status_email(monkeypatch)
        run_counts_calls = []
        monkeypatch.setattr(
            mc_copy_number_counts, 'run_counts', lambda logger, recs: run_counts_calls.append(recs),
        )

        with caplog.at_level('WARNING'):
            mc_copy_number.main()

        assert len(run_counts_calls) == 1
        assert run_counts_calls[0] == [good]
        assert bad.counts_ran is False
        assert good.counts_ran is True  # untouched, still its default
        assert any('failed DB aliquot validation' in rec.message for rec in caplog.records)


class TestDbValidationAllFail:
    def test_run_counts_not_called_at_all(self, make_config, monkeypatch, caplog):
        main_cfg = _main_cfg(make_config, validate_aliquots_against_db=True)
        _stub_initialize_run(monkeypatch, main_cfg)
        records = [_record(alignment_ok=True, db_validation_ok=False)]
        _stub_run_alignment(monkeypatch, records)
        _stub_send_status_email(monkeypatch)
        run_counts_calls = []
        monkeypatch.setattr(mc_copy_number_counts, 'run_counts', lambda *a, **k: run_counts_calls.append(1))

        with caplog.at_level('WARNING'):
            mc_copy_number.main()

        assert run_counts_calls == []
        assert any('No files passed DB validation' in rec.message for rec in caplog.records)
        assert records[0].counts_ran is False


class TestDbValidationDisabled:
    def test_all_records_passed_through_unfiltered(self, make_config, monkeypatch):
        main_cfg = _main_cfg(make_config, validate_aliquots_against_db=False)
        _stub_initialize_run(monkeypatch, main_cfg)
        records = [_record(alignment_ok=True, db_validation_ok=False), _record(alignment_ok=False)]
        _stub_run_alignment(monkeypatch, records)
        _stub_send_status_email(monkeypatch)
        run_counts_calls = []
        monkeypatch.setattr(
            mc_copy_number_counts, 'run_counts', lambda logger, recs: run_counts_calls.append(recs),
        )

        mc_copy_number.main()

        assert len(run_counts_calls) == 1
        assert run_counts_calls[0] == records  # whole list, unfiltered by alignment_ok or db_validation_ok


class TestStatusEmailAlwaysSent:
    def test_sent_exactly_once_per_run(self, make_config, monkeypatch):
        main_cfg = _main_cfg(make_config)
        _stub_initialize_run(monkeypatch, main_cfg)
        _stub_run_alignment(monkeypatch, [])
        send_calls = []
        monkeypatch.setattr(mc_copy_number, 'send_status_email', lambda *a, **k: send_calls.append(1))
        monkeypatch.setattr(mc_copy_number_counts, 'run_counts', lambda *a, **k: None)

        mc_copy_number.main()

        assert len(send_calls) == 1
