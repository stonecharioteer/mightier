"""Shared record ID renames must not lose prose or leave scoped tags stale."""

import shutil
import subprocess
import sys
import unittest
from contextlib import chdir
from unittest.mock import patch

from test_books import ProjectCase

from mightier import renaming
from mightier.books import BookError, Metadata, Project, read_entity


class RenameTests(ProjectCase):
    def setUp(self):
        super().setUp()
        self.sample()
        self.other = Project(self.base / "books/book-02")
        self.other.initialize(self.shared)
        self.other_chapter = self.other.root / "chapters/01-arrival.md"
        self.other_chapter.write_text(
            "# Another book\n\n{{character:john-doe}} met {{character:john-doe.full}}.\n"
        )
        self.person = self.shared / "characters/john-doe.md"
        self.place = self.shared / "places/greyhaven.md"
        self.person.write_text(
            self.person.read_text()
            + "\nRemembers {{character:john-doe.short}} and {{place:greyhaven}}.\n"
        )
        self.place.write_text(
            self.place.read_text() + "\nHome of {{character:john-doe}}.\n"
        )

    def rename(self, old="john-doe", new="jane-doe", kind="character", **options):
        return renaming.rename_entity(
            self.project, kind, old, new, self.base, **options
        )

    def snapshots(self):
        return {
            path: path.read_bytes()
            for path in self.base.rglob("*")
            if path.suffix in (".md", ".toml", ".bib") and path.is_file()
        }

    def test_updates_both_books_shared_notes_and_record_id(self):
        names = read_entity("character", self.person)["names"]
        configs = {
            book.root / "mightier.toml": (book.root / "mightier.toml").read_bytes()
            for book in (self.project, self.other)
        }
        target = self.rename()
        self.assertEqual(target, self.shared / "characters/jane-doe.md")
        self.assertFalse(self.person.exists())
        data = read_entity("character", target)
        self.assertEqual(data["id"], "jane-doe")
        self.assertEqual(data["names"], names)
        self.assertIn("{{character:jane-doe.full}}", self.chapter.read_text())
        self.assertIn("{{character:jane-doe}}", self.other_chapter.read_text())
        self.assertIn("{{character:jane-doe}}", self.place.read_text())
        self.assertIn("{{character:jane-doe.short}}", target.read_text())
        for path, before in configs.items():
            self.assertEqual(path.read_bytes(), before)

    def test_place_renames_use_the_same_reference_update(self):
        self.person.write_text(
            self.person.read_text() + "\nVisits {{place:greyhaven.name}}.\n"
        )
        target = self.rename("greyhaven", "new-haven", "place")
        self.assertFalse(self.place.exists())
        self.assertEqual(read_entity("place", target)["id"], "new-haven")
        self.assertIn("{{place:new-haven.name}}", self.chapter.read_text())
        self.assertIn("{{place:new-haven}}", self.person.read_text())

    def test_unicode_crlf_comments_names_and_prose_are_preserved(self):
        self.chapter.write_bytes(
            "# Éva\r\n\r\nJohn Doe read {{character:john-doe}}.\r\n".encode()
        )
        self.person.write_bytes(
            self.person.read_bytes().replace(
                b"id: john-doe", b"id: 'john-doe' # stable ID"
            )
        )
        original = self.chapter.read_bytes()
        self.person.chmod(0o600)
        target = self.rename()
        self.assertEqual(
            self.chapter.read_bytes(),
            original.replace(b"{{character:john-doe}}", b"{{character:jane-doe}}"),
        )
        self.assertIn("id: 'jane-doe' # stable ID", target.read_text())
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertIn("full: John Doe", target.read_text())

    def test_names_are_literal_but_descriptions_and_examples_follow_the_id(self):
        self.person.write_text(
            self.person.read_text()
            .replace("short: John", 'short: "{{character:john-doe}}"')
            .replace(
                "public_description: ''",
                'public_description: "Sees {{character:john-doe.full}}"',
            )
        )
        self.chapter.write_text(
            self.chapter.read_text()
            + "\n`{{character:john-doe}}` and {{character:john-doe-junior}}.\n"
        )
        target = self.rename()
        self.assertEqual(
            read_entity("character", target)["names"]["short"], "{{character:john-doe}}"
        )
        self.assertIn("Sees {{character:jane-doe.full}}", target.read_text())
        self.assertIn("`{{character:jane-doe}}`", self.chapter.read_text())
        self.assertIn("{{character:john-doe-junior}}", self.chapter.read_text())

    def test_other_namespaces_keep_the_same_id(self):
        self.project.new_entity("place", "john-doe")
        self.chapter.write_text(
            self.chapter.read_text() + "\n{{place:john-doe}} is not a character.\n"
        )
        self.rename()
        self.assertIn("{{place:john-doe}}", self.chapter.read_text())
        self.assertTrue((self.shared / "places/john-doe.md").exists())

    def test_metadata_ignores_atomic_write_temporary_files(self):
        temporary = self.shared / "characters/.mightier-temporary.md"
        temporary.write_text("Incomplete write")
        self.assertIn(("character", "john-doe"), Metadata(self.shared).load_entities())

    def test_init_preserves_an_existing_metadata_ignore_file(self):
        ignore = self.shared / ".gitignore"
        ignore.write_text("# User rules\nprivate/\n")
        self.project.initialize()
        self.assertEqual(ignore.read_text(), "# User rules\nprivate/\n")

    def test_yaml_boolean_like_id_is_quoted(self):
        target = self.rename(new="yes")
        self.assertEqual(read_entity("character", target)["id"], "yes")

    def test_other_metadata_projects_and_nested_books_are_not_changed(self):
        separate = Project(self.root / "side-story")
        separate.initialize()
        separate.new_entity("character", "john-doe")
        path = separate.root / "chapters/01-arrival.md"
        path.write_text("# Separate\n\n{{character:john-doe}} stays here.\n")
        record = separate.metadata_dir / "characters/john-doe.md"
        before = {path: path.read_bytes(), record: record.read_bytes()}
        self.rename()
        for file, content in before.items():
            self.assertEqual(file.read_bytes(), content)

    def test_other_metadata_inside_a_book_source_tree_is_left_alone(self):
        separate = Project(self.base / "independent-book")
        separate.initialize(self.root / "independent-records")
        separate.new_entity("character", "john-doe")
        record = separate.metadata_dir / "characters/john-doe.md"
        record.write_text(
            record.read_text() + "\n{{character:john-doe}} belongs to another book.\n"
        )
        before = record.read_bytes()
        self.rename()
        self.assertEqual(record.read_bytes(), before)

    def test_overlapping_independent_metadata_roots_are_rejected(self):
        separate = Project(self.shared)
        separate.initialize(self.shared / "private-records")
        separate.new_entity("character", "john-doe")
        before = self.snapshots()
        with self.assertRaisesRegex(BookError, "non-overlapping"):
            self.rename()
        self.assertEqual(self.snapshots(), before)

    def test_metadata_in_the_selected_book_root_is_supported(self):
        flat = Project(self.base / "flat-book")
        flat.initialize(flat.root)
        flat.new_entity("character", "john-doe")
        chapter = flat.root / "chapters/01-arrival.md"
        chapter.write_text("# Opening\n\n{{character:john-doe}} arrived.\n")
        renaming.rename_entity(flat, "character", "john-doe", "jane-doe", self.base)
        self.assertIn("{{character:jane-doe}}", chapter.read_text())
        self.assertTrue((flat.root / "characters/jane-doe.md").exists())

    def test_books_named_like_build_directories_are_still_discovered(self):
        for name in ("build", "dist", "target"):
            book = Project(self.base / "books" / name)
            book.initialize(self.shared)
            (book.root / "chapters/01-arrival.md").write_text(
                "# Opening\n\n{{character:john-doe}} arrived.\n"
            )
        self.rename()
        for name in ("build", "dist", "target"):
            self.assertIn(
                "{{character:jane-doe}}",
                (self.base / "books" / name / "chapters/01-arrival.md").read_text(),
            )

    def test_unlisted_drafts_are_updated_but_generated_output_is_not(self):
        draft = self.root / "drafts/unlisted.md"
        draft.parent.mkdir()
        draft.write_text("{{character:john-doe}}")
        generated = self.root / "build/snapshot.md"
        generated.parent.mkdir()
        generated.write_text("{{character:john-doe}}")
        self.rename()
        self.assertEqual(draft.read_text(), "{{character:jane-doe}}")
        self.assertEqual(generated.read_text(), "{{character:john-doe}}")

    def test_collision_and_invalid_ids_leave_manuscripts_untouched(self):
        self.project.new_entity("character", "jane-doe")
        before = self.snapshots()
        with self.assertRaisesRegex(BookError, "already exists"):
            self.rename()
        for value in ("../outside", "Bad Name", "has--gap", "name.md", ""):
            with self.subTest(value=value), self.assertRaises(BookError):
                self.rename(new=value)
        with self.assertRaisesRegex(BookError, "Unknown character"):
            self.rename(old="missing", new="new-person")
        with self.assertRaisesRegex(BookError, "must differ"):
            self.rename(new="john-doe")
        self.assertEqual(self.snapshots(), before)

    def test_dry_run_does_not_modify_manuscripts_or_create_the_target(self):
        before = self.snapshots()
        self.rename(dry_run=True)
        self.assertEqual(self.snapshots(), before)
        self.assertFalse((self.shared / "characters/jane-doe.md").exists())
        self.assertFalse((self.shared / ".mightier-metadata.lock").exists())

    def test_midway_write_failure_rolls_back_all_changes(self):
        before = self.snapshots()
        original_write = renaming.atomic_write
        calls = 0

        def fail_second(path, expected, replacement):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("Simulated disk failure")
            return original_write(path, expected, replacement)

        with (
            patch.object(renaming, "atomic_write", side_effect=fail_second),
            self.assertRaises(OSError),
        ):
            self.rename()
        self.assertEqual(self.snapshots(), before)
        self.assertFalse((self.shared / "characters/jane-doe.md").exists())

    def test_failure_after_a_write_commit_is_also_rolled_back(self):
        before = self.snapshots()
        original_write = renaming.atomic_write
        first = True

        def fail_once(path, expected, replacement):
            nonlocal first
            original_write(path, expected, replacement)
            if first:
                first = False
                raise OSError("Failure after atomic commit")

        with (
            patch.object(renaming, "atomic_write", side_effect=fail_once),
            self.assertRaises(OSError),
        ):
            self.rename()
        self.assertEqual(self.snapshots(), before)

    def test_concurrent_edit_is_kept_with_both_record_ids_available(self):
        original_write = renaming.atomic_write
        changed_path = None

        def concurrent(path, expected, replacement):
            nonlocal changed_path
            original_write(path, expected, replacement)
            if changed_path is None:
                changed_path = path
                path.write_bytes(replacement + b"\nA concurrent user edit.\n")
                raise BookError("Concurrent edit")

        with (
            patch.object(renaming, "atomic_write", side_effect=concurrent),
            self.assertRaisesRegex(BookError, "both metadata records"),
        ):
            self.rename()
        self.assertIn(b"A concurrent user edit.", changed_path.read_bytes())
        self.assertTrue(self.person.exists())
        self.assertTrue((self.shared / "characters/jane-doe.md").exists())

    def test_change_to_an_unmodified_file_is_detected(self):
        scratch = self.root / "draft.md"
        scratch.write_text("No references yet.")
        original_write = renaming.atomic_write
        changed = False

        def concurrent(path, expected, replacement):
            nonlocal changed
            original_write(path, expected, replacement)
            if not changed:
                changed = True
                scratch.write_text("A new {{character:jane-doe}} reference.\n")

        with (
            patch.object(renaming, "atomic_write", side_effect=concurrent),
            self.assertRaisesRegex(BookError, "both metadata records"),
        ):
            self.rename()
        self.assertIn("{{character:jane-doe}}", scratch.read_text())
        self.assertTrue(self.person.exists())
        self.assertTrue((self.shared / "characters/jane-doe.md").exists())

    def test_a_new_consumer_during_rename_keeps_both_record_ids(self):
        original_write = renaming.atomic_write
        consumer = Project(self.base / "books/new-consumer")
        created = False

        def concurrent(path, expected, replacement):
            nonlocal created
            original_write(path, expected, replacement)
            if not created:
                created = True
                consumer.initialize(self.shared)
                (consumer.root / "chapters/01-arrival.md").write_text(
                    "# Opening\n\n{{character:jane-doe}} arrived.\n"
                )

        with (
            patch.object(renaming, "atomic_write", side_effect=concurrent),
            self.assertRaisesRegex(BookError, "both metadata records"),
        ):
            self.rename()
        self.assertTrue(self.person.exists())
        self.assertTrue((self.shared / "characters/jane-doe.md").exists())

    def test_simultaneous_renames_from_two_books_are_serialized(self):
        commands = [
            [
                sys.executable,
                "-m",
                "mightier",
                "-P",
                str(book.root),
                "character",
                "rename",
                "john-doe",
                target,
                "--scope",
                str(self.base),
            ]
            for book, target in ((self.project, "left-id"), (self.other, "right-id"))
        ]
        processes = [
            subprocess.Popen(
                command,
                cwd=self.base,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for command in commands
        ]
        try:
            outputs = [process.communicate(timeout=30) for process in processes]
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
        self.assertEqual(
            sorted(process.returncode for process in processes), [0, 1], outputs
        )
        ids = {path.stem for path in (self.shared / "characters").glob("*.md")}
        self.assertIn(ids, ({"left-id"}, {"right-id"}))
        winner = next(iter(ids))
        self.assertIn("{{character:" + winner + ".full}}", self.chapter.read_text())
        self.assertIn("{{character:" + winner + "}}", self.other_chapter.read_text())

    def test_invalid_consumer_config_aborts_before_changes(self):
        (self.other.root / "mightier.toml").write_text("broken = [\n")
        before = self.snapshots()
        with self.assertRaisesRegex(BookError, "Fix its configuration"):
            self.rename()
        self.assertEqual(self.snapshots(), before)

    def test_linked_affected_files_are_not_rewritten(self):
        outside = self.base / "outside.md"
        outside.write_text(self.chapter.read_text())
        self.chapter.unlink()
        self.chapter.symlink_to(outside)
        before = self.snapshots()
        with self.assertRaises(BookError):
            self.rename()
        self.assertEqual(self.snapshots(), before)

    def test_id_aliases_are_rejected_without_changes(self):
        text = self.person.read_text().replace(
            "id: john-doe", "anchor: &identity john-doe\nid: *identity"
        )
        self.person.write_text(text)
        before = self.snapshots()
        with self.assertRaisesRegex(BookError, "aliased|alias"):
            self.rename()
        self.assertEqual(self.snapshots(), before)

    def test_default_scope_uses_the_current_series_directory(self):
        with chdir(self.base):
            renaming.rename_entity(self.project, "character", "john-doe", "jane-doe")
        self.assertIn("{{character:jane-doe}}", self.other_chapter.read_text())

    def test_external_metadata_requires_an_explicit_scope_from_a_book(self):
        with chdir(self.root), self.assertRaisesRegex(BookError, "--scope"):
            renaming.rename_entity(self.project, "character", "john-doe", "jane-doe")
        with self.assertRaisesRegex(BookError, "must contain"):
            renaming.rename_entity(
                self.project, "character", "john-doe", "jane-doe", self.root
            )

    @unittest.skipUnless(shutil.which("git"), "Git is required")
    def test_inside_a_git_book_scope_covers_the_entire_series(self):
        subprocess.run(
            ["git", "init", "-q", str(self.base)], check=True, capture_output=True
        )
        with chdir(self.root):
            renaming.rename_entity(self.project, "character", "john-doe", "jane-doe")
        self.assertIn("{{character:jane-doe}}", self.other_chapter.read_text())

    @unittest.skipUnless(shutil.which("pandoc"), "Pandoc is required")
    def test_both_books_and_live_notes_still_render_after_rename(self):
        self.rename()
        self.project.check()
        self.other.check()
        self.assertIn("John", self.project.build("html").read_text())
        self.assertIn("John", self.project.render_notes("character", "jane-doe"))
