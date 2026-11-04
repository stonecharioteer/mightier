"""Verbose local logs, bounded to 10 MB across the active file and one backup."""

import fcntl
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

FILE_BYTES = 5_000_000
BACKUP_COUNT = 1
RECORD_BYTES = 64_000
LEVELS = ("debug", "info", "warning", "error", "off")
logger = logging.getLogger("mightier")


class ByteRotatingHandler(RotatingFileHandler):
    def __init__(self, filename, **kwargs):
        self.process_lock = os.open(
            Path(filename).parent / ".mightier-log.lock", os.O_CREAT | os.O_RDWR, 0o600
        )
        try:
            super().__init__(filename, **kwargs)
        except Exception:
            os.close(self.process_lock)
            self.process_lock = None
            raise

    def emit(self, record):
        # Preview and editor subprocesses can log in the same folder. Serialize
        # rotation and reopen the active file so no process writes to a backup.
        fcntl.flock(self.process_lock, fcntl.LOCK_EX)
        try:
            if self.stream is not None:
                self.stream.close()
                self.stream = None
            super().emit(record)
        finally:
            fcntl.flock(self.process_lock, fcntl.LOCK_UN)

    def close(self):
        super().close()
        if getattr(self, "process_lock", None) is not None:
            os.close(self.process_lock)
            self.process_lock = None

    def _open(self):
        stream = super()._open()
        os.chmod(self.baseFilename, 0o600)
        return stream

    def format(self, record):
        raw = super().format(record).encode("utf-8", errors="replace")
        if len(raw) > RECORD_BYTES:
            return (
                raw[: RECORD_BYTES - 32].decode("utf-8", errors="ignore")
                + " [log record truncated]"
            )
        return raw.decode("utf-8")

    def shouldRollover(self, record):
        if self.stream is None:
            self.stream = self._open()
        self.stream.seek(0, 2)
        return (
            self.stream.tell() + len((self.format(record) + "\n").encode("utf-8"))
            >= self.maxBytes
        )


def configure(level="debug", directory=None):
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    logger.propagate = False
    if level == "off":
        logger.addHandler(logging.NullHandler())
        logger.setLevel(logging.CRITICAL + 1)
        return
    logger.setLevel(getattr(logging, level.upper()))
    path = Path(directory or Path.cwd()) / "mightier.log"
    handler = ByteRotatingHandler(
        path, maxBytes=FILE_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-7s %(threadName)s %(name)s: %(message)s"
        )
    )
    logger.addHandler(handler)
    logger.debug(
        "Logging enabled: path=%s level=%s maximum_total_bytes=%d",
        path,
        level,
        FILE_BYTES * 2,
    )
