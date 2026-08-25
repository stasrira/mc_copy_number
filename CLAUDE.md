# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A batch-processing pipeline for mitochondrial copy number (MC Copy Number) data. It ingests
provider-specific Excel files, standardizes them into CSVs, validates aliquot IDs against a SQL
Server metadata database, transposes the data into per-program count tables, and emails a status
report after each run. There is no long-running service — every entry point is a script meant to
be invoked (e.g. by a scheduled task) and then exit.

## Running

```bash
./run.sh                              # activates .venv and runs the full pipeline (alignment + counts)
python mc_copy_number.py              # same, without the venv wrapper
python mc_copy_number_alignment.py    # alignment step only (Step 1)
python mc_copy_number_counts.py --input_file path/to/aligned.csv   # counts step only, standalone
python mc_copy_number_counts.py --input_dir path/to/dir            # same, dir must contain exactly one CSV
python mc_copy_number_requests.py     # process ad-hoc request files (re-run counts for existing raw_data CSVs)
```

Setup: `pip install -r requirements.txt`, create `.env` from `.env.example` (SMTP + `MC_DB_*` DB
credentials) and `configs/location_config.yaml` from `configs/location_config_example.yaml`
(both git-ignored — they hold machine-specific secrets/absolute paths).

Tests: `pip install -r requirements-dev.txt`, then `pytest`. Tests live under `tests/unit/` and
`tests/integration/` (see `IGNORE__temp/test_plan.md` for the full test plan and rollout order).
No test talks to a real DB or SMTP server — `pyodbc`/`yagmail` are mocked at their call boundary.

## Pipeline architecture

Three independent entry points, each self-contained (loads its own config, sets up its own logger,
sends its own status email at the end):

- **`mc_copy_number.py`** — orchestrates Alignment → Counts → status email in one run. Skips Counts
  when nothing aligned, when `Alignment/allow_automated_counts_processing` is off, or (per-file) when
  `Alignment/validate_aliquots_against_db` is on and DB validation failed for that file.
- **`mc_copy_number_alignment.py`** — Step 1 only. Also runnable standalone (`main()`), in which case
  Counts never runs regardless of config.
- **`mc_copy_number_counts.py`** — Step 2 only. Runnable standalone via `--input_file`/`--input_dir`
  against an already-aligned CSV in `raw_data`. `process_counts_input()` is the shared end-to-end
  helper (validate aligned CSV format → extract aliquots → DB-validate → apply program override →
  run counts) reused by both the standalone CLI and `requests/request_processor.py`.
- **`mc_copy_number_requests.py`** — a separate lifecycle for re-running Counts against existing
  `raw_data` CSVs via an Excel "request file" (columns: `Raw_data_source`, optional `Program_code`,
  optional `Skip_aliquot_validation`). One status email per request file, one section per entry.

### Step 1: Alignment (`alignment/`, `providers/`)

For each provider sub-folder found under `configs/providers/*/provider_config.yaml`
(`_discover_providers` in `mc_copy_number_alignment.py`), scans that provider's `ready/` folder
under `runFolders/<provider>/` for files matching `provider/file_pattern`. Each file goes through
`AlignmentFileProcessor.process_file()` (`alignment/file_processor.py`):

1. Atomically claim it — `os.rename` into `temp_processing/` (this is how concurrent/duplicate runs
   avoid double-processing; `FileNotFoundError`/`PermissionError` on the rename are treated as
   "someone else has it" / "it's open in Excel", not errors).
2. Extract via a provider class (`providers/` — currently only `ExcelProvider`, driven entirely by
   its YAML `extraction` block: header row, data start row, column-index → schema-key mapping, and
   an end-of-data anchor column). Provider type is looked up in
   `providers/provider_factory.py`'s `_PROVIDER_REGISTRY`; add new formats by writing a
   `BaseProvider` subclass and registering it there.
3. Rename extracted columns from schema keys to canonical names via
   `Alligned_file_schema/fields` in `main_config.yaml` — this is the layer of indirection that lets
   every provider config reference a stable `schema_key` (e.g. `aliquot_id`, `mtdna_mean`) while the
   actual output column name is controlled in one place.
4. Write the standardized CSV to a fresh timestamped sub-folder under `raw_data/`.
5. Move the source file to `processed/` (success) or `reprocess/` a.k.a. `work` (any failure after
   step 1) for manual intervention. Destination name collisions get a `(1)`, `(2)`, ... suffix
   (`AlignmentFileProcessor._unique_dest_path`).

Each source file is tracked by a `FileRecord` (`utils/issue_collector.py`); a `CapturingLogHandler`
is attached to the logger for the duration of processing that file so all WARNING/ERROR log lines
get captured onto the record for the eventual status email, without threading error state through
every function call.

After alignment, if `Alignment/validate_aliquots_against_db` is enabled, aliquot IDs extracted from
the output CSV are validated against the metadata DB (`alignment/aliquot_db_validator.py`) via the
stored procedure `usp_get_aliquot_dataset_main_ids_only`. Every aliquot must exist in the DB; if
`Alignment/allow_multiple_programs` is false, all aliquots in a file must belong to exactly one
program, otherwise they're grouped into `program_groups: {program_code: [aliquot_id, ...]}` for
per-program output splitting downstream. Aliquot IDs are restricted to `^[\w.-]+$` before being
comma-joined for the stored proc call (the proc takes one comma-separated string param) — this is a
defense-in-depth measure since the SQL itself is already parameterized.

### Step 2: Counts (`mc_copy_number_counts.py`)

Reads an aligned CSV, validates it has all columns from `Alligned_file_schema/fields`, transposes it
(aliquot IDs become columns, measurement fields become rows), and writes it under
`processed_data/<program_code>/...` (`Counts/output_path_depth` controls how many parent folders of
the input path are preserved in the output path). When a file spans multiple programs
(`program_groups` has >1 entry), one output file is written per program, each filtered to that
program's aliquots via `aliquot_filter`.

CSV write encoding (`utils/common.py:resolve_csv_write_encoding`) is `utf-8-sig` (BOM) only when the
data contains non-ASCII characters *and* `Csv_output/enable_utf8_bom` is on — plain `utf-8`
otherwise. This is deliberate: Excel needs the BOM to render non-ASCII correctly, but the BOM
confuses non-BOM-aware tools (e.g. base R's `read.csv()`), so it's added conditionally rather than
always. Files affected are tracked in `record.bom_applied_paths` and surfaced as one aggregated note
in the status email rather than a warning per file.

### Config layering

- `configs/main_config.yaml` — checked into git; behavior toggles and schema definitions shared
  across environments.
- `configs/location_config.yaml` — **not** checked into git (see `.gitignore`); machine-specific
  absolute paths (`Location/mitCopyN_studies_dir`, `Location/logs`, `Location/requests_dir`). Copy
  from `configs/location_config_example.yaml` per environment.
- `configs/providers/<ProviderName>/provider_config.yaml` — one per data provider, auto-discovered
  by folder; defines the provider's source folder name, file pattern, and Excel extraction layout.
- `.env` — **not** checked into git; SMTP settings and `MC_DB_*` DB credentials, loaded via
  `python-dotenv`. Copy from `.env.example` per environment. `ConfigData`
  (`utils/configuration.py`) resolves dotted paths like `Alignment/allow_multiple_programs` against
  a parsed YAML dict; `get_value` returns `None` silently for any missing key/path.
- `db/db_connection.py`'s `MetadataDB._build_conn_str` builds the ODBC connection string by taking
  the `Database/connection/mdb_conn_str` template from `main_config.yaml` and substituting its
  placeholder tokens with values read from the environment variables named in
  `Database/connection/env_db_*`.

### Status emails (`utils/common.py`, `templates/`)

Every entry point ends by calling `send_status_email` (or `send_request_status_email`), which
renders Jinja2 templates (`templates/file_status.html` per file/entry, wrapped in
`templates/pipeline_status.html` or `templates/request_status.html`) and sends via
`utils/send_email.py` (yagmail over SMTP) — controlled by `Email/send_emails` in `main_config.yaml`.
Subject line reflects whether any file had errors/warnings. Email body newlines are stripped
(`clean_email_body`) to avoid Outlook rendering issues.

## Conventions worth knowing

- Every entry-point script's `main()` builds its own `logger`/`main_cfg`/`loc_cfg` via
  `utils.common.initialize_run()`, and always sends a status email in a `finally`-like pattern even
  when something failed early — errors are captured onto a placeholder `FileRecord` rather than
  raised, since these are unattended scheduled runs with no one watching stdout.
- Any SQL touching user/file-derived data must go through `?`-bound `params` in
  `MetadataDB.exec_query`, never string interpolation (see `47d332f` — a prior injection fix).
- Folder lifecycle pattern (`ready/` → `temp_processing/`/`processing_temp/` → `processed/` or
  `reprocess/`/`work/`) is repeated for both provider run folders and request files; it exists so
  concurrent/repeated runs can't double-process a file and partial failures are routed somewhere a
  human will look.
