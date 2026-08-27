"""utils/log_cleanup.py

Generic log-file retention/cleanup utility.

Deletes files older than a configured retention period from a directory. Intentionally has no
dependency on this project's ConfigData/entry-point conventions (only stdlib + a logger), so it
can be dropped into other projects that manage their own log directory as-is.
"""
from dataclasses import dataclass, field
from pathlib import Path
import time


@dataclass
class LogCleanupResult:
    scanned: int = 0
    deleted: list = field(default_factory=list)   # list of str(Path) actually deleted
    errors: list = field(default_factory=list)     # list of (str(Path), error message) tuples
    bytes_freed: int = 0

    @property
    def deleted_count(self) -> int:
        return len(self.deleted)

    @property
    def error_count(self) -> int:
        return len(self.errors)


def cleanup_old_logs(log_dir: Path, retention_days: int, logger, pattern: str = '*.log') -> LogCleanupResult:
    """Delete files matching *pattern* directly under *log_dir* that are older than *retention_days*.

    Age is judged by last-modified time (mtime), not filesystem creation/change time: on Unix,
    ``st_ctime`` is metadata-change time, not creation time, so it isn't a reliable "when was this
    written" signal across platforms. Log files are opened, written, and closed once, so mtime is
    an accurate stand-in for when each file was produced.

    A per-file error (permission denied, file removed concurrently by another run, ...) is caught,
    logged, and skipped rather than aborting the whole cleanup — one bad file shouldn't block the
    rest from being cleaned up.

    :param log_dir: directory to scan (non-recursive).
    :param retention_days: files last modified more than this many days ago are deleted.
    :param logger: application logger.
    :param pattern: glob pattern for files to consider (default ``'*.log'``).
    :returns: a LogCleanupResult summarizing what happened.
    """
    result = LogCleanupResult()
    log_dir = Path(log_dir)

    if not log_dir.is_dir():
        logger.warning(f'Log cleanup: directory not found, nothing to do: "{log_dir}"')
        return result

    cutoff = time.time() - (retention_days * 86400)

    for file_path in sorted(log_dir.glob(pattern)):
        if not file_path.is_file():
            continue
        result.scanned += 1

        try:
            stat = file_path.stat()
        except OSError as e:
            logger.warning(f'Log cleanup: could not stat "{file_path}": {e}')
            result.errors.append((str(file_path), str(e)))
            continue

        if stat.st_mtime >= cutoff:
            continue

        try:
            file_path.unlink()
            result.deleted.append(str(file_path))
            result.bytes_freed += stat.st_size
            logger.info(f'Log cleanup: deleted "{file_path}" (last modified {time.ctime(stat.st_mtime)}).')
        except OSError as e:
            logger.warning(f'Log cleanup: failed to delete "{file_path}": {e}')
            result.errors.append((str(file_path), str(e)))

    return result
