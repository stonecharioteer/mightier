"""Shared metadata and headless Neovim completion tests."""

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from test_books import ProjectCase

from mightier import completion
from mightier.books import BookError, Metadata, Project

ROOT = Path(__file__).resolve().parents[1]


class CompletionTests(ProjectCase):
    def setUp(self):
        super().setUp()
        self.sample()
        self.character = self.shared / "characters/john-doe.md"
        self.character.write_text(
            "---\nid: john-doe\ntype: character\nnames:\n"
            "  full: Jöhn Doe\n  short: Jöhn\n  surname: Doe\n  nickname: Jack\n"
            "public_description: A physician.\nprivate_metadata: SECRET_METADATA\n"
            "---\n\n# Notes\n\nSECRET_NOTES\n",
            encoding="utf-8",
        )

    def test_collects_forms_and_descriptions_without_notes(self):
        result = completion.collect(self.project)
        self.assertEqual(result["metadata_dir"], str(self.shared))
        self.assertEqual(result["errors"], [])
        self.assertEqual(
            result["records"][0],
            {
                "type": "character",
                "id": "john-doe",
                "names": {
                    "full": "Jöhn Doe",
                    "short": "Jöhn",
                    "surname": "Doe",
                    "nickname": "Jack",
                },
                "description": "A physician.",
            },
        )
        self.assertNotIn("SECRET", str(result))

    def test_standalone_metadata_completion_matches_book_context(self):
        self.assertEqual(
            completion.collect(Metadata(self.shared)), completion.collect(self.project)
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "mightier",
                "completion-data",
                "--metadata-dir",
                str(self.shared),
            ],
            cwd=self.base,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        import json

        self.assertEqual(json.loads(result.stdout), completion.collect(self.project))
        self.assertNotIn("SECRET_NOTES", result.stdout)

    def test_invalid_record_does_not_remove_valid_records(self):
        (self.shared / "characters/broken.md").write_text("---\nid: broken\n")
        result = completion.collect(self.project)
        self.assertEqual(len(result["records"]), 2)
        self.assertEqual(len(result["errors"]), 1)

    def test_build_and_completion_validate_names_consistently(self):
        self.character.write_text(
            self.character.read_text().replace("short: Jöhn", "short: 12")
        )
        result = completion.collect(self.project)
        self.assertEqual(len(result["records"]), 1)
        self.assertIn("single-line text", result["errors"][0])
        with self.assertRaisesRegex(BookError, "single-line text"):
            self.project.load_entities()

    def environment(self, **extra):
        other = Project(self.base / "books/book-02")
        other.initialize(self.shared)
        return dict(
            os.environ,
            MIGHTIER_TEST_ROOT=str(self.root),
            MIGHTIER_SOURCE_ROOT=str(ROOT),
            MIGHTIER_TEST_METADATA=str(self.shared),
            MIGHTIER_TEST_PYTHON=sys.executable,
            MIGHTIER_OTHER_ROOT=str(other.root),
            **extra,
        )

    @unittest.skipUnless(shutil.which("nvim"), "Neovim is required")
    def test_neovim_source_and_mappings(self):
        result = subprocess.run(
            [
                "nvim",
                "--headless",
                "-u",
                "NONE",
                "-l",
                str(ROOT / "tests/nvim_completion.lua"),
            ],
            cwd=self.root,
            env=self.environment(),
            text=True,
            capture_output=True,
            timeout=90,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Neovim completion checks passed", result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("nvim"), "Neovim is required")
    def test_blink_insert_mode_completion(self):
        data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
        blink = data_home / "nvim/lazy/blink.cmp"
        if not blink.is_dir():
            self.skipTest("The local blink.cmp installation is required")
        result = subprocess.run(
            [
                "nvim",
                "--headless",
                "-u",
                "NONE",
                "-c",
                "luafile " + str(ROOT / "tests/nvim_blink.lua"),
            ],
            cwd=self.root,
            env=self.environment(MIGHTIER_BLINK=str(blink)),
            text=True,
            capture_output=True,
            timeout=90,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            "Blink insert-mode completion checks passed", result.stdout + result.stderr
        )
