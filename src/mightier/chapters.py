"""Create and rename chapter files while preserving the user's TOML document."""

import fcntl
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path

import tomlkit
from tomlkit.exceptions import ParseError

from mightier.books import CONFIG, BookError, require_slug, require_text
from mightier.logging_config import logger
from mightier.output import success


@contextmanager
def project_lock(project):
    descriptor = os.open(
        project.root / ".mightier-project.lock", os.O_CREAT | os.O_RDWR, 0o600
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def config_state(project):
    path = project.root / CONFIG
    if path.is_symlink():
        raise BookError(
            "Chapter changes require a regular mightier.toml, not a symbolic link."
        )
    original = path.read_bytes()
    project.load_book()
    if path.read_bytes() != original:
        raise BookError("mightier.toml changed during validation. Try again.")
    try:
        document = tomlkit.parse(original.decode("utf-8"))
    except ParseError as error:
        raise BookError(f"Could not update mightier.toml: {error}") from error
    return path, original, document


def write_config(path, expected, replacement):
    """Replace a complete configuration, but never discard a concurrent edit."""
    replacement = (
        replacement.encode("utf-8") if isinstance(replacement, str) else replacement
    )
    descriptor, temporary = tempfile.mkstemp(
        prefix=".mightier-", suffix=".toml", dir=path.parent
    )
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(replacement)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), path.stat().st_mode & 0o777)
        if path.is_symlink() or path.read_bytes() != expected:
            raise BookError(
                "mightier.toml changed during the operation. The concurrent edit was kept."
            )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def destination(project, relative):
    path = project.root / relative
    resolved = path.resolve()
    if not resolved.is_relative_to(project.root) or resolved.is_relative_to(
        project.root / "build"
    ):
        raise BookError(
            "The chapter must stay inside the book folder and outside build/."
        )
    if path.exists() or path.is_symlink():
        raise BookError(f"{relative}: already exists. Nothing was overwritten.")
    return path


def create_file(path, content, mode=None):
    """Exclusive creation avoids overwriting an existing or newly created file."""
    try:
        stream = path.open("xb")
    except FileExistsError as error:
        raise BookError(f"{path}: already exists. Nothing was overwritten.") from error
    try:
        with stream:
            stream.write(content)
            if mode is not None:
                os.fchmod(stream.fileno(), mode)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def remove_created(path, content):
    if not path.exists():
        return
    if path.read_bytes() == content:
        path.unlink()
    else:
        logger.warning("Keeping concurrently edited chapter after rollback: %s", path)


def clean_uncommitted(config, replacement, path, content):
    # A signal or an I/O error can occur after the atomic config replacement.
    # Keep a chapter that is already referenced, or whose commit is uncertain.
    try:
        if config.read_bytes() == replacement:
            return
    except OSError:
        logger.warning(
            "Keeping chapter because its config commit could not be checked: %s", path
        )
        return
    remove_created(path, content)


def new_chapter(project, name, title=None):
    require_slug(name)
    title = (
        title
        if title is not None
        else re.sub(r"^\d+-", "", name).replace("-", " ").capitalize()
    )
    require_text(title, "Chapter title")
    content = f"# {title}\n\n".encode()
    with project_lock(project):
        config, original, document = config_state(project)
        relative = f"chapters/{name}.md"
        path = destination(project, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        document["chapters"].append(relative)
        updated = tomlkit.dumps(document).encode("utf-8")
        create_file(path, content)
        try:
            write_config(config, original, updated)
        except BaseException:
            clean_uncommitted(config, updated, path, content)
            raise
    success(f"Created {path} and appended it to the chapter list.")
    return path


def rename_chapter(project, old, new):
    require_slug(new)
    with project_lock(project):
        config, original, document = config_state(project)
        chapters = list(document["chapters"])
        if old in chapters:
            matches = [chapters.index(old)]
        else:
            matches = [
                index
                for index, value in enumerate(chapters)
                if old in (Path(value).stem, Path(value).name)
            ]
        if not matches:
            raise BookError(
                f"Unknown chapter {old!r}. Use a listed chapter name or book-relative path."
            )
        if len(matches) > 1:
            raise BookError(
                f"Chapter {old!r} is ambiguous. Use its book-relative path from mightier.toml."
            )
        index = matches[0]
        source_relative = Path(chapters[index])
        source = project.root / source_relative
        if source.is_symlink():
            raise BookError(
                "Renaming a symbolic-link chapter is not supported. Edit its configuration manually."
            )
        target_relative = source_relative.with_name(f"{new}.md").as_posix()
        if target_relative == source_relative.as_posix():
            raise BookError("The new chapter name must differ from the old name.")
        target = destination(project, target_relative)
        content = source.read_bytes()
        document["chapters"][index] = target_relative
        updated = tomlkit.dumps(document).encode("utf-8")
        # Keep the source until the destination and its configuration are ready.
        create_file(target, content, source.stat().st_mode & 0o777)
        try:
            write_config(config, original, updated)
        except BaseException:
            clean_uncommitted(config, updated, target, content)
            raise
        try:
            if source.read_bytes() != content:
                raise BookError(
                    "The source chapter changed during the rename. Its edit was kept."
                )
            source.unlink()
        except BaseException:
            # The original still exists. Restore the manifest before removing
            # the new copy. If the manifest changed again, keep both files.
            if source.exists():
                write_config(config, updated, original)
                remove_created(target, content)
            raise
    success(
        f"Renamed {source_relative} to {target_relative}. Chapter order and text were kept."
    )
    return target
