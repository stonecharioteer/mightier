"""Individual record editing without shell interpolation or destructive saves."""

import os
import shutil
import subprocess
import unittest
from unittest.mock import patch

from test_books import ProjectCase

from mightier.books import BookError
from mightier.editing import edit, editor_arguments


class EditingTests(ProjectCase):
    def test_individual_character_file_and_validation(self):
        self.sample()
        path = self.shared / "characters/john-doe.md"

        def change(arguments, **kwargs):
            self.assertEqual(arguments[-1], str(path))
            path.write_text(path.read_text().replace("short: John", "short: Jack"))
            return subprocess.CompletedProcess(arguments, 0)

        with (
            patch.dict(os.environ, {"EDITOR": "nvim", "VISUAL": ""}),
            patch("mightier.editing.subprocess.run", side_effect=change),
        ):
            edit(self.project, "character", "john-doe")
        self.assertEqual(
            self.project.load_entities()[("character", "john-doe")]["short"], "Jack"
        )

    def test_invalid_edits_are_kept(self):
        self.sample()
        path = self.shared / "characters/john-doe.md"

        def change(arguments, **kwargs):
            path.write_text("An unfinished edit.")
            return subprocess.CompletedProcess(arguments, 0)

        with (
            patch.dict(os.environ, {"EDITOR": "nvim", "VISUAL": ""}),
            patch("mightier.editing.subprocess.run", side_effect=change),
            self.assertRaisesRegex(BookError, "saved edit is invalid"),
        ):
            edit(self.project, "character", "john-doe")
        self.assertEqual(path.read_text(), "An unfinished edit.")

    def test_unknown_records_are_not_created(self):
        self.initialize()
        with patch("mightier.editing.subprocess.run") as run:
            with self.assertRaisesRegex(BookError, "Unknown place"):
                edit(self.project, "place", "missing")
            run.assert_not_called()

    def test_visual_precedence_and_no_shell(self):
        with patch.dict(os.environ, {"EDITOR": "ignored", "VISUAL": "nvim -f"}):
            result = editor_arguments(self.base / "file with spaces.md", 12)
        self.assertEqual(
            result, ["nvim", "-f", "+12", str(self.base / "file with spaces.md")]
        )

    @unittest.skipUnless(shutil.which("pandoc"), "Pandoc is required")
    def test_reference_opens_at_the_individual_entry(self):
        self.initialize()
        self.project.new_reference("first2024")
        self.project.new_reference("second2024")
        real_run = subprocess.run

        def run(arguments, **kwargs):
            if arguments[0] == "nvim":
                self.assertGreater(int(arguments[-2][1:]), 1)
                self.assertEqual(arguments[-1], str(self.project.bibliography))
                return subprocess.CompletedProcess(arguments, 0)
            return real_run(arguments, **kwargs)

        with (
            patch.dict(os.environ, {"EDITOR": "nvim", "VISUAL": ""}),
            patch("mightier.editing.subprocess.run", side_effect=run),
        ):
            edit(self.project, "reference", "second2024")
