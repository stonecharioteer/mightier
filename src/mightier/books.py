"""Book projects, shared metadata, and Pandoc builds."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from importlib.resources import files
from pathlib import Path

import yaml

from mightier.logging_config import logger
from mightier.output import success, warning

CONFIG = "mightier.toml"
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
FORM = re.compile(r"[a-z][a-z0-9_-]*\Z")
TAG = re.compile(
    r"\{\{([a-z]+):([a-z0-9]+(?:-[a-z0-9]+)*)(?:\.([a-z][a-z0-9_-]*))?\}\}"
)
ENTITY_FOLDERS = {"character": "characters", "place": "places"}
DEFAULT_FORMS = {"character": "short", "place": "name"}


class BookError(Exception):
    """An input or tool error that can be reported without a traceback."""


class UniqueLoader(yaml.SafeLoader):
    """Reject duplicate YAML keys rather than silently replacing a name."""


def unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        try:
            if key in result:
                raise BookError(f"Duplicate YAML key: {key}")
            result[key] = loader.construct_object(value_node)
        except TypeError as error:
            raise BookError("YAML keys must be simple values.") from error
    return result


UniqueLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping
)


def read_yaml(text, source):
    try:
        value = yaml.load(text, Loader=UniqueLoader)
    except (yaml.YAMLError, BookError) as error:
        raise BookError(f"{source}: {error}") from error
    if not isinstance(value, dict):
        raise BookError(f"{source}: expected a YAML mapping.")
    return value


def require_text(value, label):
    if (
        not isinstance(value, str)
        or not value.strip()
        or "\n" in value
        or "\r" in value
    ):
        raise BookError(f"{label}: expected non-empty, single-line text.")
    return value


def require_slug(value):
    if not isinstance(value, str) or not SLUG.fullmatch(value):
        raise BookError("Use a lowercase ID with letters, numbers, and single hyphens.")
    return value


def read_entity_document(kind, path):
    require_slug(path.stem)
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise BookError(f"{path}: start the file with YAML front matter.")
    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise BookError(f"{path}: missing closing --- for front matter.") from error
    data = read_yaml("\n".join(lines[1:end]), path)
    if data.get("id") != path.stem or data.get("type") != kind:
        raise BookError(
            f"{path}: id and type must match the filename and metadata folder."
        )
    names = data.get("names")
    if not isinstance(names, dict) or not names:
        raise BookError(f"{path}: names must be a non-empty mapping.")
    for form, name in names.items():
        if not isinstance(form, str) or not FORM.fullmatch(form):
            raise BookError(f"{path}: invalid name form {form!r}.")
        require_text(name, f"{path}: names.{form}")
    if not isinstance(data.get("public_description", ""), str):
        raise BookError(f"{path}: public_description must be text.")
    return data, "\n".join(lines[end + 1 :])


def read_entity(kind, path):
    data, _ = read_entity_document(kind, path)
    return data


def name_inlines(name):
    return [
        {"t": "Space"} if part.isspace() else {"t": "Str", "c": part}
        for part in re.split(r"(\s+)", name)
        if part
    ]


def expand_text(text, entities, source, occurrences):
    result = []
    start = 0
    for match in TAG.finditer(text):
        prefix = text[start : match.start()]
        if "{{" in prefix or "}}" in prefix:
            raise BookError(f"{source}: malformed entity tag in {text!r}.")
        if prefix:
            result.append({"t": "Str", "c": prefix})
        kind, entity_id, form = match.groups()
        key = (kind, entity_id)
        if key not in entities:
            raise BookError(f"{source}: unknown entity {kind}:{entity_id}.")
        form = form or DEFAULT_FORMS.get(kind, "short")
        if form not in entities[key]:
            raise BookError(f"{source}: unknown name form {kind}:{entity_id}.{form}.")
        occurrences[key] = occurrences.get(key, 0) + 1
        attributes = [
            f"ref-{kind}-{entity_id}-{occurrences[key]}",
            ["entity-reference"],
            [["data-entity", f"{kind}:{entity_id}"], ["data-form", form]],
        ]
        result.append(
            {"t": "Span", "c": [attributes, name_inlines(entities[key][form])]}
        )
        start = match.end()
    suffix = text[start:]
    if "{{" in suffix or "}}" in suffix:
        raise BookError(f"{source}: malformed entity tag in {text!r}.")
    if suffix:
        result.append({"t": "Str", "c": suffix})
    return result


def expand_nodes(value, entities, source, occurrences):
    if isinstance(value, list):
        expanded = []
        for item in value:
            if isinstance(item, dict) and item.get("t") == "Str":
                expanded.extend(expand_text(item["c"], entities, source, occurrences))
            else:
                expanded.append(expand_nodes(item, entities, source, occurrences))
        return expanded
    if isinstance(value, dict):
        # Code, raw markup, math, and URLs have no prose Str nodes.
        return {
            key: expand_nodes(item, entities, source, occurrences)
            for key, item in value.items()
        }
    return value


class Metadata:
    """A shared metadata directory, independent of any particular book."""

    def __init__(self, directory):
        self.metadata_dir = Path(directory).expanduser().resolve()

    def entity_files(self):
        shared = self.metadata_dir
        logger.debug("Reading shared metadata: %s", shared)
        if not shared.is_dir():
            raise BookError(f"{shared}: metadata directory is missing.")
        for kind, folder in ENTITY_FOLDERS.items():
            for path in sorted((shared / folder).glob("*.md")):
                yield kind, path

    def load_entities(self):
        entities = {}
        for kind, path in self.entity_files():
            data = read_entity(kind, path)
            key = (kind, data["id"])
            if key in entities:
                raise BookError(f"{path}: duplicate entity {kind}:{data['id']}.")
            entities[key] = data["names"]
        return entities


class Project:
    """A single book and a reference to its possibly shared metadata directory."""

    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        logger.debug("Selected book project: %s", self.root)

    @classmethod
    def discover(cls, start=None):
        start = Path(start or Path.cwd()).expanduser().resolve()
        if start.is_file():
            start = start.parent
        for folder in (start, *start.parents):
            if (folder / CONFIG).is_file():
                return cls(folder)
        raise BookError(
            "No mightier.toml found. Run mightier init or use --project PATH."
        )

    def initialize(self, metadata_dir=None):
        path = self.root / CONFIG
        requested = (
            Path(metadata_dir).expanduser().resolve()
            if metadata_dir is not None
            else None
        )
        if path.exists():
            current = self.metadata_dir
            if requested is not None and requested != current:
                raise BookError(
                    f"{path}: already exists. Edit metadata-dir there to change it."
                )
        else:
            self.root.mkdir(parents=True, exist_ok=True)
            shared = requested or self.root / "metadata"
            relative = os.path.relpath(shared, self.root)
            title = self.root.name.replace("-", " ").title()
            config = (
                "schema-version = 1\n"
                f"title = {json.dumps(title, ensure_ascii=False)}\n"
                'author = "Author name"\n'
                'lang = "en"\n'
                f"metadata-dir = {json.dumps(relative, ensure_ascii=False)}\n"
                f"bibliography = {json.dumps(str(Path(relative) / 'references.bib'), ensure_ascii=False)}\n"
                'chapters = ["chapters/01-arrival.md"]\n'
            )
            with path.open("x", encoding="utf-8") as stream:
                stream.write(config)
            chapters = self.root / "chapters"
            chapters.mkdir(exist_ok=True)
            first = chapters / "01-arrival.md"
            if not first.exists():
                first.write_text(
                    "# Arrival\n\nReplace this paragraph with your opening scene.\n",
                    encoding="utf-8",
                )
            ignore = self.root / ".gitignore"
            if not ignore.exists():
                ignore.write_text(
                    "/build/\nmightier.log*\n.mightier-log.lock\n.mightier-project.lock\n",
                    encoding="utf-8",
                )
        for folder in ENTITY_FOLDERS.values():
            directory = self.metadata_dir / folder
            directory.mkdir(parents=True, exist_ok=True)
            (directory / ".gitkeep").touch(exist_ok=True)
        bibliography = self.bibliography
        bibliography.parent.mkdir(parents=True, exist_ok=True)
        if not bibliography.exists():
            with bibliography.open("x", encoding="utf-8") as stream:
                stream.write("% Shared book references. Add BibTeX entries here.\n")
        success(f"Initialized {path}. Existing content was kept.")

    def config(self):
        path = self.root / CONFIG
        logger.debug("Reading book configuration: %s", path)
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as error:
            raise BookError(f"{path}: {error}") from error
        if type(data.get("schema-version")) is not int or data["schema-version"] != 1:
            raise BookError(f"{path}: schema-version must be 1.")
        for field in ("title", "lang", "metadata-dir", "bibliography"):
            require_text(data.get(field), f"{path}: {field}")
        authors = data.get("author")
        authors = authors if isinstance(authors, list) else [authors]
        if not authors:
            raise BookError(f"{path}: author must not be empty.")
        for author in authors:
            require_text(author, f"{path}: author")
        if not isinstance(data.get("chapters"), list) or not data["chapters"]:
            raise BookError(f"{path}: chapters must be a non-empty ordered list.")
        for chapter in data["chapters"]:
            require_text(chapter, f"{path}: chapter path")
        return data

    @property
    def metadata_dir(self):
        target = Path(self.config()["metadata-dir"]).expanduser()
        resolved = (self.root / target).resolve()
        if resolved.is_relative_to(self.root / "build"):
            raise BookError(
                "metadata-dir must not be inside the generated build directory."
            )
        return resolved

    @property
    def bibliography(self):
        target = Path(self.config()["bibliography"]).expanduser()
        resolved = (self.root / target).resolve()
        if resolved.is_relative_to(self.root / "build"):
            raise BookError(
                "bibliography must not be inside the generated build directory."
            )
        return resolved

    def references(self):
        path = self.bibliography
        if not path.is_file():
            raise BookError(f"{path}: bibliography is missing. Check mightier.toml.")
        entries = json.loads(self.pandoc(["--from=bibtex", "--to=csljson", str(path)]))
        keys = [entry["id"] for entry in entries]
        if len(keys) != len(set(keys)):
            raise BookError(f"{path}: duplicate citation keys.")
        return entries

    def new_reference(self, key):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_.+\-]*", key):
            raise BookError(
                "Use a citation key with letters, numbers, colons, dots, underscores, or hyphens."
            )
        if any(entry["id"] == key for entry in self.references()):
            raise BookError(
                f"Reference {key!r} already exists. Nothing was overwritten."
            )
        with self.bibliography.open("a", encoding="utf-8") as stream:
            stream.write(
                f"\n@misc{{{key},\n"
                "  title = {Replace with the reference title},\n"
                "  author = {Author name},\n"
                "}\n"
            )
        success(
            f"Added {key} to {self.bibliography}. Edit its details before publication."
        )

    def new_entity(self, kind, entity_id):
        require_slug(entity_id)
        name = entity_id.replace("-", " ").title()
        names = {"name": name}
        if kind == "character":
            names = {
                "full": name,
                "short": name.split()[0],
                "surname": name.split()[-1],
            }
        metadata = {
            "id": entity_id,
            "type": kind,
            "names": names,
            "public_description": "",
        }
        path = self.metadata_dir / ENTITY_FOLDERS[kind] / f"{entity_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8") as stream:
                stream.write(
                    "---\n" + yaml.safe_dump(metadata, sort_keys=False) + "---\n"
                )
                stream.write("\n# Background\n\n# Relationships\n\n# Story notes\n")
        except FileExistsError as error:
            raise BookError(
                f"{path}: already exists. Nothing was overwritten."
            ) from error
        success(f"Created {path}. Names are placeholders. Edit them before use.")

    def entity_files(self):
        return Metadata(self.metadata_dir).entity_files()

    def load_entities(self):
        return Metadata(self.metadata_dir).load_entities()

    def load_book(self):
        data = self.config()
        paths = []
        for chapter in data["chapters"]:
            path = self.root / chapter
            if Path(chapter).is_absolute() or not path.resolve().is_relative_to(
                self.root
            ):
                raise BookError(
                    f"{CONFIG}: chapter paths must stay inside the book folder."
                )
            if path.resolve().is_relative_to(self.root / "build"):
                raise BookError(
                    f"{CONFIG}: chapters must not be inside the generated build directory."
                )
            if path.suffix != ".md" or not path.is_file():
                raise BookError(f"{CONFIG}: missing Markdown chapter {chapter!r}.")
            if path.resolve() in paths:
                raise BookError(f"{CONFIG}: duplicate chapter {chapter!r}.")
            paths.append(path.resolve())
        return data, paths

    def pandoc(self, arguments, text=None):
        logger.debug(
            "Running Pandoc: arguments=%r input_characters=%d",
            arguments,
            len(text or ""),
        )
        try:
            result = subprocess.run(
                ["pandoc", "--fail-if-warnings", *arguments],
                input=text,
                text=True,
                capture_output=True,
                cwd=self.root,
                check=False,
                timeout=120,
            )
        except FileNotFoundError as error:
            raise BookError("Pandoc is missing. Run mightier setup.") from error
        except subprocess.TimeoutExpired as error:
            raise BookError("Pandoc timed out after 120 seconds.") from error
        logger.debug(
            "Pandoc finished: returncode=%d stderr=%s",
            result.returncode,
            result.stderr.strip(),
        )
        if result.returncode:
            raise BookError(f"Pandoc failed: {result.stderr.strip()}")
        if result.stderr:
            warning(result.stderr.strip())
        return result.stdout

    def prepare_book(self):
        data, chapters = self.load_book()
        entities = self.load_entities()
        document = None
        blocks = []
        occurrences = {}
        for chapter in chapters:
            part = json.loads(
                self.pandoc(["--from=markdown", "--to=json", str(chapter)])
            )
            blocks.extend(expand_nodes(part["blocks"], entities, chapter, occurrences))
            if document is None:
                document = part
        document["blocks"] = blocks
        authors = (
            data["author"] if isinstance(data["author"], list) else [data["author"]]
        )
        document["meta"] = {
            "title": {"t": "MetaString", "c": data["title"]},
            "author": {
                "t": "MetaList",
                "c": [{"t": "MetaString", "c": name} for name in authors],
            },
            "lang": {"t": "MetaString", "c": data["lang"]},
        }
        return self.resolve_citations(document), chapters

    def resolve_citations(self, document):
        self.references()
        publication_metadata = document["meta"]
        document["meta"]["reference-section-title"] = {
            "t": "MetaString",
            "c": "References",
        }
        document = json.loads(
            self.pandoc(
                [
                    "--from=json",
                    "--to=json",
                    "--citeproc",
                    f"--bibliography={self.bibliography}",
                ],
                json.dumps(document),
            )
        )
        document["meta"] = publication_metadata
        return document

    def prepare_notes(self, kind, entity_id, entities=None):
        require_slug(entity_id)
        if kind not in ENTITY_FOLDERS:
            raise BookError(f"Unknown metadata type {kind!r}.")
        entities = self.load_entities() if entities is None else entities
        if (kind, entity_id) not in entities:
            raise BookError(f"Unknown entity {kind}:{entity_id}.")
        path = self.metadata_dir / ENTITY_FOLDERS[kind] / f"{entity_id}.md"
        data, notes = read_entity_document(kind, path)
        description = data.get("public_description", "")
        body = description + "\n\n" + notes if description else notes
        document = json.loads(self.pandoc(["--from=markdown", "--to=json"], body))
        document["blocks"] = expand_nodes(document["blocks"], entities, path, {})
        names = data["names"]
        title = (
            names.get("full") or names.get("name") or names.get("short") or entity_id
        )
        document["meta"] = {
            "title": {"t": "MetaString", "c": title},
            "lang": {"t": "MetaString", "c": self.config()["lang"]},
        }
        return self.resolve_citations(document), path

    def render_notes(self, kind, entity_id):
        """Render author notes only for the local server. Never write an export."""
        document, path = self.prepare_notes(kind, entity_id)
        return self.pandoc(
            [
                "--from=json",
                "--to=html5",
                "--standalone",
                "--toc",
                "--embed-resources",
                f"--css={files('mightier').joinpath('assets/reader.css')}",
                f"--resource-path={os.pathsep.join(map(str, (path.parent, self.metadata_dir, self.root)))}",
            ],
            json.dumps(document),
        )

    def check(self):
        _, chapters = self.prepare_book()
        entities = self.load_entities()
        for kind, entity_id in entities:
            self.prepare_notes(kind, entity_id, entities)
        success(
            f"Checked {len(chapters)} chapter(s) and {len(entities)} metadata note(s) in {self.metadata_dir}."
        )

    def build(self, output_format="epub", announce=True):
        if output_format not in ("epub", "html"):
            raise BookError(f"Unsupported output format: {output_format}")
        document, chapters = self.prepare_book()
        folder = self.root / "build"
        if folder.is_symlink():
            raise BookError("The build directory must not be a symbolic link.")
        folder.mkdir(exist_ok=True)
        output = folder / f"book.{output_format}"
        resource_paths = list(
            dict.fromkeys([str(path.parent) for path in chapters] + [str(self.root)])
        )
        with tempfile.TemporaryDirectory(prefix=".build-", dir=folder) as temporary:
            staged = Path(temporary) / output.name
            arguments = [
                "--from=json",
                f"--to={'epub3' if output_format == 'epub' else 'html5'}",
                "--standalone",
                "--toc",
                f"--output={staged}",
                f"--resource-path={os.pathsep.join(resource_paths)}",
            ]
            if output_format == "html":
                arguments.extend(
                    [
                        "--embed-resources",
                        f"--css={files('mightier').joinpath('assets/reader.css')}",
                    ]
                )
            self.pandoc(arguments, json.dumps(document))
            if output_format == "epub":
                if shutil.which("epubcheck"):
                    result = subprocess.run(
                        ["epubcheck", str(staged)], cwd=self.root, check=False
                    )
                    if result.returncode:
                        raise BookError(
                            "EPUBCheck failed. The previous output was kept."
                        )
                else:
                    warning(
                        "EPUBCheck is missing. Run mightier setup to enable EPUB validation."
                    )
            staged.replace(output)
        if announce:
            success(f"Created {output}")
        return output

    def clean(self):
        folder = self.root / "build"
        if folder.is_symlink():
            raise BookError(
                "Refusing to clean a build directory that is a symbolic link."
            )
        if folder.exists():
            shutil.rmtree(folder)
        success("Removed generated build files. Manuscripts and metadata were kept.")
