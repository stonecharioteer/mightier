"""Test real shell completion without dispatching commands or writing files."""

import argparse
import os
import shutil
import subprocess
import sys
import unittest
from contextlib import chdir
from pathlib import Path

from test_books import ProjectCase

from mightier.books import Project
from mightier.chapters import new_chapter
from mightier.shell_completion import chapter_names, record_names, script


class ShellCompletionTests(ProjectCase):
    def setUp(self):
        super().setUp()
        self.sample()
        new_chapter(self.project, "02-return", "The return")
        self.arguments = argparse.Namespace(project=self.root)

    def complete(self, line, cwd=None):
        output = self.base / "completion-result"
        environment = dict(
            os.environ,
            _ARGCOMPLETE="1",
            _ARGCOMPLETE_STDOUT_FILENAME=str(output),
            _ARGCOMPLETE_IFS="\n",
            _ARGCOMPLETE_DFS="\t",
            _ARGCOMPLETE_SHELL="fish",
            _ARGCOMPLETE_SUPPRESS_SPACE="1",
            COMP_LINE=line,
            COMP_POINT=str(len(line)),
        )
        result = subprocess.run(
            [sys.executable, "-m", "mightier"],
            cwd=cwd or self.base,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        return {entry.split("\t", 1)[0] for entry in output.read_text().splitlines()}

    def test_chapter_names_come_from_the_selected_book(self):
        other = Project(self.base / "book-two")
        other.initialize()
        new_chapter(other, "02-different")
        self.assertEqual(
            chapter_names("", self.arguments),
            {
                "01-arrival": "chapters/01-arrival.md",
                "02-return": "chapters/02-return.md",
            },
        )
        values = chapter_names("", argparse.Namespace(project=other.root))
        self.assertIn("02-different", values)
        self.assertNotIn("02-return", values)

    def test_current_project_is_discovered_from_nested_folder(self):
        with chdir(self.chapter.parent):
            result = chapter_names("", argparse.Namespace(project=None))
        self.assertIn("02-return", result)

    def test_ambiguous_names_and_explicit_path_prefixes_use_paths(self):
        alternate = self.root / "part-two/01-arrival.md"
        alternate.parent.mkdir()
        alternate.write_text("# Another arrival\n")
        self.configure(chapters=["chapters/01-arrival.md", "part-two/01-arrival.md"])
        values = chapter_names("", self.arguments)
        self.assertEqual(
            set(values), {"chapters/01-arrival.md", "part-two/01-arrival.md"}
        )
        self.assertNotIn("01-arrival", values)
        self.assertIn(
            "chapters/01-arrival.md", chapter_names("chapters/", self.arguments)
        )

    def test_invalid_or_missing_projects_have_no_chapter_suggestions(self):
        self.assertEqual(chapter_names("", argparse.Namespace(project=self.base)), {})
        (self.root / "mightier.toml").write_text("broken = [\n")
        self.assertEqual(chapter_names("", self.arguments), {})

    def test_edit_completion_reads_shared_metadata_ids(self):
        self.assertEqual(record_names("character")("", self.arguments), ["john-doe"])
        self.assertEqual(record_names("place")("", self.arguments), ["greyhaven"])

    def test_actual_protocol_completes_subcommands_and_flags(self):
        self.assertIn("chapter", self.complete("mightier "))
        self.assertEqual(self.complete("mightier chapter "), {"new", "rename"})
        self.assertEqual(
            self.complete("mightier character "), {"new", "edit", "rename"}
        )
        self.assertIn("--project", self.complete("mightier --pr"))
        self.assertIn("--title", self.complete("mightier chapter new 03-next --t"))

    def test_project_options_complete_directories_not_files(self):
        (self.base / "books/not-a-book.txt").write_text("Not a project directory.")
        values = self.complete("mightier -P books/")
        self.assertIn("books/book-01/", values)
        self.assertNotIn("books/not-a-book.txt", values)
        self.assertEqual(values, self.complete("mightier --project books/"))

    @unittest.skipUnless(shutil.which("pandoc"), "Pandoc is required for reference IDs")
    def test_edit_reference_completion_uses_the_selected_bibliography(self):
        self.project.new_reference("doe2024")
        self.assertEqual(
            self.complete("mightier -P books/book-01 reference edit do"), {"doe2024"}
        )

    def test_actual_protocol_completes_selected_chapters_and_prefixes(self):
        prefix = "mightier -P books/book-01 chapter rename "
        self.assertEqual(self.complete(prefix), {"01-arrival", "02-return"})
        self.assertEqual(self.complete(prefix + "02-"), {"02-return"})
        self.assertEqual(
            self.complete("mightier --project books/book-01 chapter rename "),
            {"01-arrival", "02-return"},
        )
        self.assertEqual(
            self.complete("mightier chapter rename ", cwd=self.chapter.parent),
            {"01-arrival", "02-return"},
        )

    def test_completion_refreshes_after_additions_and_renames(self):
        from mightier.chapters import rename_chapter

        prefix = "mightier -P books/book-01 chapter rename "
        self.assertIn("02-return", self.complete(prefix))
        rename_chapter(self.project, "02-return", "02-homecoming")
        self.assertEqual(self.complete(prefix), {"01-arrival", "02-homecoming"})

    def test_new_names_are_not_completed_with_existing_records_or_files(self):
        self.assertEqual(self.complete("mightier -P books/book-01 chapter new "), set())
        self.assertEqual(
            self.complete("mightier -P books/book-01 character new "), set()
        )
        self.assertEqual(
            self.complete("mightier -P books/book-01 chapter rename 02-return "), set()
        )
        self.assertEqual(
            self.complete("mightier -P books/book-01 character edit jo"), {"john-doe"}
        )

    def test_character_rename_completes_ids_and_refreshes_after_rename(self):
        from mightier.renaming import rename_entity

        prefix = "mightier -P books/book-01 character rename "
        self.assertEqual(self.complete(prefix), {"john-doe"})
        self.assertEqual(self.complete(prefix + "john-doe "), set())
        self.assertIn("--scope", self.complete(prefix + "john-doe jane-doe --s"))
        rename_entity(self.project, "character", "john-doe", "jane-doe", self.base)
        self.assertEqual(self.complete(prefix), {"jane-doe"})
        self.assertEqual(
            self.complete("mightier -P books/book-01 character edit ja"), {"jane-doe"}
        )

    def test_completion_is_read_only_and_does_not_create_logs(self):
        before = {
            path.relative_to(self.root): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }
        self.complete("mightier chapter rename ", cwd=self.root)
        after = {
            path.relative_to(self.root): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)
        self.assertFalse((self.root / "mightier.log").exists())
        self.assertFalse((self.root / ".mightier-log.lock").exists())

    def test_script_generation_is_plain_text_without_a_project(self):
        for shell in ("fish", "bash", "zsh"):
            result = subprocess.run(
                [sys.executable, "-m", "mightier", "completion", shell],
                cwd=self.base,
                text=True,
                capture_output=True,
                check=False,
                timeout=15,
                env=dict(os.environ, FORCE_COLOR="1"),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, script(shell))
            self.assertNotIn("\033", result.stdout)

    @unittest.skipUnless(shutil.which("fish"), "Fish is required for integration tests")
    def test_fish_completes_a_quoted_project_path(self):
        project = Project(self.base / "book with spaces")
        project.initialize(self.shared)
        new_chapter(project, "02-in-another-book")
        hook = self.base / "mightier.fish"
        hook.write_text(script("fish"))
        environment = dict(
            os.environ,
            PATH=str(Path(sys.executable).parent)
            + os.pathsep
            + os.environ.get("PATH", ""),
        )
        line = "mightier -P 'book with spaces' chapter rename "
        result = subprocess.run(
            [
                "fish",
                "--no-config",
                "-c",
                'source "$argv[1]"; complete -C "$argv[2]"',
                str(hook),
                line,
            ],
            cwd=self.base,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        values = {entry.split("\t", 1)[0] for entry in result.stdout.splitlines()}
        self.assertEqual(values, {"01-arrival", "02-in-another-book"})
