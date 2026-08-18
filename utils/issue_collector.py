import logging
from pathlib import Path


class FileRecord:
    def __init__(self, source_file: str, provider_name: str):
        self.source_file = source_file
        self.provider_name = provider_name
        self.alignment_output = None          # Path or None
        self.counts_output = None             # Path or None
        self.alignment_ok = False
        self.counts_ok = False
        self.alignment_ran = True             # False when alignment step was not executed
        self.counts_ran = True                # False when counts step was not executed
        self.db_validation_ok = False         # True when aliquot DB validation passed
        self.program_code = None              # _program_code when all aliquots share one program
        self.program_groups = {}             # {program_code: [aliquot_id, ...]} from DB validation
        self.counts_outputs = []             # list of (program_code, Path) for each written counts file
        self.launch_param = None             # CLI parameter name used to start standalone run
        self.launch_value = None             # CLI parameter value used to start standalone run
        self.aliquots = []                    # list of aliquot ID strings (populated after alignment)
        self.alignment_aliquot_count = 0      # number of aliquots from the alignment step
        self.counts_aliquot_count = 0         # number of aliquots from the counts step
        self.errors = []                      # list of str (message text only)
        self.warnings = []                    # list of str


class CapturingLogHandler(logging.Handler):
    """Attaches to a logger and captures WARNING/ERROR messages into a FileRecord."""
    def __init__(self, file_record: FileRecord):
        super().__init__()
        self._record = file_record

    def emit(self, log_record):
        msg = log_record.getMessage()
        if log_record.levelno >= logging.ERROR:
            self._record.errors.append(msg)
        elif log_record.levelno == logging.WARNING:
            self._record.warnings.append(msg)
