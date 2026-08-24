from requests.request_processor import _resolve_allowed_raw_data_root, _resolve_raw_data_source


class TestResolveAllowedRawDataRoot:
    def test_missing_studies_dir_returns_none(self, make_config):
        loc_cfg = make_config({}, filename='loc.yaml')
        main_cfg = make_config({}, filename='main.yaml')

        assert _resolve_allowed_raw_data_root(loc_cfg, main_cfg) is None

    def test_default_raw_data_dir_name(self, tmp_path, make_config):
        loc_cfg = make_config({'Location': {'mitCopyN_studies_dir': str(tmp_path)}}, filename='loc.yaml')
        main_cfg = make_config({}, filename='main.yaml')

        assert _resolve_allowed_raw_data_root(loc_cfg, main_cfg) == tmp_path / 'raw_data'

    def test_configured_raw_data_dir_name(self, tmp_path, make_config):
        loc_cfg = make_config({'Location': {'mitCopyN_studies_dir': str(tmp_path)}}, filename='loc.yaml')
        main_cfg = make_config({'Alignment': {'raw_data_dir': 'custom_raw'}}, filename='main.yaml')

        assert _resolve_allowed_raw_data_root(loc_cfg, main_cfg) == tmp_path / 'custom_raw'


class TestResolveRawDataSource:
    def test_empty_value_rejected(self, tmp_path, logger):
        csv_path, launch_param, err = _resolve_raw_data_source('', tmp_path, logger)

        assert csv_path is None
        assert 'value is empty' in err

    def test_relative_path_to_existing_csv_resolves_under_root(self, tmp_path, logger):
        allowed_root = tmp_path / 'raw_data'
        target = allowed_root / 'run1' / 'foo.csv'
        target.parent.mkdir(parents=True)
        target.write_text('data')

        csv_path, launch_param, err = _resolve_raw_data_source('run1/foo.csv', allowed_root, logger)

        assert err is None
        assert csv_path == target.resolve()
        assert launch_param == '--input_file'

    def test_absolute_path_inside_root_is_allowed(self, tmp_path, logger):
        allowed_root = tmp_path / 'raw_data'
        target = allowed_root / 'run1' / 'foo.csv'
        target.parent.mkdir(parents=True)
        target.write_text('data')

        csv_path, launch_param, err = _resolve_raw_data_source(str(target), allowed_root, logger)

        assert err is None
        assert csv_path == target.resolve()

    def test_absolute_path_outside_root_is_rejected(self, tmp_path, logger):
        allowed_root = tmp_path / 'raw_data'
        allowed_root.mkdir()
        outside = tmp_path / 'elsewhere' / 'secret.csv'
        outside.parent.mkdir(parents=True)
        outside.write_text('data')

        csv_path, launch_param, err = _resolve_raw_data_source(str(outside), allowed_root, logger)

        assert csv_path is None
        assert 'resolves outside the allowed raw_data directory' in err

    def test_relative_traversal_escaping_root_is_rejected(self, tmp_path, logger):
        allowed_root = tmp_path / 'raw_data'
        allowed_root.mkdir()
        outside = tmp_path / 'secret.csv'
        outside.write_text('data')

        csv_path, launch_param, err = _resolve_raw_data_source('../secret.csv', allowed_root, logger)

        assert csv_path is None
        assert 'resolves outside the allowed raw_data directory' in err

    def test_deep_traversal_sequence_is_rejected(self, tmp_path, logger):
        allowed_root = tmp_path / 'raw_data'
        allowed_root.mkdir()

        csv_path, launch_param, err = _resolve_raw_data_source(
            '../../../../etc/passwd', allowed_root, logger,
        )

        assert csv_path is None
        assert 'resolves outside the allowed raw_data directory' in err

    def test_nonexistent_path_rejected(self, tmp_path, logger):
        allowed_root = tmp_path / 'raw_data'
        allowed_root.mkdir()

        csv_path, launch_param, err = _resolve_raw_data_source('run1/missing.csv', allowed_root, logger)

        assert csv_path is None
        assert 'does not exist' in err

    def test_non_csv_file_rejected(self, tmp_path, logger):
        allowed_root = tmp_path / 'raw_data'
        target = allowed_root / 'run1' / 'foo.txt'
        target.parent.mkdir(parents=True)
        target.write_text('not a csv')

        csv_path, launch_param, err = _resolve_raw_data_source('run1/foo.txt', allowed_root, logger)

        assert csv_path is None
        assert 'is not a CSV' in err

    def test_directory_with_zero_csvs_rejected(self, tmp_path, logger):
        allowed_root = tmp_path / 'raw_data'
        run_dir = allowed_root / 'run1'
        run_dir.mkdir(parents=True)

        csv_path, launch_param, err = _resolve_raw_data_source('run1', allowed_root, logger)

        assert csv_path is None
        assert 'No CSV files found' in err

    def test_directory_with_exactly_one_csv_resolves(self, tmp_path, logger):
        allowed_root = tmp_path / 'raw_data'
        run_dir = allowed_root / 'run1'
        run_dir.mkdir(parents=True)
        (run_dir / 'foo.csv').write_text('data')

        csv_path, launch_param, err = _resolve_raw_data_source('run1', allowed_root, logger)

        assert err is None
        assert csv_path == (run_dir / 'foo.csv').resolve()
        assert launch_param == '--input_dir'

    def test_directory_with_multiple_csvs_rejected(self, tmp_path, logger):
        allowed_root = tmp_path / 'raw_data'
        run_dir = allowed_root / 'run1'
        run_dir.mkdir(parents=True)
        (run_dir / 'a.csv').write_text('data')
        (run_dir / 'b.csv').write_text('data')

        csv_path, launch_param, err = _resolve_raw_data_source('run1', allowed_root, logger)

        assert csv_path is None
        assert 'but found 2' in err

    def test_directory_lock_files_are_ignored(self, tmp_path, logger):
        allowed_root = tmp_path / 'raw_data'
        run_dir = allowed_root / 'run1'
        run_dir.mkdir(parents=True)
        (run_dir / 'foo.csv').write_text('data')
        (run_dir / '~$foo.csv').write_text('lock file placeholder')

        csv_path, launch_param, err = _resolve_raw_data_source('run1', allowed_root, logger)

        assert err is None
        assert csv_path == (run_dir / 'foo.csv').resolve()
