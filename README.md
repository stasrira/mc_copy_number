# MC Copy Number Processing

A batch-processing pipeline for mitochondrial copy number (MC Copy Number) data. It ingests
provider-specific Excel files, standardizes them into CSVs, validates aliquot IDs against a SQL
Server metadata database, transposes the data into per-program count tables, and emails a status
report after each run.

There is no long-running service — every entry point is a script meant to be invoked (e.g. by a
scheduled task) and then exit.

## Requirements

- Python 3.12
- In production, a conda environment named `python3.12.10_odbc` providing the ODBC driver needed
  by `pyodbc` (used for the metadata DB connection). The `run_*.sh` wrapper scripts (see below)
  validate this environment exists before activating it.
- A project-local virtualenv at `.venv` with the packages from `requirements.txt` installed.

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Then create the two git-ignored config files (see `configs/location_config_example.yaml` and
`.env.example` for the full list of settings and comments):

```bash
cp configs/location_config_example.yaml configs/location_config.yaml
cp .env.example .env
```

- `configs/location_config.yaml` — machine-specific absolute paths (studies directory, requests
  directory, log directory).
- `.env` — SMTP settings, status-email addresses, and `MC_DB_*` metadata database credentials.

At least one provider must be configured under `configs/providers/<ProviderName>/provider_config.yaml`
before the alignment step will find anything to process (see `configs/providers/NairLab/` for a
working example). Providers are auto-discovered by folder at runtime.

For development/testing:

```bash
pip install -r requirements-dev.txt
pytest
```

No test talks to a real DB or SMTP server — `pyodbc`/`yagmail` are mocked at their call boundary.

## Running

There are five entry points, each a self-contained script that loads its own config, sets up its
own logger, and always sends its own status email at the end (even on failure) — the fifth (log
cleanup) is housekeeping rather than part of the pipeline, but still reports what it did by email.
Each has a `run_*.sh` wrapper that activates the `python3.12.10_odbc` conda environment (failing with a clear error if
`conda activate` itself fails, e.g. because the environment doesn't exist) and `.venv`, runs the
corresponding Python script, and cleans up afterwards — this is the wrapper pattern used in
production.

### Automated entry points (crontab)

These two are meant to be scheduled and are safe to run unattended and repeatedly — each is a
no-op if there's nothing new to pick up, and the folder-claiming logic (`ready/` →
`temp_processing/` → `processed/`/`work/`) means overlapping/duplicate runs won't double-process a
file.

- **`run_mc_copy_number.sh`** → `mc_copy_number.py` — the main pipeline. For each configured
  provider, aligns any new files sitting in that provider's `ready/` folder, then (unless skipped —
  see `Alignment/allow_automated_counts_processing` and per-file DB validation outcome in
  `configs/main_config.yaml`) runs the Counts step against the aligned output. Sends one status
  email per run.
- **`run_mc_copy_number_requests.sh`** → `mc_copy_number_requests.py` — processes ad-hoc request
  Excel files (re-runs Counts against an already-aligned `raw_data` CSV, optionally overriding the
  program code or skipping aliquot validation). Scans `Location/requests_dir`'s `ready/` folder;
  each request file goes through the same `ready/` → `processing_temp/` → `processed/`/`work/`
  lifecycle. Sends one status email per request file processed, with one section per entry in that
  file.

Example crontab entries (adjust paths and schedule as needed):

```cron
# Run the main alignment+counts pipeline every 15 minutes
*/15 * * * * bash -c '/path/to/mc_copy_number_processing/run_mc_copy_number.sh' 2>&1 | logger -t mc_copy_number_pipeline

# Process any queued request files every 15 minutes
*/15 * * * * bash -c '/path/to/mc_copy_number_processing/run_mc_copy_number_requests.sh' 2>&1 | logger -t mc_copy_number_requests
```

If your site needs environment setup before the wrapper script itself (e.g. loading modules via
Lmod), fold it into the same `bash -c '...'` chain rather than tacking it on before the crontab
line — a trailing pipe/redirect after a `;`-separated command list only binds to the *last*
command, not the whole chain, so anything the setup commands print would otherwise bypass it:

```cron
*/15 * * * * bash -c 'source /etc/profile.d/z00_lmod.sh; ml purge; ml anaconda3; /path/to/mc_copy_number_processing/run_mc_copy_number.sh' 2>&1 | logger -t mc_copy_number_pipeline
```

Why redirect at all: cron mails you the combined stdout/stderr of *every* run that produces any
output, regardless of exit code. `Logging/mirror_to_stdout` (`configs/main_config.yaml`) mirrors
every log line to stdout by default, so a routine, fully successful run still produces output and
still gets mailed — that's unrelated to whether anything actually went wrong. Piping into `logger`
moves that output into syslog instead (tagged for later retrieval), so cron sees no output and
stays silent on success; it also captures anything printed by the wrapper script itself before the
Python logger exists yet (e.g. a missing conda environment). Real status reporting still happens
through the app's own status email — this is only about suppressing cron's separate,
output-triggered mail.

To review what a job printed afterward: `journalctl -t mc_copy_number_pipeline` (swap in whatever
tag you used). `>> /path/to/a/file.log 2>&1` instead of `| logger -t ...` works the same way if you
prefer a plain file over syslog — just be aware `>>` (append) grows that file forever, since it's
separate from the pipeline's own per-run, `run_mc_copy_number_log_cleanup.sh`-managed logs under
`Location/logs`. Either way, confirm your host actually captures it before relying on it: `echo hi
| logger -t <tag>` then `journalctl -t <tag>`.

### Scheduled maintenance entry point

- **`run_mc_copy_number_log_cleanup.sh`** → `mc_copy_number_log_cleanup.py` — deletes log files
  under `Location/logs` last modified more than `LogCleanup/retention_days` days ago (see
  Configuration reference below). Housekeeping, not part of the pipeline, but still sends its own
  status email listing every file deleted (and any that failed to delete), and logs to its own
  `log_cleanup_<timestamp>.log`. Meant to run on its own, much less frequent schedule — separate
  from the two automated entries above:

  ```cron
  # Clean up old log files once a week
  0 3 * * 0 bash -c '/path/to/mc_copy_number_processing/run_mc_copy_number_log_cleanup.sh' 2>&1 | logger -t mc_copy_number_log_cleanup
  ```

  (Same redirect pattern and rationale as the automated entries above — fold any needed
  environment-setup commands into the same `bash -c '...'` chain.)

### Manual / ad-hoc entry points

These two are normally run by hand for troubleshooting or backfills, but nothing about them
prevents scheduling them too (e.g. a dedicated cron job that reprocesses a specific known path) —
they're just not part of the scheduled entries above.

- **`run_mc_copy_number_alignment.sh`** → `mc_copy_number_alignment.py` — Alignment (Step 1) only.
  When run standalone like this, the Counts step never runs afterwards regardless of config. Useful
  for re-running just the alignment step, e.g. after fixing a provider config.
- **`run_mc_copy_number_counts.sh`** → `mc_copy_number_counts.py` — Counts (Step 2) only, against an
  already-aligned CSV. Requires exactly one of the following, forwarded through the wrapper as
  extra CLI args:

  ```bash
  ./run_mc_copy_number_counts.sh --input_file path/to/aligned.csv
  ./run_mc_copy_number_counts.sh --input_dir path/to/dir   # dir must contain exactly one CSV
  ```

  Useful for re-running Counts against a specific `raw_data` CSV without going through Alignment
  again (e.g. after fixing a config issue that only affects Counts).

### Without the wrapper scripts

Any entry point can also be run directly against an already-activated environment (e.g. during
local development), bypassing the conda validation and `.venv` activation the wrappers do:

```bash
source .venv/bin/activate
python mc_copy_number.py
python mc_copy_number_alignment.py
python mc_copy_number_counts.py --input_file path/to/aligned.csv
python mc_copy_number_counts.py --input_dir path/to/dir
python mc_copy_number_requests.py
python mc_copy_number_log_cleanup.py
```

## Pipeline overview

- **Alignment** (`alignment/`, `providers/`) — extracts each provider's Excel file (layout driven
  entirely by that provider's `provider_config.yaml`), renames columns to the canonical schema in
  `configs/main_config.yaml`'s `Alligned_file_schema/fields`, and writes a standardized CSV under
  `raw_data/`. The output sub-folder and CSV file name are derived from the source file's stem with
  whitespace collapsed to underscores (`utils.common.sanitize_output_name`) — the source file
  itself keeps its original name (spaces included) when moved to `processed/`/`reprocess/`. If
  `Alignment/validate_aliquots_against_db` is on, extracted aliquot IDs are then validated against
  the metadata DB and grouped by program code.
- **Counts** (`mc_copy_number_counts.py`) — transposes an aligned CSV (aliquot IDs become columns)
  and writes the result under `processed_data/<program_code>/...`, one output file per program when
  a file spans multiple programs.
- **Requests** (`mc_copy_number_requests.py`, `requests/`) — a separate lifecycle for re-running
  Counts against existing `raw_data` CSVs via an Excel request file (columns: `Raw_data_source`,
  optional `Program_code`, optional `Skip_aliquot_validation`).
- **Log cleanup** (`mc_copy_number_log_cleanup.py`, `utils/log_cleanup.py`) — housekeeping, not part
  of the pipeline: deletes log files under `Location/logs` older than `LogCleanup/retention_days`
  and emails a status report listing every file it deleted (and any it couldn't); see
  [Scheduled maintenance entry point](#scheduled-maintenance-entry-point) above. Its status email
  never goes to `MC_EMAIL_ADDITIONAL_TO`, regardless of `Email/include_additional_emails` — see below.
- **Status emails** — every run ends with a Jinja2-rendered HTML email (`templates/`) sent via SMTP
  (`utils/send_email.py`), gated by `Email/send_emails`. The subject is optionally tagged with
  `[Location/environment_name]` (e.g. `[production]`) when that key is set, and
  `MC_EMAIL_ADDITIONAL_TO` recipients are only included when `Email/include_additional_emails` is on
  *and* the run actually had something to report (at least one file/entry attempted) — a run that
  found nothing to do at all goes to `MC_EMAIL_TO` only. The log cleanup status email is an
  exception: it never includes `MC_EMAIL_ADDITIONAL_TO`, regardless of that setting.

See `CLAUDE.md` for the full architecture writeup (file-by-file responsibilities, config layering,
and conventions).

## Configuration reference

| File | Git-ignored | Purpose |
|---|---|---|
| `configs/main_config.yaml` | No | Behavior toggles and schema definitions shared across environments. |
| `configs/location_config.yaml` | Yes | Machine-specific absolute paths (studies dir, requests dir, logs dir). Copy from `configs/location_config_example.yaml`. |
| `configs/providers/<Provider>/provider_config.yaml` | No | One per data provider; source folder name, file pattern, Excel extraction layout. |
| `.env` | Yes | SMTP settings, status-email addresses, `MC_DB_*` DB credentials. Copy from `.env.example`. |

`ConfigData` (`utils/configuration.py`) resolves dotted paths like `Alignment/allow_multiple_programs`
against the parsed YAML. A missing key returns `None` silently, so most settings below have a
fallback used when the key is absent — noted per setting.

### `configs/main_config.yaml`

Checked into git — no secrets or machine-specific paths belong here.

**`Logging`**

| Key | Meaning |
|---|---|
| `log_level` | Python logging level for the log file, e.g. `INFO` or `DEBUG`. |
| `mirror_to_stdout` | When `True`, every log line written to the log file is also printed to stdout. |

**`LogCleanup`** — used by `mc_copy_number_log_cleanup.py` only.

| Key | Meaning | Default if absent |
|---|---|---|
| `retention_days` | Log files directly under `Location/logs` last modified more than this many days ago are deleted. | `60` — but this key is expected to be tuned per-site over time; the code fallback isn't required to track whatever value is currently checked into `main_config.yaml`. |

**`Database`** — used only when `Alignment/validate_aliquots_against_db` is `True`. `connection/mdb_conn_str`
is an ODBC connection-string template; the `db_plh_*` keys are the placeholder tokens inside it, and each
`env_db_*` key names the environment variable (from `.env`) substituted for the matching placeholder. You
normally don't need to change any of this — it only needs editing if the ODBC connection string format itself
changes.

**`Email`**

| Key | Meaning |
|---|---|
| `send_emails` | Master on/off switch for sending the status email at all. |
| `email_subject_prefix` | Text prepended to every status email subject line (after the optional `[Location/environment_name]` tag — see below). |
| `include_additional_emails` | When `True`, pipeline/requests status emails also go to `MC_EMAIL_ADDITIONAL_TO` (`.env`) in addition to `MC_EMAIL_TO` — but only for a run that actually attempted at least one file/entry (attempted-but-failed still counts; an empty `ready/` folder does not). Defaults to `False` when unset. Does not apply to the log cleanup status email, which never goes to `MC_EMAIL_ADDITIONAL_TO`. |

Sender/recipient addresses themselves live in `.env`, not here, since this file is checked into git.

**`Alligned_file_schema/fields`** — maps a stable `schema_key` (referenced by every provider config's
column mapping, e.g. `aliquot_id`, `mtdna_mean`, `mtdna_se`) to the actual column name written to the
aligned CSV and expected on Counts input. Rename an output column by changing the value here only — no
provider config changes needed.

**`Csv_output`**

| Key | Meaning |
|---|---|
| `enable_utf8_bom` | When `True` (default), a CSV is written with a UTF-8 BOM *only if* it contains non-ASCII characters, so Excel renders them correctly. Set `False` to always write plain UTF-8 — e.g. if downstream tooling (like base R's `read.csv()` without `fileEncoding='UTF-8-BOM'`) isn't BOM-aware. |

**`Alignment`**

| Key | Meaning | Default if absent |
|---|---|---|
| `run_folders_dir` | Sub-directory under `Location/mitCopyN_studies_dir` holding provider run folders. | `runFolders` |
| `raw_data_dir` | Sub-directory under `Location/mitCopyN_studies_dir` where aligned CSVs are written. | `raw_data` |
| `ready_subfolder` | Sub-folder inside each provider's run folder (`runFolders/<Provider>/`) holding files ready to process. | `ready` |
| `processing_temp_subfolder` | Sub-folder a file is atomically renamed into while claimed for processing (prevents double-processing by concurrent runs). | `temp_processing` |
| `processed_subfolder` | Sub-folder a source file is moved to after successful processing. | `processed` |
| `reprocess_subfolder` | Sub-folder a source file is moved to after a processing failure, for manual intervention. | `work` |
| `providers_config_dir` | Path (relative to project root) to the directory holding per-provider config sub-folders. | `configs/providers` |
| `validate_aliquots_against_db` | When `True`, aliquot IDs extracted from each aligned file are validated against the metadata DB (`Database` settings + `MC_DB_*` in `.env` required). | — |
| `allow_automated_counts_processing` | When `True` (and DB validation is on and passed), the Counts step runs automatically after Alignment in `mc_copy_number.py`. No effect when running `mc_copy_number_alignment.py` standalone — Counts never runs there regardless. | — |
| `allow_multiple_programs` | When `False`, every aliquot in a file must belong to exactly one program or the file fails. When `True`, aliquots spanning multiple programs are accepted and Counts writes one output file per program. | `False` |

**`Requests`**

| Key | Meaning | Default if absent |
|---|---|---|
| `request_sheet_name` | Excel sheet name to read from request files. Blank/omitted uses the first sheet; falls back to the first sheet (with a warning) if the named sheet isn't found. | first sheet |
| `ready_subfolder` | Sub-folder under `Location/requests_dir` holding request files ready to process. | `ready` |
| `processing_temp_subfolder` | Sub-folder a request file is claimed into while being processed. | `processing_temp` |
| `processed_subfolder` | Sub-folder a request file is moved to after successful processing. | `processed` |
| `reprocess_subfolder` | Sub-folder a request file is moved to after a processing failure. | `work` |

**`Counts`**

| Key | Meaning | Default if absent |
|---|---|---|
| `processed_data_dir` | Sub-directory under `Location/mitCopyN_studies_dir` where transposed count tables are written. | `processed_data` |
| `output_path_depth` | Number of parent folder levels preserved from the input CSV path when building the output path under `processed_data_dir`. `depth=1`: `<timestamp_stem>/file.csv` → `processed_data/<program>/<timestamp_stem>/file.csv`. `depth=2`: `raw_data/<timestamp_stem>/file.csv` → `processed_data/<program>/raw_data/<timestamp_stem>/file.csv`. | `1` |

### `configs/location_config.yaml`

Not checked into git (machine-specific absolute paths) — copy from `configs/location_config_example.yaml`.

| Key | Meaning |
|---|---|
| `Location/mitCopyN_studies_dir` | Absolute path to the root directory holding `runFolders/`, `raw_data/`, and `processed_data/` for this environment. |
| `Location/requests_dir` | Absolute path to the root directory holding the request-file lifecycle folders (`ready/`, `processing_temp/`, `processed/`, `work/`). |
| `Location/logs` | Path to the folder where log files are written; relative to the project root if not absolute. Also the directory `mc_copy_number_log_cleanup.py` cleans up. |
| `Location/environment_name` | Optional environment identifier (e.g. `production`, `staging`). When set, shown as a `[name]` prefix on every status email subject. Leave unset/blank to omit it entirely. |

### `.env`

Not checked into git (secrets and environment-specific addresses) — copy from `.env.example`. Loaded via
`python-dotenv` at the top of every entry-point script.

| Variable | Meaning |
|---|---|
| `SRD_SMTP_SERVER` | SMTP server hostname used to send status emails. |
| `SRD_SMTP_SERVER_PORT` | SMTP server port. Falls back to `25` (plaintext SMTP) if unset or non-numeric. |
| `MC_EMAIL_FROM` | Sender address for status emails. |
| `MC_EMAIL_TO` | Comma-separated recipient address(es) for status emails. |
| `MC_EMAIL_ADDITIONAL_TO` | Comma-separated additional recipient address(es) for pipeline/requests status emails, used only when `Email/include_additional_emails` is `True` in `main_config.yaml`. Never used by the log cleanup status email. |
| `MC_DB_DRIVER` | ODBC driver name for the metadata DB connection, e.g. `ODBC Driver 18 for SQL Server`. |
| `MC_DB_SERVER` | Metadata DB server hostname. |
| `MC_DB_NAME` | Metadata DB database name. |
| `MC_DB_USER_NAME` | Metadata DB username. |
| `MC_DB_USER_PWD` | Metadata DB password. |

The five `MC_DB_*` variables are only required when `Alignment/validate_aliquots_against_db` is `True` in
`main_config.yaml`; their names are configurable indirection points (see `Database/connection/env_db_*` in
`main_config.yaml`) rather than being hardcoded into the DB connection code.

### `configs/providers/<ProviderName>/provider_config.yaml`

One per data provider, auto-discovered by folder under `Alignment/providers_config_dir`. Not
machine-specific — checked into git. See `configs/providers/NairLab/provider_config.yaml` for a complete
working example.

**`provider`**

| Key | Meaning |
|---|---|
| `name` | Provider name, used in logs and status emails. |
| `source_folder_name` | Name of this provider's folder under `runFolders/` (i.e. `runFolders/<source_folder_name>/`). |
| `type` | Which extraction class handles this provider. Currently only `excel` (`providers/excel_provider.py`) is registered in `providers/provider_factory.py`'s `_PROVIDER_REGISTRY`. |
| `file_pattern` | Glob pattern (e.g. `"*.xlsx"`) matched against files in the provider's `ready/` folder. |

**`extraction`**

| Key | Meaning | Default if absent |
|---|---|---|
| `sheet_name` | Excel sheet to read. `null`/omitted uses the active sheet. | active sheet |
| `header_row` | 1-indexed row number containing column headers. | `1` (logged as a warning) |
| `data_start_row` | 1-indexed first row of actual data. | `header_row + 1` (logged as a warning) |
| `data_end_strategy` | How to detect the end of data rows. Only `first_empty` is currently implemented: stop when `data_end_anchor_field`'s value is empty. | `first_empty` |
| `data_end_anchor_field` | Output field name (a `schema_key`, e.g. `aliquot_id`) watched for emptiness under `first_empty`. | — |
| `columns` | Map of `schema_key` → `{source_header, column_index}`. `column_index` is the 1-based Excel column number (A=1, B=2, ...); `source_header` is the header text expected in that column, checked at read time and logged as a warning on mismatch (not a hard failure). Every `schema_key` used here must also exist under `Alligned_file_schema/fields` in `main_config.yaml`. |
