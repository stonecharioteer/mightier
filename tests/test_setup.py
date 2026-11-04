"""Never run real Homebrew in tests."""

import io
import subprocess
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from mightier.books import BookError
from mightier.setup import setup


class SetupTests(unittest.TestCase):
    def run_setup(self, available, failure=False):
        with (
            patch(
                "mightier.setup.shutil.which",
                side_effect=lambda name: f"/fake/{name}" if name in available else None,
            ),
            patch(
                "mightier.setup.subprocess.run",
                return_value=subprocess.CompletedProcess([], int(failure)),
            ) as run,
            redirect_stdout(io.StringIO()),
        ):
            setup()
            return [call.args[0] for call in run.call_args_list]

    def test_available_tools_need_no_homebrew(self):
        self.assertEqual(self.run_setup({"pandoc", "epubcheck"}), [])

    def test_installs_missing_tools(self):
        self.assertEqual(
            self.run_setup({"brew"}),
            [["brew", "install", "pandoc"], ["brew", "install", "epubcheck"]],
        )

    def test_installs_only_missing_tool(self):
        self.assertEqual(
            self.run_setup({"brew", "pandoc"}), [["brew", "install", "epubcheck"]]
        )

    def test_missing_homebrew_has_instructions(self):
        with self.assertRaisesRegex(BookError, "https://brew.sh"):
            self.run_setup(set())

    def test_install_failure_stops_setup(self):
        with self.assertRaisesRegex(BookError, "Setup stopped"):
            self.run_setup({"brew"}, failure=True)
