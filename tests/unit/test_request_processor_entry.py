from requests.request_processor import _process_request_entry
from utils.issue_collector import FileRecord


def _entry(row_number=2, raw_data_source='run1/foo.csv', program_code='', skip_aliquot_validation=False):
    return {
        'row_number': row_number,
        'raw_data_source': raw_data_source,
        'program_code': program_code,
        'skip_aliquot_validation': skip_aliquot_validation,
    }


class TestUnresolvableSource:
    def test_missing_studies_dir_produces_error_record_without_calling_process_counts_input(
        self, make_config, monkeypatch, logger,
    ):
        main_cfg = make_config({}, filename='main.yaml')
        loc_cfg = make_config({}, filename='loc.yaml')  # no Location/mitCopyN_studies_dir

        def fail_if_called(*a, **k):
            raise AssertionError('process_counts_input must not be called when the source cannot be resolved')

        monkeypatch.setattr('requests.request_processor.process_counts_input', fail_if_called)

        record = _process_request_entry(_entry(), main_cfg, loc_cfg, logger)

        assert record.counts_ran is False
        assert record.row_number == 2
        assert any('mitCopyN_studies_dir' in e for e in record.errors)

    def test_path_outside_allowed_root_produces_error_record(self, tmp_path, make_config, monkeypatch, logger):
        loc_cfg = make_config({'Location': {'mitCopyN_studies_dir': str(tmp_path)}}, filename='loc.yaml')
        main_cfg = make_config({}, filename='main.yaml')
        (tmp_path / 'raw_data').mkdir()
        outside = tmp_path / 'outside.csv'
        outside.write_text('data')

        def fail_if_called(*a, **k):
            raise AssertionError('process_counts_input must not be called for a rejected path')

        monkeypatch.setattr('requests.request_processor.process_counts_input', fail_if_called)

        record = _process_request_entry(_entry(raw_data_source=str(outside)), main_cfg, loc_cfg, logger)

        assert record.counts_ran is False
        assert any('resolves outside' in e for e in record.errors)


class TestDelegatesToProcessCountsInput:
    def test_success_wires_override_and_skip_flags_and_copies_result_attributes(
        self, tmp_path, make_config, monkeypatch, logger,
    ):
        loc_cfg = make_config({'Location': {'mitCopyN_studies_dir': str(tmp_path)}}, filename='loc.yaml')
        main_cfg = make_config({}, filename='main.yaml')
        raw_data_dir = tmp_path / 'raw_data'
        target = raw_data_dir / 'run1' / 'foo.csv'
        target.parent.mkdir(parents=True)
        target.write_text('data')

        captured_calls = []

        def fake_process_counts_input(
            input_path, main_cfg_arg, loc_cfg_arg, logger_arg, launch_param, launch_value,
            program_code_override=None, skip_aliquot_validation=False,
        ):
            captured_calls.append({
                'input_path': input_path,
                'launch_param': launch_param,
                'program_code_override': program_code_override,
                'skip_aliquot_validation': skip_aliquot_validation,
            })
            fake_record = FileRecord(str(input_path), 'standalone')
            fake_record.counts_ok = True
            fake_record.program_code = program_code_override or 'PROG1'
            return fake_record

        monkeypatch.setattr('requests.request_processor.process_counts_input', fake_process_counts_input)

        record = _process_request_entry(
            _entry(row_number=5, raw_data_source='run1/foo.csv', program_code='FORCED', skip_aliquot_validation=True),
            main_cfg, loc_cfg, logger,
        )

        assert len(captured_calls) == 1
        call = captured_calls[0]
        assert call['input_path'] == target.resolve()
        assert call['launch_param'] == '--input_file'
        assert call['program_code_override'] == 'FORCED'
        assert call['skip_aliquot_validation'] is True

        # RequestEntryRecord is enriched with request-specific context...
        assert record.row_number == 5
        assert record.raw_data_source == 'run1/foo.csv'
        assert record.program_code_override == 'FORCED'
        assert record.skip_aliquot_validation is True
        # ...and copies the delegated call's result attributes.
        assert record.counts_ok is True
        assert record.program_code == 'FORCED'
