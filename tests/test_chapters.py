"""Chapter file operations preserve reading order, prose, and TOML comments."""

from pathlib import Path
from unittest.mock import patch

from test_books import ProjectCase

from mightier import chapters
from mightier.books import CONFIG, BookError


class ChapterTests(ProjectCase):
    def setUp(self):
        super().setUp()
        self.initialize()
        self.config = self.root / CONFIG

    def test_new_chapter_appends_and_derives_heading(self):
        path = chapters.new_chapter(self.project, "02-the-return")
        self.assertEqual(path, self.root / "chapters/02-the-return.md")
        self.assertEqual(path.read_text(), "# The return\n\n")
        self.assertEqual(
            self.project.config()["chapters"],
            ["chapters/01-arrival.md", "chapters/02-the-return.md"],
        )
        self.assertEqual(self.project.metadata_dir, self.shared)

    def test_explicit_title_and_toml_comments_are_preserved(self):
        text = self.config.read_text().replace(
            'chapters = ["chapters/01-arrival.md"]',
            "# Keep this reading order\nchapters = [\n  'chapters/01-arrival.md', # opening\n]\n\n[custom]\nmessage = 'Keep this too'",
        )
        self.config.write_text(text)
        path = chapters.new_chapter(self.project, "02-return", "Éva returns")
        self.assertEqual(path.read_text(), "# Éva returns\n\n")
        chapters.rename_chapter(self.project, "02-return", "02-homecoming")
        updated = self.config.read_text()
        for content in (
            "# Keep this reading order",
            "# opening",
            "[custom]",
            "message = 'Keep this too'",
        ):
            self.assertIn(content, updated)
        self.assertEqual(self.project.config()["custom"]["message"], "Keep this too")

    def test_existing_chapter_and_unlisted_file_are_not_overwritten(self):
        original = self.config.read_bytes()
        with self.assertRaisesRegex(BookError, "already exists"):
            chapters.new_chapter(self.project, "01-arrival")
        orphan = self.root / "chapters/02-return.md"
        orphan.write_text("Keep this unpublished scene.")
        with self.assertRaisesRegex(BookError, "already exists"):
            chapters.new_chapter(self.project, "02-return")
        self.assertEqual(orphan.read_text(), "Keep this unpublished scene.")
        self.assertEqual(self.config.read_bytes(), original)

    def test_unsafe_names_and_titles_are_rejected(self):
        original = self.config.read_bytes()
        for name in (
            "../outside",
            "/absolute",
            "has spaces",
            "has--gap",
            "file.md",
            "",
            "Uppercase",
        ):
            with self.subTest(name=name), self.assertRaises(BookError):
                chapters.new_chapter(self.project, name)
            with self.subTest(name=name), self.assertRaises(BookError):
                chapters.rename_chapter(self.project, "01-arrival", name)
        for title in ("", " ", "One\n# Two"):
            with self.subTest(title=title), self.assertRaises(BookError):
                chapters.new_chapter(self.project, "02-return", title)
        self.assertEqual(self.config.read_bytes(), original)

    def test_rename_preserves_order_prose_and_permissions(self):
        second = chapters.new_chapter(self.project, "02-return", "The original title")
        third = chapters.new_chapter(self.project, "03-departure")
        content = b"# The original title\r\n\r\nAn unchanged scene.\r\n"
        second.write_bytes(content)
        second.chmod(0o600)
        self.config.chmod(0o600)
        target = chapters.rename_chapter(self.project, "02-return", "02-homecoming")
        self.assertFalse(second.exists())
        self.assertEqual(target.read_bytes(), content)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            self.project.config()["chapters"],
            [
                "chapters/01-arrival.md",
                "chapters/02-homecoming.md",
                "chapters/03-departure.md",
            ],
        )
        self.assertTrue(third.exists())

    def test_rename_accepts_book_relative_path_and_preserves_its_directory(self):
        alternate = self.root / "part-two/01-arrival.md"
        alternate.parent.mkdir()
        alternate.write_text("# Another arrival\n")
        self.configure(chapters=["chapters/01-arrival.md", "part-two/01-arrival.md"])
        with self.assertRaisesRegex(BookError, "ambiguous"):
            chapters.rename_chapter(self.project, "01-arrival", "new-name")
        target = chapters.rename_chapter(
            self.project, "part-two/01-arrival.md", "01-second-arrival"
        )
        self.assertEqual(target, self.root / "part-two/01-second-arrival.md")
        self.assertTrue(self.chapter.exists())
        self.assertEqual(
            self.project.config()["chapters"][1], "part-two/01-second-arrival.md"
        )

    def test_rename_accepts_filename(self):
        target = chapters.rename_chapter(self.project, "01-arrival.md", "01-opening")
        self.assertTrue(target.is_file())

    def test_missing_unchanged_and_conflicting_names_are_rejected(self):
        original = self.config.read_bytes()
        with self.assertRaisesRegex(BookError, "Unknown chapter"):
            chapters.rename_chapter(self.project, "missing", "new-name")
        with self.assertRaisesRegex(BookError, "must differ"):
            chapters.rename_chapter(self.project, "01-arrival", "01-arrival")
        other = self.root / "chapters/02-existing.md"
        other.write_text("Keep this scene.")
        with self.assertRaisesRegex(BookError, "already exists"):
            chapters.rename_chapter(self.project, "01-arrival", "02-existing")
        self.assertEqual(other.read_text(), "Keep this scene.")
        self.assertEqual(self.config.read_bytes(), original)

    def test_failed_config_write_removes_new_chapter(self):
        original = self.config.read_bytes()
        with (
            patch.object(
                chapters, "write_config", side_effect=OSError("Disk write failed")
            ),
            self.assertRaises(OSError),
        ):
            chapters.new_chapter(self.project, "02-return")
        self.assertFalse((self.root / "chapters/02-return.md").exists())
        self.assertEqual(self.config.read_bytes(), original)

    def test_failed_rename_config_write_keeps_original(self):
        original = self.config.read_bytes()
        content = self.chapter.read_bytes()
        with (
            patch.object(
                chapters, "write_config", side_effect=OSError("Disk write failed")
            ),
            self.assertRaises(OSError),
        ):
            chapters.rename_chapter(self.project, "01-arrival", "01-opening")
        self.assertEqual(self.chapter.read_bytes(), content)
        self.assertFalse((self.root / "chapters/01-opening.md").exists())
        self.assertEqual(self.config.read_bytes(), original)

    def test_failed_source_removal_rolls_back_config_and_keeps_original(self):
        original = self.config.read_bytes()
        unlink = Path.unlink

        def fail_source(path, *args, **kwargs):
            if path == self.chapter:
                raise PermissionError("Cannot remove source")
            return unlink(path, *args, **kwargs)

        with (
            patch.object(Path, "unlink", fail_source),
            self.assertRaises(PermissionError),
        ):
            chapters.rename_chapter(self.project, "01-arrival", "01-opening")
        self.assertTrue(self.chapter.is_file())
        self.assertFalse((self.root / "chapters/01-opening.md").exists())
        self.assertEqual(self.config.read_bytes(), original)

    def test_concurrent_config_edit_is_not_discarded(self):
        original = self.config.read_bytes()
        write_config = chapters.write_config

        def edit_before_write(path, expected, replacement):
            path.write_bytes(original + b"# A concurrent edit\n")
            write_config(path, expected, replacement)

        with (
            patch.object(chapters, "write_config", side_effect=edit_before_write),
            self.assertRaisesRegex(BookError, "concurrent edit was kept"),
        ):
            chapters.new_chapter(self.project, "02-return")
        self.assertEqual(self.config.read_bytes(), original + b"# A concurrent edit\n")
        self.assertFalse((self.root / "chapters/02-return.md").exists())

    def test_failure_after_config_commit_does_not_delete_a_listed_chapter(self):
        write_config = chapters.write_config

        def fail_after_commit(path, expected, replacement):
            write_config(path, expected, replacement)
            raise OSError("Failure after commit")

        with (
            patch.object(chapters, "write_config", side_effect=fail_after_commit),
            self.assertRaises(OSError),
        ):
            chapters.new_chapter(self.project, "02-return")
        self.assertTrue((self.root / "chapters/02-return.md").exists())
        self.assertEqual(len(self.project.load_book()[1]), 2)

    def test_config_symlink_and_escaping_chapter_directory_are_rejected(self):
        elsewhere = self.base / "config.toml"
        self.config.rename(elsewhere)
        self.config.symlink_to(elsewhere)
        with self.assertRaisesRegex(BookError, "regular mightier.toml"):
            chapters.new_chapter(self.project, "02-return")
        self.config.unlink()
        elsewhere.rename(self.config)
        self.chapter.rename(self.root / "01-arrival.md")
        self.configure(chapters=["01-arrival.md"])
        (self.root / "chapters").rmdir()
        outside = self.base / "outside"
        outside.mkdir()
        (self.root / "chapters").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(BookError, "inside the book folder"):
            chapters.new_chapter(self.project, "02-return")
        self.assertEqual(list(outside.iterdir()), [])
