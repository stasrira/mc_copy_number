import os

import pandas as pd

from alignment.file_processor import AlignmentFileProcessor
from providers.base_provider import BaseProvider


class FakeProvider(BaseProvider):
    """Minimal provider stub: returns a canned DataFrame or raises a canned error."""

    def __init__(self, name='FakeProvider', df=None, error=None):
        self._name = name
        self._df = df if df is not None else pd.DataFrame({'aliquot_id': ['A1'], 'mtdna_mean': [1.0]})
        self._error = error

    @property
    def name(self):
        return self._name

    def extract(self, file_path):
        if self._error:
            raise self._error
        return self._df


def _dirs(tmp_path):
    return tmp_path / 'temp_processing', tmp_path / 'processed', tmp_path / 'reprocess'


class TestProcessFileHappyPath:
    def test_writes_csv_with_renamed_columns_and_moves_source_to_processed(self, tmp_path, logger):
        src = tmp_path / 'ready' / 'foo.xlsx'
        src.parent.mkdir()
        src.write_text('placeholder')
        temp_dir, processed_dir, reprocess_dir = _dirs(tmp_path)
        df = pd.DataFrame({'aliquot_id_key': ['A1', 'A2'], 'mean_key': [1.0, 2.0]})
        schema_map = {'aliquot_id_key': 'aliquot_id', 'mean_key': 'mtDNA_copy_num_mean'}
        processor = AlignmentFileProcessor(
            FakeProvider(df=df), str(tmp_path / 'raw_data'), logger, schema_map=schema_map,
        )

        out_path = processor.process_file(src, temp_dir, processed_dir, reprocess_dir)

        assert out_path is not None
        written = pd.read_csv(out_path)
        assert list(written.columns) == ['aliquot_id', 'mtDNA_copy_num_mean']
        assert not src.exists()
        assert not (temp_dir / 'foo.xlsx').exists()
        assert (processed_dir / 'foo.xlsx').exists()

    def test_output_name_spaces_replaced_but_source_name_unchanged(self, tmp_path, logger):
        """Real-world example: SNE_batch1_PAXgeneDNA_mitochondrial copy number assay results_rev.xlsx
        from NairLab. The raw_data output folder/file name must have spaces collapsed to
        underscores, but the source file itself must be moved to processed/ under its original,
        space-containing name."""
        src_name = 'SNE_batch1_PAXgeneDNA_mitochondrial copy number assay results_rev.xlsx'
        src = tmp_path / 'ready' / src_name
        src.parent.mkdir()
        src.write_text('placeholder')
        temp_dir, processed_dir, reprocess_dir = _dirs(tmp_path)
        processor = AlignmentFileProcessor(
            FakeProvider(), str(tmp_path / 'raw_data'), logger,
        )

        out_path = processor.process_file(src, temp_dir, processed_dir, reprocess_dir)

        assert out_path is not None
        assert out_path.name == 'SNE_batch1_PAXgeneDNA_mitochondrial_copy_number_assay_results_rev.csv'
        assert ' ' not in out_path.parent.name
        assert (processed_dir / src_name).exists()

    def test_unmapped_column_is_kept_as_is_with_warning(self, tmp_path, logger, caplog):
        src = tmp_path / 'ready' / 'foo.xlsx'
        src.parent.mkdir()
        src.write_text('placeholder')
        temp_dir, processed_dir, reprocess_dir = _dirs(tmp_path)
        df = pd.DataFrame({'aliquot_id_key': ['A1'], 'extra_key': ['x']})
        schema_map = {'aliquot_id_key': 'aliquot_id'}
        processor = AlignmentFileProcessor(
            FakeProvider(df=df), str(tmp_path / 'raw_data'), logger, schema_map=schema_map,
        )

        with caplog.at_level('WARNING'):
            out_path = processor.process_file(src, temp_dir, processed_dir, reprocess_dir)

        written = pd.read_csv(out_path)
        assert 'extra_key' in written.columns
        assert any('not mapped via schema' in rec.message for rec in caplog.records)

    def test_processed_move_gets_suffix_on_name_collision(self, tmp_path, logger):
        src = tmp_path / 'ready' / 'foo.xlsx'
        src.parent.mkdir()
        src.write_text('placeholder')
        temp_dir, processed_dir, reprocess_dir = _dirs(tmp_path)
        processed_dir.mkdir()
        (processed_dir / 'foo.xlsx').write_text('existing file, must not be overwritten')
        processor = AlignmentFileProcessor(FakeProvider(), str(tmp_path / 'raw_data'), logger)

        out_path = processor.process_file(src, temp_dir, processed_dir, reprocess_dir)

        assert out_path is not None
        assert (processed_dir / 'foo.xlsx').read_text() == 'existing file, must not be overwritten'
        assert (processed_dir / 'foo(1).xlsx').exists()

    def test_non_ascii_data_is_written_with_utf8_bom(self, tmp_path, logger):
        src = tmp_path / 'ready' / 'foo.xlsx'
        src.parent.mkdir()
        src.write_text('placeholder')
        temp_dir, processed_dir, reprocess_dir = _dirs(tmp_path)
        df = pd.DataFrame({'aliquot_id': ['Naïve-A4']})
        processor = AlignmentFileProcessor(FakeProvider(df=df), str(tmp_path / 'raw_data'), logger)

        out_path = processor.process_file(src, temp_dir, processed_dir, reprocess_dir)

        assert out_path.read_bytes().startswith(b'\xef\xbb\xbf')

    def test_bom_disabled_flag_forces_plain_utf8(self, tmp_path, logger):
        src = tmp_path / 'ready' / 'foo.xlsx'
        src.parent.mkdir()
        src.write_text('placeholder')
        temp_dir, processed_dir, reprocess_dir = _dirs(tmp_path)
        df = pd.DataFrame({'aliquot_id': ['Naïve-A4']})
        processor = AlignmentFileProcessor(
            FakeProvider(df=df), str(tmp_path / 'raw_data'), logger, enable_utf8_bom=False,
        )

        out_path = processor.process_file(src, temp_dir, processed_dir, reprocess_dir)

        assert not out_path.read_bytes().startswith(b'\xef\xbb\xbf')


class TestProcessFileFailurePaths:
    def test_empty_extraction_moves_source_to_reprocess(self, tmp_path, logger):
        src = tmp_path / 'ready' / 'foo.xlsx'
        src.parent.mkdir()
        src.write_text('placeholder')
        temp_dir, processed_dir, reprocess_dir = _dirs(tmp_path)
        processor = AlignmentFileProcessor(FakeProvider(df=pd.DataFrame()), str(tmp_path / 'raw_data'), logger)

        out_path = processor.process_file(src, temp_dir, processed_dir, reprocess_dir)

        assert out_path is None
        assert not src.exists()
        assert not (temp_dir / 'foo.xlsx').exists()
        assert (reprocess_dir / 'foo.xlsx').exists()

    def test_extraction_exception_moves_source_to_reprocess(self, tmp_path, logger):
        src = tmp_path / 'ready' / 'foo.xlsx'
        src.parent.mkdir()
        src.write_text('placeholder')
        temp_dir, processed_dir, reprocess_dir = _dirs(tmp_path)
        processor = AlignmentFileProcessor(
            FakeProvider(error=RuntimeError('boom')), str(tmp_path / 'raw_data'), logger,
        )

        out_path = processor.process_file(src, temp_dir, processed_dir, reprocess_dir)

        assert out_path is None
        assert not src.exists()
        assert (reprocess_dir / 'foo.xlsx').exists()

    def test_missing_source_file_returns_none_without_crashing(self, tmp_path, logger):
        # Simulates another process already having claimed the file: os.rename on a
        # nonexistent source raises FileNotFoundError, which process_file must swallow.
        src = tmp_path / 'ready' / 'foo.xlsx'  # never created
        temp_dir, processed_dir, reprocess_dir = _dirs(tmp_path)
        processor = AlignmentFileProcessor(FakeProvider(), str(tmp_path / 'raw_data'), logger)

        out_path = processor.process_file(src, temp_dir, processed_dir, reprocess_dir)

        assert out_path is None

    def test_permission_error_on_claim_leaves_file_in_ready(self, tmp_path, logger, monkeypatch):
        src = tmp_path / 'ready' / 'foo.xlsx'
        src.parent.mkdir()
        src.write_text('placeholder')
        temp_dir, processed_dir, reprocess_dir = _dirs(tmp_path)

        def raise_permission_error(_src, _dst):
            raise PermissionError('file is open in Excel')

        monkeypatch.setattr(os, 'rename', raise_permission_error)
        processor = AlignmentFileProcessor(FakeProvider(), str(tmp_path / 'raw_data'), logger)

        out_path = processor.process_file(src, temp_dir, processed_dir, reprocess_dir)

        assert out_path is None
        assert src.exists()
        assert not temp_dir.exists() or not any(temp_dir.iterdir())
