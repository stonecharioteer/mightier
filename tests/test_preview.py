"""Real HTTP and file-watcher tests. Never expose unpublished source files."""

import json
import shutil
import threading
import time
import unittest
from http.client import HTTPConnection
from urllib.error import HTTPError
from urllib.request import urlopen

from test_books import ProjectCase

from mightier.preview import Preview, PreviewServer


@unittest.skipUnless(shutil.which("pandoc"), "Pandoc is required")
class PreviewCase(ProjectCase):
    def setUp(self):
        super().setUp()
        self.sample()
        self.preview = Preview(self.project)
        self.assertTrue(self.preview.rebuild())
        self.server = PreviewServer(self.preview, port=0)
        self.http_thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )
        self.http_thread.start()
        self.stop = threading.Event()
        self.watcher = None
        self.addCleanup(self.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def shutdown(self):
        self.stop.set()
        if self.watcher:
            self.watcher.join(timeout=10)
        self.server.shutdown()
        self.server.server_close()
        self.http_thread.join(timeout=5)

    def watch(self):
        initial = self.preview.snapshot()
        self.watcher = threading.Thread(
            target=self.preview.watch,
            args=(self.stop,),
            kwargs={"interval": 0.03, "initial": initial},
            daemon=True,
        )
        self.watcher.start()

    def wait_for(self, condition):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.04)
        self.fail(f"Preview did not update: {self.preview.status()}")

    def read(self, path="/"):
        with urlopen(self.url + path, timeout=5) as response:
            return response.read()


class PreviewTests(PreviewCase):
    def test_serves_only_published_page_and_known_assets(self):
        page = self.read().decode()
        self.assertIn("John", page)
        self.assertIn("/__mightier/client.js", page)
        self.assertIn("The Pen is <em>mightier</em> than the Sword.", page)
        for path in (
            "/__mightier/client.js",
            "/__mightier/logo.svg",
            "/__mightier/status",
        ):
            self.assertTrue(self.read(path))
        for path in (
            "/mightier.toml",
            "/chapters/01-arrival.md",
            "/metadata/characters/john-doe.md",
            "/../../metadata/characters/john-doe.md",
            "/%2e%2e/metadata",
            "/mightier.log",
            "/build/",
        ):
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                self.read(path)
            self.assertEqual(error.exception.code, 404)
        with urlopen(self.url, timeout=5) as response:
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    def test_head_and_host_check(self):
        connection = HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request("HEAD", "/")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.read(), b"")
        connection.close()
        connection = HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request("GET", "/", headers={"Host": "untrusted.example"})
        self.assertEqual(connection.getresponse().status, 403)
        connection.close()

    def test_chapter_changes_reload_and_build_errors_recover(self):
        self.watch()
        original = self.preview.status()["version"]
        self.chapter.write_text("# Updated chapter\n\nThe next scene.\n")
        self.wait_for(lambda: self.preview.status()["version"] != original)
        self.assertIn("The next scene.", self.read().decode())
        valid_version = self.preview.status()["version"]
        self.chapter.write_text("# Broken\n\n{{character:missing.short}}\n")
        self.wait_for(lambda: bool(self.preview.status()["error"]))
        self.assertEqual(self.preview.status()["version"], valid_version)
        self.assertIn("The next scene.", self.read().decode())
        self.chapter.write_text("# Recovered\n\nValid again.\n")
        self.wait_for(lambda: self.preview.status()["version"] != valid_version)
        self.assertEqual(self.preview.status()["error"], "")
        self.assertIn("Valid again.", self.read().decode())

    def test_shared_metadata_and_bibliography_changes_are_watched(self):
        self.project.new_reference("doe2024")
        self.chapter.write_text(
            "# Arrival\n\n{{character:john-doe.full}} read [@doe2024].\n"
        )
        self.assertTrue(self.preview.rebuild())
        self.watch()
        original = self.preview.status()["version"]
        character = self.shared / "characters/john-doe.md"
        character.write_text(
            character.read_text().replace("full: John Doe", "full: Jane Doe")
        )
        self.wait_for(lambda: self.preview.status()["version"] != original)
        self.assertIn("Jane", self.read().decode())
        original = self.preview.status()["version"]
        path = self.project.bibliography
        path.write_text(
            path.read_text().replace(
                "Replace with the reference title", "A new reference title"
            )
        )
        self.wait_for(lambda: self.preview.status()["version"] != original)
        self.assertRegex(self.read().decode(), r"A New Reference\s+Title")

    def test_config_changes_and_new_chapters_are_watched(self):
        self.watch()
        original = self.preview.status()["version"]
        second = self.root / "chapters/02-departure.md"
        second.write_text("# Departure\n\nA second scene.\n")
        self.configure(chapters=["chapters/01-arrival.md", "chapters/02-departure.md"])
        self.wait_for(
            lambda: (
                self.preview.status()["version"] != original
                and b"A second scene." in self.read()
            )
        )
        original = self.preview.status()["version"]
        (self.root / "mightier.toml").write_text("broken = [\n")
        self.wait_for(lambda: bool(self.preview.status()["error"]))
        self.assertEqual(self.preview.status()["version"], original)

    def test_chapter_commands_update_the_live_preview(self):
        from mightier.chapters import new_chapter, rename_chapter

        self.watch()
        original = self.preview.status()["version"]
        new_chapter(self.project, "02-return", "The return")
        self.wait_for(lambda: self.preview.status()["version"] != original)
        self.assertIn("The return", self.read().decode())
        original = self.preview.status()["version"]
        rename_chapter(self.project, "02-return", "02-homecoming")
        self.wait_for(lambda: self.preview.status()["version"] != original)
        self.assertEqual(self.preview.status()["error"], "")
        self.assertIn("The return", self.read().decode())

    def test_logs_and_generated_output_do_not_trigger_build_loops(self):
        before = self.preview.snapshot()
        for name in ("mightier.log", "mightier.log.1", "build/extra.html"):
            (self.root / name).write_text("Changed output.")
        self.assertEqual(self.preview.snapshot(), before)
        self.watch()
        version = self.preview.status()["version"]
        time.sleep(0.2)
        self.assertEqual(self.preview.status()["version"], version)

    def test_initial_failure_still_provides_an_error_page_and_recovers(self):
        self.chapter.write_text("# Broken\n\n{{c\n")
        self.preview.html = ""
        self.assertFalse(self.preview.rebuild())
        self.assertIn("Your preview will appear here", self.read().decode())
        self.assertFalse(json.loads(self.read("/__mightier/status"))["ready"])
        self.watch()
        self.chapter.write_text("# Recovered\n\nAn opening scene.\n")
        self.wait_for(lambda: self.preview.status()["ready"])
        self.assertIn("An opening scene.", self.read().decode())
