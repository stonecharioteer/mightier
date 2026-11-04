"""Exercise the installed CLI, never tooling copied into a writing project."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def run_cli(self, *arguments, cwd=None, success=True, environment=None):
        result = subprocess.run(
            [sys.executable, "-m", "mightier", *arguments],
            cwd=cwd or self.root,
            env=dict(os.environ, **(environment or {})),
            text=True,
            capture_output=True,
            check=False,
            timeout=90,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def test_help_has_logo_quote_and_nested_commands(self):
        for arguments in (("--help",), ("help",), ()):
            with self.subTest(arguments=arguments):
                result = self.run_cli(*arguments)
                self.assertIn("The Pen is mightier than the Sword.", result.stdout)
                self.assertIn("▀", result.stdout)
                self.assertIn("Show the logo", result.stdout)
                self.assertIn("new", result.stdout)
                self.assertNotIn("new-character", result.stdout)
                self.assertNotIn("new-place", result.stdout)
                self.assertNotIn("\033_G", result.stdout)
        result = self.run_cli("new", "--help")
        self.assertIn("character", result.stdout)
        self.assertIn("reference", result.stdout)

    @unittest.skipUnless(shutil.which("uv"), "uv is required")
    def test_exact_uv_run_help_command(self):
        result = subprocess.run(
            ["uv", "run", "--locked", "mightier", "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("The Pen is mightier than the Sword.", result.stdout)
        self.assertIn("Show the logo", result.stdout)
        self.assertIn("▀", result.stdout)

    def test_forced_colour_does_not_emit_image_protocol_to_a_pipe(self):
        result = self.run_cli(
            "--help", environment={"FORCE_COLOR": "1", "TERM_PROGRAM": "ghostty"}
        )
        self.assertIn("\033[", result.stdout)
        self.assertNotIn("\033_G", result.stdout)

    def test_init_writes_relative_paths_from_current_directory(self):
        self.run_cli("init", "books/book-01", "--metadata-dir", "shared")
        config = self.root / "books/book-01/mightier.toml"
        self.assertIn('metadata-dir = "../../shared"', config.read_text())
        self.assertIn(
            'bibliography = "../../shared/references.bib"', config.read_text()
        )
        self.assertTrue((self.root / "shared/references.bib").is_file())
        self.assertTrue((self.root / "mightier.log").is_file())
        self.assertFalse((self.root / "books/book-01/pyproject.toml").exists())

    @unittest.skipUnless(shutil.which("pandoc"), "Pandoc is required")
    def test_complete_project_workflow_from_other_directories(self):
        self.run_cli("init", "book one", "--metadata-dir", "shared")
        book = self.root / "book one"
        self.run_cli("new", "character", "john-doe", cwd=book)
        self.run_cli("new", "place", "greyhaven", cwd=book)
        self.run_cli("new", "reference", "doe2024", cwd=book)
        self.run_cli("new", "character", "john-doe", cwd=book, success=False)
        self.run_cli("new", "character", "../outside", cwd=book, success=False)
        self.run_cli("new", "character", "bad; touch injected", cwd=book, success=False)
        self.assertFalse((book / "injected").exists())
        self.run_cli("check", cwd=book / "chapters")
        self.run_cli("--project", str(book), "build")
        self.run_cli("build", "--format", "html", cwd=book)
        self.assertTrue((book / "build/book.epub").is_file())
        self.assertTrue((book / "build/book.html").is_file())
        result = self.run_cli(
            "--project", str(book), "completion-data", environment={"FORCE_COLOR": "1"}
        )
        data = json.loads(result.stdout)
        self.assertEqual(len(data["records"]), 2)
        self.run_cli("clean", cwd=book)
        self.assertFalse((book / "build").exists())
        self.assertTrue((self.root / "shared/characters/john-doe.md").is_file())

    @unittest.skipUnless(shutil.which("pandoc"), "Pandoc is required")
    def test_new_and_rename_chapter_update_the_manifest(self):
        self.run_cli("init", "book one", "--metadata-dir", "shared")
        book = self.root / "book one"
        self.run_cli(
            "-P", str(book), "new", "chapter", "02-return", "--title", "The return"
        )
        source = book / "chapters/02-return.md"
        self.assertEqual(source.read_text(), "# The return\n\n")
        self.run_cli("-P", str(book), "rename", "chapter", "02-return", "02-homecoming")
        self.assertFalse(source.exists())
        target = book / "chapters/02-homecoming.md"
        self.assertEqual(target.read_text(), "# The return\n\n")
        self.run_cli("new", "chapter", "02-homecoming", cwd=book, success=False)
        self.run_cli("rename", "chapter", "missing", "03-next", cwd=book, success=False)
        self.run_cli("check", cwd=book)
        self.run_cli("build", "--format", "html", cwd=book)
        self.assertIn("The return", (book / "build/book.html").read_text())
        for arguments in (
            ("new", "chapter", "--help"),
            ("rename", "chapter", "--help"),
        ):
            self.assertIn("chapter", self.run_cli(*arguments).stdout)

    def test_project_short_option_matches_long_option(self):
        self.run_cli("init", "books/book one", "--metadata-dir", "shared")
        self.run_cli("init", "books/book two", "--metadata-dir", "shared")
        self.run_cli("-P", "books/book one", "new", "character", "john-doe")
        short = self.run_cli("-P", "books/book two", "completion-data")
        long = self.run_cli("--project", "books/book two", "completion-data")
        self.assertEqual(json.loads(short.stdout), json.loads(long.stdout))
        self.assertEqual(json.loads(short.stdout)["records"][0]["id"], "john-doe")
        self.assertTrue((self.root / "shared/characters/john-doe.md").is_file())
        help_output = self.run_cli("--help").stdout
        self.assertRegex(help_output, r"-P(?: PROJECT)?, --project PROJECT")

    def test_missing_project_and_invalid_port_have_clear_errors(self):
        result = self.run_cli("build", success=False)
        self.assertIn("mightier init", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.run_cli("init")
        result = self.run_cli("serve", "--port", "70000", success=False)
        self.assertIn("Port must", result.stderr)

    def test_logs_follow_current_folder_not_project(self):
        self.run_cli("init", "book")
        self.run_cli("--project", "book", "completion-data")
        self.assertTrue((self.root / "mightier.log").is_file())
        self.assertFalse((self.root / "book/mightier.log").exists())
        self.assertIn("DEBUG", (self.root / "mightier.log").read_text())
        folder = self.root / "no-logs"
        folder.mkdir()
        self.run_cli("--log-level", "off", "--help", cwd=folder)
        self.assertFalse((folder / "mightier.log").exists())
