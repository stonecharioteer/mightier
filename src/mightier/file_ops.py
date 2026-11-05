"""Publish complete files without discarding existing or concurrent content."""

import os
import tempfile
from pathlib import Path

from mightier.books import BookError
from mightier.logging_config import logger


def create_file(path, content, mode=None):
    """Prepare a private copy, then publish it with an exclusive hard link."""
    descriptor, temporary = tempfile.mkstemp(
        prefix=".mightier-", suffix=path.suffix, dir=path.parent
    )
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            if mode is not None:
                os.fchmod(stream.fileno(), mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise BookError(
                f"{path}: already exists. Nothing was overwritten."
            ) from error
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "Could not remove temporary copy: %s", temporary, exc_info=True
            )


def atomic_write(path, expected, replacement):
    replacement = (
        replacement.encode("utf-8") if isinstance(replacement, str) else replacement
    )
    descriptor, temporary = tempfile.mkstemp(
        prefix=".mightier-", suffix=path.suffix, dir=path.parent
    )
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), path.stat().st_mode & 0o777)
            stream.write(replacement)
            stream.flush()
            os.fsync(stream.fileno())
        if path.is_symlink() or path.read_bytes() != expected:
            raise BookError(
                f"{path.name} changed during the operation. The concurrent edit was kept."
            )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
