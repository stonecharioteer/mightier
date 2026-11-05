"""Rename shared metadata IDs and their exact Markdown tag references."""

import fcntl
import json
import os
import re
import subprocess
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

import yaml
from yaml.nodes import ScalarNode

from mightier.books import (
    CONFIG,
    ENTITY_FOLDERS,
    BookError,
    Metadata,
    Project,
    read_entity_document,
    read_yaml,
    require_slug,
)
from mightier.chapters import project_lock
from mightier.file_ops import atomic_write, create_file
from mightier.logging_config import logger
from mightier.output import console, success

SKIP_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".tox",
}


@dataclass(frozen=True)
class Change:
    path: Path
    before: bytes
    after: bytes


@dataclass(frozen=True)
class RenamePlan:
    scope: Path
    projects: tuple[Project, ...]
    configs: dict[Path, bytes]
    roots: tuple[Path, ...]
    metadata: Path
    snapshots: dict[Path, bytes]
    source: Path
    target: Path
    original: bytes
    renamed: bytes
    changes: tuple[Change, ...]
    references: int


def walk_files(root, nested_projects=()):
    def fail(error):
        raise error

    nested = set(nested_projects)
    for directory, directories, names in root.walk(on_error=fail):
        directories[:] = [
            name
            for name in directories
            if name not in SKIP_DIRECTORIES
            and directory / name not in nested
            and not (name == "build" and CONFIG in names)
        ]
        for name in names:
            if not name.startswith(".mightier-"):
                yield directory / name


def choose_scope(project, explicit=None):
    metadata = project.metadata_dir
    if explicit is not None:
        scope = Path(explicit).expanduser().resolve()
    else:
        scope = None
        try:
            result = subprocess.run(
                ["git", "-C", str(project.root), "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode == 0:
                candidate = Path(result.stdout.strip()).resolve()
                if metadata.is_relative_to(candidate):
                    scope = candidate
        except FileNotFoundError, subprocess.TimeoutExpired:
            pass
        if scope is None:
            current = Path.cwd().resolve()
            if (
                project.root.is_relative_to(current)
                and metadata.is_relative_to(current)
                and current not in (Path.home().resolve(), Path(current.anchor))
            ):
                scope = current
            elif metadata.is_relative_to(project.root):
                scope = project.root
            else:
                raise BookError(
                    "Shared metadata is outside this book. Run from the series root or pass --scope PATH containing all affected books and metadata."
                )
    if (
        not scope.is_dir()
        or not project.root.is_relative_to(scope)
        or not metadata.is_relative_to(scope)
    ):
        raise BookError(
            "The rename scope must contain the selected book and its shared metadata directory."
        )
    if scope == Path(scope.anchor):
        raise BookError(
            "The filesystem root cannot be a rename scope. Choose the series directory."
        )
    return scope


def configuration_files(scope, projects=()):
    paths = {path for path in walk_files(scope) if path.name == CONFIG}
    paths.update(project.root / CONFIG for project in projects)
    return paths


def discover_projects(project, scope):
    paths = configuration_files(scope, (project,))
    matching, configs, roots = [], {}, []
    for path in sorted(paths):
        if path.is_symlink() or not path.resolve().is_relative_to(scope):
            raise BookError(
                f"{path}: refusing a configuration link outside the rename scope."
            )
        before = path.read_bytes()
        candidate = Project(path.parent)
        try:
            shared = candidate.metadata_dir
        except (BookError, OSError, UnicodeError) as error:
            raise BookError(
                f"Cannot determine rename consumers from {path}. Fix its configuration first. {error}"
            ) from error
        if path.read_bytes() != before:
            raise BookError(
                f"{path}: configuration changed during discovery. Try again."
            )
        roots.append(candidate.root)
        configs[path] = before
        if shared == project.metadata_dir:
            candidate.load_book()
            matching.append(candidate)
    return tuple(matching), configs, tuple(roots)


def header_nodes(text, path):
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise BookError(f"{path}: missing metadata front matter.")
    end = next(
        (
            index
            for index in range(1, len(lines))
            if lines[index].rstrip("\r\n") == "---"
        ),
        None,
    )
    if end is None:
        raise BookError(f"{path}: missing closing front matter marker.")
    start = len(lines[0])
    stop = sum(map(len, lines[:end]))
    header = text[start:stop]
    data = read_yaml(header, path)
    try:
        node = yaml.compose(header, Loader=yaml.SafeLoader)
    except yaml.YAMLError as error:
        raise BookError(f"{path}: {error}") from error
    nodes = {key.value: (key, value) for key, value in node.value}
    return start, data, nodes


def rewrite(text, pattern, replacement, path, metadata=False, new_id=None):
    protected = []
    edits = []
    before_data = None
    if metadata:
        before_data, _ = read_entity_document(metadata, path, content=text)
        offset, _, nodes = header_nodes(text, path)
        if "names" in nodes:
            names = nodes["names"][1]
            protected.append(
                (offset + names.start_mark.index, offset + names.end_mark.index)
            )
        if new_id is not None:
            key, node = nodes["id"]
            if (
                not isinstance(node, ScalarNode)
                or node.style not in (None, "'", '"')
                or node.start_mark.index < key.end_mark.index
            ):
                raise BookError(
                    f"{path}: renaming an aliased or block-style ID is not supported. Use a plain or quoted ID first."
                )
            original = text[
                offset + node.start_mark.index : offset + node.end_mark.index
            ]
            if original.startswith(("&", "*")):
                raise BookError(
                    f"{path}: remove the ID anchor or alias before renaming."
                )
            scalar = (
                f"'{new_id}'"
                if node.style == "'"
                else json.dumps(new_id)
                if node.style == '"' or not isinstance(yaml.safe_load(new_id), str)
                else new_id
            )
            edits.append(
                (offset + node.start_mark.index, offset + node.end_mark.index, scalar)
            )
    count = 0
    for match in pattern.finditer(text):
        if any(match.start() < end and match.end() > start for start, end in protected):
            continue
        edits.append(
            (match.start(), match.end(), replacement + (match.group(1) or "") + "}}")
        )
        count += 1
    for start, end, value in sorted(edits, reverse=True):
        text = text[:start] + value + text[end:]
    if metadata:
        destination = path.with_name(f"{new_id}.md") if new_id is not None else path
        after_data, _ = read_entity_document(metadata, destination, content=text)
        if after_data.get("names") != before_data.get("names"):
            raise BookError(
                f"{path}: the rename would change display names through a YAML alias. Remove that alias first."
            )
        if after_data.get("id") != (
            new_id if new_id is not None else before_data.get("id")
        ):
            raise BookError(f"{path}: could not preserve the metadata ID safely.")
    return text, count


def source_files(projects, metadata, roots):
    foreign_metadata = {Project(root).metadata_dir for root in roots} - {
        metadata.metadata_dir
    }
    boundaries = set(roots) | foreign_metadata
    paths = {
        path
        for path in walk_files(metadata.metadata_dir, boundaries)
        if path.suffix == ".md"
    }
    for book in projects:
        paths.update(
            path
            for path in walk_files(book.root, boundaries - {book.root})
            if path.suffix == ".md"
        )
        paths.update(book.load_book()[1])
    paths.update(path for _, path in metadata.entity_files())
    return paths


def plan_rename(project, kind, old, new, scope):
    projects, configs, roots = discover_projects(project, scope)
    metadata = Metadata(project.metadata_dir)
    matching_roots = {book.root for book in projects}
    for root in set(roots) - matching_roots:
        foreign = Project(root).metadata_dir
        if (
            root == metadata.metadata_dir
            or foreign in matching_roots
            or foreign.is_relative_to(metadata.metadata_dir)
            or metadata.metadata_dir.is_relative_to(foreign)
        ):
            raise BookError(
                f"{root}: rename needs separate, non-overlapping metadata directories for books that do not share metadata."
            )
    source = metadata.metadata_dir / ENTITY_FOLDERS[kind] / f"{old}.md"
    target = source.with_name(f"{new}.md")
    if target.exists() or target.is_symlink():
        raise BookError(f"{target}: already exists. Nothing was overwritten.")
    records = {path: record_kind for record_kind, path in metadata.entity_files()}
    paths = source_files(projects, metadata, roots)
    for path in paths:
        if (
            path.is_symlink()
            or path.resolve() != path
            or not path.resolve().is_relative_to(scope)
        ):
            raise BookError(
                f"{path}: refusing linked source files or files outside the rename scope."
            )
    entities = metadata.load_entities()
    if (kind, old) not in entities:
        raise BookError(f"Unknown {kind} {old!r}.")
    pattern = re.compile(
        r"(?<!\{)\{\{"
        + re.escape(kind + ":" + old)
        + r"(\.[a-z][a-z0-9_-]*)?\}\}(?!\})"
    )
    changes, references = [], 0
    snapshots = {}
    original = renamed = None
    for path in sorted(paths):
        before = path.read_bytes()
        snapshots[path] = before
        text = before.decode("utf-8")
        after_text, count = rewrite(
            text,
            pattern,
            "{{" + kind + ":" + new,
            path,
            records.get(path),
            new if path == source else None,
        )
        after = after_text.encode("utf-8")
        if before == after:
            continue
        if (
            path.is_symlink()
            or path.resolve() != path
            or not path.resolve().is_relative_to(scope)
        ):
            raise BookError(
                f"{path}: refusing to change a linked file or a file outside the rename scope."
            )
        references += count
        if path == source:
            original, renamed = before, after
        else:
            changes.append(Change(path, before, after))
    if original is None:
        raise BookError("Could not prepare the metadata record rename.")
    return RenamePlan(
        scope,
        projects,
        configs,
        roots,
        metadata.metadata_dir,
        snapshots,
        source,
        target,
        original,
        renamed,
        tuple(changes),
        references,
    )


@contextmanager
def metadata_lock(directory):
    descriptor = os.open(
        directory / ".mightier-metadata.lock", os.O_CREAT | os.O_RDWR, 0o600
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def verify_plan(plan, applied=False):
    if configuration_files(plan.scope, plan.projects) != set(plan.configs):
        raise BookError(
            "The set of book configurations changed during the rename. Try again."
        )
    for path, expected in plan.configs.items():
        if path.is_symlink() or path.read_bytes() != expected:
            raise BookError(f"{path}: configuration changed. Try the rename again.")
    if plan.source.is_symlink() or plan.source.read_bytes() != plan.original:
        raise BookError("The source record changed. Its edit was kept.")
    current_files = source_files(plan.projects, Metadata(plan.metadata), plan.roots)
    if applied:
        current_files.discard(plan.target)
    if current_files != set(plan.snapshots):
        raise BookError(
            "The set of Markdown files changed during the rename. Try again."
        )
    expected_files = dict(plan.snapshots)
    if applied:
        expected_files.update((change.path, change.after) for change in plan.changes)
    for path, expected in expected_files.items():
        if path.is_symlink() or path.resolve() != path or path.read_bytes() != expected:
            raise BookError(f"{path}: changed during the rename. Its edit was kept.")


def apply_plan(plan):
    verify_plan(plan)
    create_file(plan.target, plan.renamed, plan.source.stat().st_mode & 0o777)
    attempted = []
    try:
        for change in plan.changes:
            attempted.append(change)
            atomic_write(change.path, change.before, change.after)
        verify_plan(plan, applied=True)
        if plan.target.is_symlink() or plan.target.read_bytes() != plan.renamed:
            raise BookError("The renamed record changed. Its edit was kept.")
        plan.source.unlink()
    except BaseException as error:
        if not plan.source.exists():
            raise
        recovered = True
        attempted_paths = {change.path for change in attempted}
        try:
            if configuration_files(plan.scope, plan.projects) != set(plan.configs):
                recovered = False
            for path, expected in plan.snapshots.items():
                if path not in attempted_paths and path.read_bytes() != expected:
                    recovered = False
            if source_files(plan.projects, Metadata(plan.metadata), plan.roots) - {
                plan.target
            } != set(plan.snapshots):
                recovered = False
            for path, expected in plan.configs.items():
                if path.read_bytes() != expected:
                    recovered = False
        except BookError, OSError:
            recovered = False
        for change in reversed(attempted):
            try:
                current = change.path.read_bytes()
                if current == change.after:
                    atomic_write(change.path, change.after, change.before)
                elif current != change.before:
                    recovered = False
            except BookError, OSError:
                logger.exception("Could not roll back %s", change.path)
                recovered = False
        try:
            if (
                recovered
                and not plan.target.is_symlink()
                and plan.target.read_bytes() == plan.renamed
            ):
                plan.target.unlink()
            else:
                recovered = False
        except OSError:
            recovered = False
        if not recovered:
            raise BookError(
                f"Rename stopped. Concurrent edits were kept, along with both metadata records. Review {plan.source} and {plan.target}. {error}"
            ) from error
        raise


def rename_entity(project, kind, old, new, scope=None, dry_run=False):
    require_slug(old)
    require_slug(new)
    if kind not in ENTITY_FOLDERS:
        raise BookError(f"Unknown metadata type {kind!r}.")
    if old == new:
        raise BookError("The new ID must differ from the old ID.")
    boundary = choose_scope(project, scope)
    if dry_run:
        plan = plan_rename(project, kind, old, new, boundary)
        console.print(f"Rename scope: {boundary}", markup=False)
        console.print(f"Rename {plan.source} to {plan.target}", markup=False)
        for change in plan.changes:
            console.print(f"Update {change.path}", markup=False)
        success(
            f"Dry run: {plan.references} tag reference(s), {len(plan.projects)} book(s). No manuscript files changed."
        )
        return plan.target
    directory = project.metadata_dir
    with metadata_lock(directory), ExitStack() as locks:
        projects, _, _ = discover_projects(project, boundary)
        for book in sorted(projects, key=lambda item: str(item.root)):
            locks.enter_context(project_lock(book))
        plan = plan_rename(project, kind, old, new, boundary)
        if plan.metadata != directory:
            raise BookError(
                "The metadata directory changed during planning. Try again."
            )
        apply_plan(plan)
    success(
        f"Renamed {kind} {old} to {new}. Updated {plan.references} tag reference(s) across {len(plan.changes) + 1} file(s) and {len(plan.projects)} book(s) under {boundary}."
    )
    return plan.target
