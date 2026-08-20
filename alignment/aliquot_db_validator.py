"""Validates aliquot IDs against the metadata database.

Called at the end of the alignment step for each processed file.
Uses the stored procedure usp_get_aliquot_dataset_main_ids_only with
@include_program_info = 1 to retrieve program information for each aliquot.

Validation rules:
  1. Every aliquot must exist in the database.
  2. If allow_multiple_programs is False: all aliquots must belong to exactly one program.
     If allow_multiple_programs is True: multiple programs are allowed; aliquots are grouped
     by program code for separate Counts output files.
"""

import re

from db.db_connection import MetadataDB

# The stored procedure receives all IDs as a single comma-joined string parameter and splits
# it internally, so an ID containing a comma would corrupt that list even though the query
# itself is now parameterized and safe from SQL injection. \w (Unicode letters/digits/'_') plus
# '.' and '-' covers every aliquot ID format seen in practice (including dotted IDs like
# "BA.ANFO.S01.ANF010M.M01" and accented names like "Naïve-A4") while still rejecting the
# comma delimiter itself along with quotes, semicolons and whitespace. Rejecting anything
# outside this format up front keeps the comma-joined list well-formed and surfaces a clear
# error instead of a confusing DB failure.
_ALIQUOT_ID_RE = re.compile(r'^[\w.-]+$')


def validate_aliquots(
    aliquots: list[str],
    main_cfg,
    logger,
    allow_multiple_programs: bool = False,
) -> tuple[bool, dict[str, list[str]] | None, list[str]]:
    """Validate *aliquots* against the metadata DB.

    :param aliquots:                List of aliquot ID strings to validate.
    :param main_cfg:                ConfigData instance for main_config.yaml.
    :param logger:                  Application logger.
    :param allow_multiple_programs: When True, aliquots spanning multiple programs are accepted
                                    and grouped by program code. When False (default), all aliquots
                                    must belong to exactly one program.
    :returns: ``(ok, program_groups, error_messages)``

        * *ok*             – ``True`` when all aliquots are found and program rules are satisfied.
        * *program_groups* – ``{program_code: [aliquot_id, ...]}`` on success, ``None`` on failure.
        * *error_messages* – List of human-readable error strings (empty on success).
    """
    errors: list[str] = []

    if not aliquots:
        errors.append(
            'Aliquot validation could not run: no aliquot IDs were extracted from the aligned file. '
            'Please verify that the source file contains data rows and that the aliquot ID column '
            'is correctly mapped in the provider configuration.'
        )
        return False, None, errors

    malformed = [a for a in aliquots if not _ALIQUOT_ID_RE.match(a)]
    if malformed:
        errors.append(
            f'Aliquot validation could not run: {len(malformed)} aliquot ID(s) contain unexpected '
            f'characters (only letters, digits, ".", "-" and "_" are allowed): {", ".join(malformed)}. '
            f'Please verify the aliquot ID column in the source file.'
        )
        return False, None, errors

    aliquots_str = ','.join(aliquots)

    db = MetadataDB(main_cfg, logger)
    try:
        # @aliquot_ids is passed as a bound parameter (not interpolated into the SQL text) so
        # it is always treated as data by the driver, never as SQL syntax.
        sql = "exec dbo.usp_get_aliquot_dataset_main_ids_only @aliquot_ids = ?, @include_program_info = 1"
        rows, err = db.exec_query(sql, params=(aliquots_str,))

        if err or rows is None:
            errors.append(
                'Aliquot validation could not be completed because the database query failed. '
                'This is likely a connectivity or configuration issue. '
                'Check the Database/connection settings in main_config.yaml and the DB credentials '
                'in the .env file. See the log for the full error details.'
            )
            return False, None, errors

        # Rule 1: every submitted aliquot must appear in the result set
        found_ids = {row['_aliquot_id'] for row in rows}
        missing = [a for a in aliquots if a not in found_ids]
        if missing:
            errors.append(
                f'Aliquot ID validation failed: {len(missing)} of {len(aliquots)} submitted aliquot(s) '
                f'were not found in the metadata database (only {len(found_ids)} matched). '
                f'Processing cannot continue until all aliquot IDs are registered in the database. '
                f'Please verify the IDs and ensure they have been properly enrolled before resubmitting. '
                f'Unrecognised aliquot(s): {", ".join(missing)}'
            )
            return False, None, errors

        # Build program_groups: {program_code: [aliquot_id, ...]}
        program_groups: dict[str, list[str]] = {}
        for row in rows:
            code = row.get('_program_code') or ''
            program_groups.setdefault(code, []).append(row['_aliquot_id'])

        if not program_groups or (len(program_groups) == 1 and '' in program_groups):
            errors.append(
                'Aliquot validation failed: the database returned no program code for the submitted '
                'aliquots. This indicates a data registration issue — the aliquots may not be fully '
                'enrolled in the system. Please contact the data management team for assistance.'
            )
            return False, None, errors

        # Rule 2: single-program enforcement (when allow_multiple_programs is False)
        if len(program_groups) > 1 and not allow_multiple_programs:
            errors.append(
                f'Aliquot validation failed: the submitted aliquots belong to more than one program '
                f'({", ".join(sorted(program_groups))}). A single processing run must contain aliquots '
                f'from only one program. Please separate the aliquots by program and resubmit each '
                f'group independently. If processing aliquots from multiple programs together is '
                f'intentional, set "allow_multiple_programs: True" in main_config.yaml.'
            )
            return False, None, errors

        programs_summary = ', '.join(f'"{c}" ({len(v)} aliquot(s))' for c, v in sorted(program_groups.items()))
        logger.info(
            f'Aliquot validation passed: {len(aliquots)} aliquot(s) validated across '
            f'{len(program_groups)} program(s): {programs_summary}.'
        )
        return True, program_groups, []

    finally:
        db.close()
