"""Resolve references in author notes without exporting their private text."""

import json
import shutil
import unittest
import zipfile
from urllib.error import HTTPError

from test_books import ProjectCase
from test_preview import PreviewCase

from mightier.books import BookError, read_entity, read_entity_document


@unittest.skipUnless(shutil.which("pandoc"), "Pandoc is required")
class NoteRenderingTests(ProjectCase):
    def setUp(self):
        super().setUp()
        self.sample()
        self.person = self.shared / "characters/john-doe.md"
        self.place = self.shared / "places/greyhaven.md"
        self.person.write_text(
            self.person.read_text()
            + "\nPRIVATE_PERSON_NOTE. Visits {{place:greyhaven}}. Knows {{character:john-doe.full}}.\n"
        )
        self.place.write_text(
            self.place.read_text()
            + "\nPRIVATE_PLACE_NOTE. Home of {{character:john-doe}}.\n"
        )

    def test_bare_tags_and_explicit_forms_render_in_book_prose(self):
        self.chapter.write_text(
            "# Names\n\n{{character:john-doe}} met {{character:john-doe.full}} in {{place:greyhaven}}.\n"
        )
        html = self.project.build("html").read_text()
        self.assertIn('data-form="short">John</span>', html)
        self.assertIn('data-form="full">John Doe</span>', html)
        self.assertIn('data-form="name">Greyhaven</span>', html)
        self.assertNotIn("{{", html)

    def test_notes_support_references_without_recursively_inlining_other_notes(self):
        person = self.project.render_notes("character", "john-doe")
        place = self.project.render_notes("place", "greyhaven")
        self.assertIn("PRIVATE_PERSON_NOTE", person)
        self.assertIn('data-entity="place:greyhaven"', person)
        self.assertIn("Greyhaven", person)
        self.assertIn("PRIVATE_PLACE_NOTE", place)
        self.assertIn('data-form="short">John</span>', place)
        self.assertNotIn("PRIVATE_PLACE_NOTE", person)
        self.assertNotIn("PRIVATE_PERSON_NOTE", place)
        self.assertNotIn("{{character:", person + place)
        self.assertFalse((self.root / "build").exists())

    def test_note_body_is_explicitly_separate_from_metadata(self):
        header = read_entity("character", self.person)
        _, body = read_entity_document("character", self.person)
        self.assertNotIn("PRIVATE_PERSON_NOTE", json.dumps(header))
        self.assertIn("PRIVATE_PERSON_NOTE", body)

    def test_notes_stay_out_of_html_and_epub_exports(self):
        self.project.check()
        html = self.project.build("html").read_text()
        with zipfile.ZipFile(self.project.build()) as archive:
            text = "\n".join(
                archive.read(name).decode()
                for name in archive.namelist()
                if name.endswith((".xhtml", ".opf"))
            )
        for output in (html, text):
            self.assertNotIn("PRIVATE_PERSON_NOTE", output)
            self.assertNotIn("PRIVATE_PLACE_NOTE", output)
            self.assertNotIn("/__mightier/notes/", output)
            self.assertNotIn("/__mightier/client.js", output)

    def test_check_validates_notes_without_making_them_part_of_builds(self):
        self.person.write_text(self.person.read_text() + "\n{{character:missing}}\n")
        with self.assertRaisesRegex(BookError, "unknown entity character:missing"):
            self.project.check()
        self.assertTrue(self.project.build("html").is_file())

    def test_code_in_notes_keeps_literal_tag_examples(self):
        self.person.write_text(self.person.read_text() + "\n`{{character:missing}}`\n")
        self.assertIn(
            "{{character:missing}}", self.project.render_notes("character", "john-doe")
        )


@unittest.skipUnless(shutil.which("pandoc"), "Pandoc is required")
class NotesPreviewTests(PreviewCase):
    def test_live_notes_routes_render_nested_references(self):
        person = self.shared / "characters/john-doe.md"
        place = self.shared / "places/greyhaven.md"
        person.write_text(
            person.read_text() + "\nPRIVATE_PERSON_NOTE. Visits {{place:greyhaven}}.\n"
        )
        place.write_text(
            place.read_text() + "\nPRIVATE_PLACE_NOTE. Knows {{character:john-doe}}.\n"
        )
        html = self.read("/__mightier/notes/character/john-doe").decode()
        self.assertIn("PRIVATE_PERSON_NOTE", html)
        self.assertIn("Greyhaven", html)
        self.assertIn('data-view="notes"', html)
        self.assertIn("Back to book", html)
        self.assertIn("/__mightier/client.js", html)
        self.assertNotIn("PRIVATE_PLACE_NOTE", html)
        place_html = self.read("/__mightier/notes/place/greyhaven").decode()
        self.assertIn("PRIVATE_PLACE_NOTE", place_html)
        self.assertIn('data-form="short">John</span>', place_html)
        self.assertNotIn("PRIVATE_PERSON_NOTE", self.read().decode())
        for path in (
            "/__mightier/notes/character/unknown",
            "/__mightier/notes/place/../../private",
            "/__mightier/notes/file/john-doe",
        ):
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                self.read(path)
            self.assertEqual(error.exception.code, 404)

    def test_note_pages_update_even_when_the_book_has_a_build_error(self):
        self.watch()
        version = self.preview.status()["version"]
        self.chapter.write_text("# Broken\n\n{{character:missing}}\n")
        self.wait_for(lambda: bool(self.preview.status()["error"]))
        self.assertEqual(self.preview.status()["version"], version)
        before = self.preview.status()["notes_version"]
        person = self.shared / "characters/john-doe.md"
        person.write_text(
            person.read_text() + "\nNew private note while the draft is invalid.\n"
        )
        self.wait_for(lambda: self.preview.status()["notes_version"] != before)
        self.assertIn(
            "New private note",
            self.read("/__mightier/notes/character/john-doe").decode(),
        )
        self.assertEqual(self.preview.status()["version"], version)

    def test_invalid_note_has_an_error_page_and_can_recover(self):
        person = self.shared / "characters/john-doe.md"
        original = person.read_text()
        person.write_text(original + "\n{{character:missing}}\n")
        with self.assertRaises(HTTPError) as error:
            self.read("/__mightier/notes/character/john-doe")
        self.assertEqual(error.exception.code, 422)
        error_page = error.exception.read().decode()
        self.assertIn("Notes could not be rendered", error_page)
        self.assertIn("/__mightier/client.js", error_page)
        self.assertIn("&lt;script&gt;", self.preview.error_page("<script>").decode())
        self.watch()
        version = self.preview.status()["notes_version"]
        person.write_text(original + "\nFixed notes.\n")
        self.wait_for(lambda: self.preview.status()["notes_version"] != version)
        self.assertIn(
            "Fixed notes.", self.read("/__mightier/notes/character/john-doe").decode()
        )
