import os
import shutil
import time
import traceback
from pathlib import Path

import pandas as pd

from providers.base_provider import BaseProvider


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

    def __init__(self, provider: BaseProvider, raw_data_dir: str, logger, schema_map: dict = None):
        self.provider = provider
        self.raw_data_dir = Path(raw_data_dir)
        self.logger = logger
        self.schema_map = schema_map or {}

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
                reprocess_file_path = reprocess_dir / file_name
                shutil.move(str(temp_file_path), str(reprocess_file_path))
                self.logger.warning(
                    f'[{self.provider.name}] Moved "{file_name}" to reprocess folder: "{reprocess_dir}"'
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
            df.to_csv(out_csv_path, index=False)
            self.logger.info(
                f'[{self.provider.name}] Saved {len(df)} rows → "{out_csv_path}"'
            )

            # --- Step 5: move source to processed ---
            os.makedirs(processed_dir, exist_ok=True)
            processed_file_path = processed_dir / file_name
            shutil.move(str(temp_file_path), str(processed_file_path))
            self.logger.info(
                f'[{self.provider.name}] Moved source → processed: "{file_name}"'
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
                reprocess_file_path = reprocess_dir / file_name
                shutil.move(str(temp_file_path), str(reprocess_file_path))
                self.logger.warning(
                    f'[{self.provider.name}] Moved "{file_name}" to reprocess folder: "{reprocess_dir}"'
                )
            except Exception:
                self.logger.error(
                    f'[{self.provider.name}] Could not move "{file_name}" to reprocess folder!\n'
                    + traceback.format_exc()
                )
            return None
