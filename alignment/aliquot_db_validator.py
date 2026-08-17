"""Validates aliquot IDs against the metadata database.

Called at the end of the alignment step for each processed file.
Uses the stored procedure usp_get_aliquot_dataset_main_ids_only with
@include_program_info = 1 to retrieve program information for each aliquot.

Validation rules:
  1. Every aliquot must exist in the database.
  2. All aliquots must belong to exactly one program (one distinct _program_code).
"""

from db.db_connection import MetadataDB


def validate_aliquots(
    aliquots: list[str],
    main_cfg,
    logger,
) -> tuple[bool, str | None, list[str]]:
    """Validate *aliquots* against the metadata DB.

    :param aliquots:  List of aliquot ID strings to validate.
    :param main_cfg:  ConfigData instance for main_config.yaml.
    :param logger:    Application logger.
    :returns: ``(ok, program_code, error_messages)``

        * *ok*             – ``True`` when all aliquots are found and belong to one program.
        * *program_code*   – The single ``_program_code`` value, or ``None`` on failure.
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

    aliquots_str = ','.join(aliquots)

    db = MetadataDB(main_cfg, logger)
    try:
        sql = (
            f"exec dbo.usp_get_aliquot_dataset_main_ids_only "
            f"@aliquot_ids = '{aliquots_str}', "
            f"@include_program_info = 1"
        )
        rows, err = db.exec_query(sql)

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

        # Rule 2: all aliquots must belong to exactly one program
        program_codes = {row['_program_code'] for row in rows if row.get('_program_code')}
        if not program_codes:
            errors.append(
                'Aliquot validation failed: the database returned no program code for the submitted '
                'aliquots. This indicates a data registration issue — the aliquots may not be fully '
                'enrolled in the system. Please contact the data management team for assistance.'
            )
            return False, None, errors

        if len(program_codes) > 1:
            errors.append(
                f'Aliquot validation failed: the submitted aliquots belong to more than one program '
                f'({", ".join(sorted(program_codes))}). A single processing run must contain aliquots '
                f'from only one program. Please separate the aliquots by program and resubmit each '
                f'group independently. If this is expected, an Analysis Request is required to proceed.'
            )
            return False, None, errors

        program_code = next(iter(program_codes))
        logger.info(
            f'Aliquot validation passed: {len(aliquots)} aliquot(s) validated, '
            f'program code: "{program_code}".'
        )
        return True, program_code, []

    finally:
        db.close()
