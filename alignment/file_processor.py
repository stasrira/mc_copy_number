import os
import shutil
import time
import traceback
from pathlib import Path

import pandas as pd

from providers.base_provider import BaseProvider
from utils.common import resolve_csv_write_encoding


class AlignmentFileProcessor:
    """Handles the full lifecycle of processing a single source file for a provider.

    Lifecycle per file:
      1. Atomically move source file from ready/ → temp_processing/   (claim it)
      2. Extract data via the provider
      3. Create a timestamped output sub-folder under raw_data_dir
      4. Save the converted CSV into that sub-folder
      5. Move the source file from temp_processing/ → processed/
      On any error after step 1, the file is moved to reprocess/ for manual intervention.
    """

    def __init__(self, provider: BaseProvider, raw_data_dir: str, logger, schema_map: dict = None,
                enable_utf8_bom: bool = True):
        self.provider = provider
        self.raw_data_dir = Path(raw_data_dir)
        self.logger = logger
        self.schema_map = schema_map or {}
        self.enable_utf8_bom = enable_utf8_bom

    @staticmethod
    def _unique_dest_path(dest_dir: Path, file_name: str) -> Path:
        """Return a destination path that does not conflict with existing files.

        If dest_dir/file_name already exists, appends "(n)" before the suffix,
        incrementing n from 1 until a free name is found.
        E.g. "file.xlsx" → "file(1).xlsx" → "file(2).xlsx" …
        """
        candidate = dest_dir / file_name
        if not candidate.exists():
            return candidate
        stem = Path(file_name).stem
        suffix = Path(file_name).suffix
        n = 1
        while True:
            candidate = dest_dir / f'{stem}({n}){suffix}'
            if not candidate.exists():
                return candidate
            n += 1

    def process_file(self, file_path: Path, temp_processing_dir: Path, processed_dir: Path,
                     reprocess_dir: Path) -> bool:
        """Process a single file end-to-end.

        :param file_path: Full path to the file in the ready/ folder
        :param temp_processing_dir: Path to temp_processing/ sub-folder
        :param processed_dir: Path to processed/ sub-folder
        :param reprocess_dir: Path to reprocess/ sub-folder (destination on error)
        :returns: Path to the output CSV on success, None on failure/skip
        """
        file_name = file_path.name
        self.logger.info(f'[{self.provider.name}] Starting processing of: "{file_name}"')

        # --- Step 1: atomically claim the file ---
        temp_file_path = temp_processing_dir / file_name
        try:
            os.makedirs(temp_processing_dir, exist_ok=True)
            os.rename(file_path, temp_file_path)
            self.logger.info(f'[{self.provider.name}] Claimed file → temp_processing: "{file_name}"')
        except FileNotFoundError:
            # Another process already claimed it — skip silently
            self.logger.warning(
                f'[{self.provider.name}] File "{file_name}" was already claimed by another process. Skipping.'
            )
            return False
        except PermissionError:
            self.logger.warning(
                f'[{self.provider.name}] File "{file_name}" is locked by another process (e.g. open in Excel) '
                f'and cannot be moved. The file remains in the ready folder and will be attempted again on the next run.'
            )
            return False
        except Exception:
            self.logger.error(
                f'[{self.provider.name}] Failed to move "{file_name}" to temp_processing.\n'
                + traceback.format_exc()
            )
            return False

        try:
            # --- Step 2: extract ---
            df = self.provider.extract(temp_file_path)

            if df.empty:
                self.logger.error(
                    f'[{self.provider.name}] Extraction produced 0 rows from "{file_name}". '
                    f'Possible cause: wrong file format or expected column headers were not found '
                    f'(check header mismatch warnings above). Moving to reprocess folder.'
                )
                os.makedirs(reprocess_dir, exist_ok=True)
                reprocess_file_path = self._unique_dest_path(reprocess_dir, file_name)
                shutil.move(str(temp_file_path), str(reprocess_file_path))
                self.logger.warning(
                    f'[{self.provider.name}] Moved "{file_name}" to reprocess folder: "{reprocess_file_path}"'
                )
                return None

            # --- Step 2b: rename schema keys → canonical column names ---
            if self.schema_map:
                df = df.rename(columns=self.schema_map)
                unmapped = [c for c in df.columns if c not in self.schema_map.values()]
                if unmapped:
                    self.logger.warning(
                        f'[{self.provider.name}] Columns not mapped via schema (kept as-is): {unmapped}'
                    )

            # --- Step 3: create output sub-folder ---
            timestamp = time.strftime('%Y%m%d_%H%M%S')
            stem = Path(file_name).stem
            out_subfolder = self.raw_data_dir / f'{timestamp}_{stem}'
            os.makedirs(out_subfolder, exist_ok=True)

            # --- Step 4: save CSV ---
            out_csv_name = f'{stem}.csv'
            out_csv_path = out_subfolder / out_csv_name
            # Only add a UTF-8 BOM (via utf-8-sig) when the data actually needs it — see
            # resolve_csv_write_encoding()'s docstring for why this is conditional rather than
            # always-on.
            encoding, non_ascii_example = resolve_csv_write_encoding(df, enable_utf8_bom=self.enable_utf8_bom)
            if non_ascii_example:
                # Logged at INFO (not WARNING) and tagged via `extra` so the status email
                # aggregates this into a single BOM note per record instead of one confusingly
                # similar warning per output file (see CapturingLogHandler).
                self.logger.info(
                    f'[{self.provider.name}] "{out_csv_name}" saved with a UTF-8 BOM '
                    f'(non-ASCII character(s) found, e.g. "{non_ascii_example}").',
                    extra={'bom_path': str(out_csv_path), 'bom_example': non_ascii_example},
                )
            df.to_csv(out_csv_path, index=False, encoding=encoding)
            self.logger.info(
                f'[{self.provider.name}] Saved {len(df)} rows → "{out_csv_path}"'
            )

            # --- Step 5: move source to processed ---
            os.makedirs(processed_dir, exist_ok=True)
            processed_file_path = self._unique_dest_path(processed_dir, file_name)
            shutil.move(str(temp_file_path), str(processed_file_path))
            self.logger.info(
                f'[{self.provider.name}] Moved source → processed: "{processed_file_path.name}"'
            )
            return out_csv_path

        except Exception:
            err_msg = traceback.format_exc()
            self.logger.error(
                f'[{self.provider.name}] Error processing "{file_name}". '
                f'Moving to reprocess folder.\n{err_msg}'
            )
            # Move file to reprocess/ for manual intervention
            try:
                os.makedirs(reprocess_dir, exist_ok=True)
                reprocess_file_path = self._unique_dest_path(reprocess_dir, file_name)
                shutil.move(str(temp_file_path), str(reprocess_file_path))
                self.logger.warning(
                    f'[{self.provider.name}] Moved "{file_name}" to reprocess folder: "{reprocess_file_path}"'
                )
            except Exception:
                self.logger.error(
                    f'[{self.provider.name}] Could not move "{file_name}" to reprocess folder!\n'
                    + traceback.format_exc()
                )
            return None
