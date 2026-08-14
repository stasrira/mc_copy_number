import logging
from pathlib import Path


class FileRecord:
    def __init__(self, source_file: str, provider_name: str):
        self.source_file = source_file
        self.provider_name = provider_name
        self.alignment_output = None   # Path or None
        self.counts_output = None      # Path or None
        self.alignment_ok = False
        self.counts_ok = False
        self.errors = []               # list of str (message text only)
        self.warnings = []             # list of str


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
