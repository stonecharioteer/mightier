"""Test single-book projects, shared metadata, and real Pandoc output."""

import io
import json
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from mightier import books
from mightier.books import BookError, Project


class ProjectCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "books/book-01"
        self.shared = self.base / "metadata"
        self.project = Project(self.root)
        for redirect in (
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            redirect.__enter__()
            self.addCleanup(redirect.__exit__, None, None, None)

    def initialize(self):
        self.project.initialize(self.shared)
        self.chapter = self.root / "chapters/01-arrival.md"

    def sample(self):
        self.initialize()
        self.project.new_entity("character", "john-doe")
        self.project.new_entity("place", "greyhaven")
        self.chapter.write_text(
            "# Arrival\n\n{{character:john-doe.full}} entered {{place:greyhaven.name}}.\n\n"
            '"{{character:john-doe.short}}, wait!"\n',
            encoding="utf-8",
        )

    def configure(self, **changes):
        data = self.project.config()
        data.update(changes)
        (self.root / books.CONFIG).write_text(
            "\n".join(
                f"{key} = {json.dumps(value, ensure_ascii=False)}"
                for key, value in data.items()
                if value is not None
            )
            + "\n",
            encoding="utf-8",
        )


class BookTests(ProjectCase):
    def test_init_stores_shared_paths_in_toml(self):
        self.initialize()
        config = self.project.config()
        self.assertEqual(config["metadata-dir"], "../../metadata")
        self.assertEqual(config["bibliography"], "../../metadata/references.bib")
        self.assertEqual(config["chapters"], ["chapters/01-arrival.md"])
        self.assertTrue(self.chapter.is_file())
        self.assertTrue((self.shared / "references.bib").is_file())
        self.assertFalse((self.root / "pyproject.toml").exists())

    def test_default_metadata_directory(self):
        self.project.initialize()
        self.assertEqual(self.project.metadata_dir, self.root / "metadata")
        self.assertEqual(self.project.config()["metadata-dir"], "metadata")

    def test_init_does_not_overwrite_existing_content(self):
        self.initialize()
        self.chapter.write_text("Keep this opening.")
        path = self.root / books.CONFIG
        original = path.read_bytes()
        reference = self.shared / "references.bib"
        reference.write_text("% Keep this comment.\n")
        self.project.initialize(self.shared)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.chapter.read_text(), "Keep this opening.")
        self.assertEqual(reference.read_text(), "% Keep this comment.\n")
        with self.assertRaisesRegex(BookError, "already exists"):
            self.project.initialize(self.base / "different")

    def test_discover_from_nested_folder_or_file(self):
        self.initialize()
        self.assertEqual(Project.discover(self.chapter.parent).root, self.root)
        self.assertEqual(Project.discover(self.chapter).root, self.root)
        with self.assertRaisesRegex(BookError, "No mightier.toml"):
            Project.discover(self.base)

    def test_two_books_share_metadata_without_a_workspace(self):
        self.initialize()
        other = Project(self.base / "another-book")
        other.initialize(self.shared)
        self.project.new_entity("character", "john-doe")
        self.assertEqual(other.load_entities(), self.project.load_entities())
        other.new_entity("place", "greyhaven")
        self.assertIn(("place", "greyhaven"), self.project.load_entities())
        self.assertFalse((self.base / books.CONFIG).exists())

    def test_entity_templates_and_no_overwrite(self):
        self.initialize()
        for kind, folder in books.ENTITY_FOLDERS.items():
            self.project.new_entity(kind, "john-doe")
            path = self.shared / folder / "john-doe.md"
            before = path.read_bytes()
            self.assertIn("# Story notes", path.read_text())
            with self.assertRaisesRegex(BookError, "already exists"):
                self.project.new_entity(kind, "john-doe")
            self.assertEqual(path.read_bytes(), before)
        entities = self.project.load_entities()
        self.assertEqual(entities[("character", "john-doe")]["short"], "John")
        self.assertEqual(entities[("place", "john-doe")]["name"], "John Doe")

    def test_rejects_unsafe_ids(self):
        self.initialize()
        for value in ("../outside", "Book 1", "", "a/b", "a--b", "/absolute", "-a"):
            with self.subTest(value=value), self.assertRaises(BookError):
                self.project.new_entity("character", value)

    def test_entity_validation(self):
        self.sample()
        path = self.shared / "characters/john-doe.md"
        cases = (
            ("# No metadata", "front matter"),
            ("---\nid: john-doe\n", "closing"),
            (
                "---\nid: wrong\ntype: character\nnames: {short: John}\n---",
                "must match",
            ),
            ("---\nid: john-doe\ntype: place\nnames: {short: John}\n---", "must match"),
            ("---\nid: john-doe\ntype: character\nnames: {}\n---", "non-empty mapping"),
            (
                "---\nid: john-doe\ntype: character\nnames: {short: 12}\n---",
                "single-line",
            ),
            (
                "---\nid: john-doe\ntype: character\nnames: {short: ''}\n---",
                "single-line",
            ),
            (
                "---\nid: john-doe\ntype: character\nnames: {bad.form: John}\n---",
                "invalid name",
            ),
            ("---\nid: john-doe\nid: john-doe\n---", "Duplicate YAML key"),
            (
                "---\nid: john-doe\ntype: character\nnames: {short: John, short: Jon}\n---",
                "Duplicate YAML key",
            ),
        )
        for content, message in cases:
            with self.subTest(message=message):
                path.write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(BookError, message):
                    self.project.load_entities()

    def test_invalid_toml_and_configuration(self):
        self.initialize()
        original = (self.root / books.CONFIG).read_text()
        cases = {
            "schema-version": [None, 2, True],
            "title": [None, ""],
            "author": [None, [], 12],
            "lang": [""],
            "metadata-dir": [""],
            "bibliography": [""],
            "chapters": [[], "chapter.md", [12]],
        }
        for key, values in cases.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    (self.root / books.CONFIG).write_text(original)
                    self.configure(**{key: value})
                    with self.assertRaises(BookError):
                        self.project.config()
        (self.root / books.CONFIG).write_text("invalid = [\n")
        with self.assertRaisesRegex(BookError, "mightier.toml"):
            self.project.config()

    def test_paths_can_be_shared_but_not_generated(self):
        self.initialize()
        self.configure(**{"metadata-dir": str(self.shared)})
        self.assertEqual(self.project.metadata_dir, self.shared)
        self.configure(**{"metadata-dir": "build/metadata"})
        with self.assertRaisesRegex(BookError, "generated"):
            _ = self.project.metadata_dir
        self.configure(
            **{"metadata-dir": "../../metadata", "bibliography": "build/references.bib"}
        )
        with self.assertRaisesRegex(BookError, "generated"):
            _ = self.project.bibliography

    def test_chapter_validation(self):
        self.initialize()
        for chapters in (
            ["missing.md"],
            ["../outside.md"],
            [str(self.chapter)],
            ["chapters/01-arrival.md"] * 2,
            ["build/chapter.md"],
        ):
            with self.subTest(chapters=chapters):
                self.configure(chapters=chapters)
                with self.assertRaises(BookError):
                    self.project.load_book()

    def test_chapter_symlink_cannot_escape_book(self):
        self.initialize()
        outside = self.base / "outside.md"
        outside.write_text("Private text.")
        self.chapter.unlink()
        self.chapter.symlink_to(outside)
        with self.assertRaisesRegex(BookError, "inside the book"):
            self.project.load_book()

    def test_clean_keeps_sources_and_shared_metadata(self):
        self.sample()
        (self.root / "build").mkdir()
        (self.root / "build/book.html").write_text("Generated.")
        self.project.clean()
        self.project.clean()
        self.assertFalse((self.root / "build").exists())
        self.assertTrue(self.chapter.is_file())
        self.assertTrue((self.shared / "characters/john-doe.md").is_file())

    def test_clean_refuses_symlinks(self):
        self.initialize()
        (self.root / "build").symlink_to(self.shared, target_is_directory=True)
        with self.assertRaisesRegex(BookError, "symbolic link"):
            self.project.clean()
        self.assertTrue((self.shared / "references.bib").is_file())

    def test_expand_validates_tags(self):
        entities = {("character", "john-doe"): {"short": "John"}}
        for text in (
            "{{character:missing.short}}",
            "{{character:john-doe.full}}",
            "{{animal:john-doe.short}}",
            "{{character:john-doe}}",
            "{{character:",
            "broken}}",
            "{{{{character:john-doe.short}}",
        ):
            with self.subTest(text=text), self.assertRaises(BookError):
                books.expand_text(text, entities, "chapter.md", {})


@unittest.skipUnless(shutil.which("pandoc"), "Pandoc is required for build tests")
class PandocTests(ProjectCase):
    def test_real_pandoc_expands_and_preserves_identity(self):
        self.sample()
        document, chapters = self.project.prepare_book()
        serialized = json.dumps(document)
        self.assertIn("ref-character-john-doe-1", serialized)
        self.assertIn("ref-character-john-doe-2", serialized)
        self.assertIn('"data-entity", "place:greyhaven"', serialized)
        self.assertNotIn("{{", serialized)
        self.assertEqual(len(chapters), 1)
        self.project.check()

    def test_code_math_and_link_targets_are_literal(self):
        self.sample()
        self.chapter.write_text(
            "# Examples\n\n`{{character:missing.short}}`\n\n"
            "```text\n{{character:missing.short}}\n```\n\n"
            "$x_{{unknown}}$\n\n[link](https://example.com/{{unknown}})\n\n"
            "**{{character:john-doe.short}}**\n"
        )
        document, _ = self.project.prepare_book()
        serialized = json.dumps(document)
        self.assertIn("{{character:missing.short}}", serialized)
        self.assertIn("{{unknown}}", serialized)
        self.assertIn("ref-character-john-doe-1", serialized)

    def test_invalid_prose_tags(self):
        self.sample()
        for text in (
            "{{c",
            "{{character:john-doe.unknown}}",
            "{{character:missing.short}}",
            "{{ character:john-doe.short }}",
            "{{character:john-doe}}",
        ):
            with self.subTest(text=text):
                self.chapter.write_text(f"# Chapter\n\n{text}\n")
                with self.assertRaises(BookError):
                    self.project.prepare_book()

    def test_builds_html_and_epub_without_private_notes(self):
        self.sample()
        entity = self.shared / "characters/john-doe.md"
        entity.write_text(
            entity.read_text().replace(
                "public_description: ''",
                "public_description: PUBLIC_DESCRIPTION_NOT_SELECTED",
            )
            + "\nPRIVATE_CHARACTER_NOTES\n"
        )
        with (self.root / books.CONFIG).open("a") as stream:
            stream.write('private-notes = "PRIVATE_BOOK_NOTES"\n')
        self.chapter.write_text(
            "---\nprivate_notes: PRIVATE_CHAPTER_NOTES\n---\n\n"
            + self.chapter.read_text()
        )
        second = self.chapter.parent / "00-second.md"
        second.write_text("# Second chapter\n\n{{character:john-doe.short}} left.\n")
        self.configure(chapters=["chapters/01-arrival.md", "chapters/00-second.md"])
        html = self.project.build("html").read_text()
        self.assertIn("John", html)
        self.assertIn("Greyhaven", html)
        self.assertIn('data-entity="character:john-doe"', html)
        self.assertLess(html.index('id="arrival"'), html.index('id="second-chapter"'))
        self.assertIn("ref-character-john-doe-3", html)
        self.assertNotIn("/__mightier/client.js", html)
        with zipfile.ZipFile(self.project.build()) as archive:
            self.assertEqual(archive.read("mimetype"), b"application/epub+zip")
            self.assertEqual(archive.infolist()[0].filename, "mimetype")
            self.assertEqual(archive.infolist()[0].compress_type, zipfile.ZIP_STORED)
            documents = []
            for name in archive.namelist():
                if name.endswith((".xhtml", ".opf", ".xml", ".ncx")):
                    content = archive.read(name)
                    ET.fromstring(content)
                    documents.append(content.decode())
            contents = "\n".join(documents)
        for output in (html, contents):
            for private in (
                "PRIVATE_CHARACTER_NOTES",
                "PRIVATE_BOOK_NOTES",
                "PRIVATE_CHAPTER_NOTES",
                "PUBLIC_DESCRIPTION_NOT_SELECTED",
            ):
                self.assertNotIn(private, output)
            self.assertNotIn("{{character:", output)

    def test_references_append_preserve_and_resolve(self):
        self.sample()
        original = self.project.bibliography.read_text()
        self.project.new_reference("doe2024")
        self.assertTrue(self.project.bibliography.read_text().startswith(original))
        before = self.project.bibliography.read_bytes()
        with self.assertRaisesRegex(BookError, "already exists"):
            self.project.new_reference("doe2024")
        self.assertEqual(self.project.bibliography.read_bytes(), before)
        self.chapter.write_text("# Notes\n\nSee [@doe2024].\n")
        html = self.project.build("html").read_text()
        self.assertRegex(html, r"Replace with the\s+Reference Title")
        self.assertIn("References", html)
        self.assertIn("ref-doe2024", html)
        self.chapter.write_text("# Notes\n\nSee [@missing2024].\n")
        with self.assertRaisesRegex(BookError, "citation.*not found"):
            self.project.check()

    def test_failed_build_keeps_previous_output(self):
        self.sample()
        output = self.project.build("html")
        original = output.read_bytes()
        self.chapter.write_text("# Broken\n\n{{character:missing.short}}\n")
        with self.assertRaises(BookError):
            self.project.build("html")
        self.assertEqual(output.read_bytes(), original)

    def test_epubcheck_failure_keeps_previous_output(self):
        self.sample()
        output = self.root / "build/book.epub"
        output.parent.mkdir()
        output.write_bytes(b"previous output")
        real_run = subprocess.run

        def run(arguments, **kwargs):
            if arguments[0] == "epubcheck":
                return subprocess.CompletedProcess(arguments, 1)
            return real_run(arguments, **kwargs)

        with (
            patch.object(books.shutil, "which", return_value="/fake/epubcheck"),
            patch.object(books.subprocess, "run", side_effect=run),
            self.assertRaisesRegex(BookError, "EPUBCheck failed"),
        ):
            self.project.build()
        self.assertEqual(output.read_bytes(), b"previous output")

    def test_build_refuses_symlinked_output(self):
        self.sample()
        (self.root / "build").symlink_to(self.shared, target_is_directory=True)
        with self.assertRaisesRegex(BookError, "symbolic link"):
            self.project.build("html")
