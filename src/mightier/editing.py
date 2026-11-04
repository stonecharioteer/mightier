"""Open individual records without a second copy or a second data format."""

import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

from mightier.books import CONFIG, ENTITY_FOLDERS, BookError, read_entity, require_slug
from mightier.output import success


def editor_arguments(path, line=1):
    selected = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if selected:
        arguments = shlex.split(selected)
    else:
        executable = shutil.which("nvim") or shutil.which("vi")
        arguments = [executable] if executable else []
    if not arguments:
        raise BookError("Set VISUAL or EDITOR to an editor command, or install Neovim.")
    name = Path(arguments[0]).name
    if line > 1 and name in ("nvim", "vim", "vi", "nano", "emacs", "emacsclient"):
        arguments.append(f"+{line}")
    arguments.append(str(path))
    return arguments


def edit(project, kind, key=None):
    line = 1
    if kind == "book":
        path = project.root / CONFIG
        validate = project.config
    elif kind == "reference":
        if not any(entry["id"] == key for entry in project.references()):
            raise BookError(f"Unknown reference {key!r}.")
        path = project.bibliography
        pattern = re.compile(r"(?m)^\s*@\w+\s*[({]\s*" + re.escape(key) + r"\s*,")
        contents = path.read_text(encoding="utf-8")
        match = pattern.search(contents)
        if match:
            line = contents.count("\n", 0, match.start()) + 1
        validate = project.references
    else:
        require_slug(key)
        path = project.metadata_dir / ENTITY_FOLDERS[kind] / f"{key}.md"
        if not path.is_file():
            raise BookError(f"Unknown {kind} {key!r}.")
        validate = lambda: read_entity(kind, path)
    result = subprocess.run(editor_arguments(path, line), cwd=project.root, check=False)
    if result.returncode:
        raise BookError("The editor exited with an error. Any saved edits were kept.")
    try:
        validate()
    except (BookError, OSError, UnicodeError) as error:
        raise BookError(
            f"The saved edit is invalid. It was kept so you can correct it. {error}"
        ) from error
    success(f"Validated {path}.")
