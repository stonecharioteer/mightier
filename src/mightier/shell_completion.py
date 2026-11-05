"""Read-only shell completion based on the selected book's configuration."""

from collections import Counter
from pathlib import Path

from mightier.books import ENTITY_FOLDERS, SLUG, BookError, Project

SHELLS = ("fish", "bash", "zsh")


def no_values(**kwargs):
    return []


def selected_project(parsed_args):
    folder = getattr(parsed_args, "project", None)
    return Project(folder) if folder is not None else Project.discover()


def chapter_names(prefix, parsed_args, **kwargs):
    try:
        project = selected_project(parsed_args)
        config, _ = project.load_book()
    except BookError, OSError, UnicodeError:
        return {}
    paths = config["chapters"]
    counts = Counter(Path(path).stem for path in paths)
    # Prefer short names. Where a name is ambiguous, suggest the exact path
    # that rename accepts. Explicit path prefixes also receive path suggestions.
    path_prefix = "/" in prefix or (
        prefix and not any(Path(path).stem.startswith(prefix) for path in paths)
    )
    return {
        path if path_prefix or counts[Path(path).stem] > 1 else Path(path).stem: path
        for path in paths
    }


def record_names(kind):
    def complete(prefix, parsed_args, **kwargs):
        try:
            project = selected_project(parsed_args)
            if kind == "reference":
                return [entry["id"] for entry in project.references()]
            directory = project.metadata_dir / ENTITY_FOLDERS[kind]
            return [
                path.stem
                for path in sorted(directory.glob("*.md"))
                if SLUG.fullmatch(path.stem)
            ]
        except BookError, OSError, UnicodeError:
            return []

    return complete


def script(shell):
    import argcomplete

    return argcomplete.shellcode(["mightier"], shell=shell)
