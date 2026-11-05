"""A loopback-only preview server. Never serve the manuscript directory."""

import hashlib
import json
import re
import secrets
import threading
import webbrowser
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import urlsplit

from mightier.books import CONFIG, ENTITY_FOLDERS, BookError
from mightier.logging_config import logger
from mightier.output import console, failure, success

ASSETS = files("mightier").joinpath("assets")


class Preview:
    def __init__(self, project):
        self.project = project
        self.metadata = project.metadata_dir
        self.bibliography = project.bibliography
        self.lock = threading.Lock()
        self.html = ""
        self.version = ""
        self.notes_version = secrets.token_hex(8)
        self.error = ""
        self.key = hashlib.sha256(str(project.root).encode()).hexdigest()[:16]

    def status(self):
        with self.lock:
            return {
                "version": self.version,
                "notes_version": self.notes_version,
                "ready": bool(self.html),
                "error": self.error,
            }

    def rebuild(self):
        logger.debug("Rebuilding live preview for %s", self.project.root)
        with self.lock:
            self.notes_version = secrets.token_hex(8)
        try:
            output = self.project.build("html", announce=False)
            html = output.read_text(encoding="utf-8")
        except (BookError, OSError, UnicodeError) as error:
            with self.lock:
                self.error = str(error)
            failure(str(error))
            return False
        with self.lock:
            self.html = html
            self.version = secrets.token_hex(8)
            self.error = ""
        success("Preview updated.")
        return True

    def page(self):
        with self.lock:
            html, version = self.html, self.version
        if not html:
            css = ASSETS.joinpath("reader.css").read_text(encoding="utf-8")
            html = (
                '<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                f"<title>Mightier preview</title><style>{css}</style></head>"
                "<body><h1>Your preview will appear here.</h1>"
                "<p>Fix the reported build error and save the file. Mightier will try again.</p></body></html>"
            )
        return self.decorate(html, version)

    def notes_page(self, kind, entity_id):
        with self.lock:
            version = self.notes_version
        return self.decorate(
            self.project.render_notes(kind, entity_id), version, notes=True
        )

    def error_page(self, message):
        css = ASSETS.joinpath("reader.css").read_text(encoding="utf-8")
        html = (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>Notes error</title><style>{css}</style></head><body>"
            "<h1>Notes could not be rendered.</h1>"
            f"<pre>{escape(message)}</pre><p>Correct the source and save it to try again.</p></body></html>"
        )
        with self.lock:
            version = self.notes_version
        return self.decorate(html, version, notes=True)

    def decorate(self, html, version, notes=False):
        view = "notes" if notes else "book"
        back = '<a class="mightier-back" href="/">Back to book</a>' if notes else ""
        panel = (
            f'<aside id="mightier-live" class="mightier-live" data-state="ready" '
            f'data-version="{version}" data-project="{self.key}" data-view="{view}">'
            '<div class="mightier-live-row"><a class="mightier-brand" href="/">'
            '<img src="/__mightier/logo.svg" width="28" height="28" alt=""> Mightier</a>'
            f"{back}"
            '<span class="mightier-motto">The Pen is <em>mightier</em> than the Sword.</span>'
            '<span id="mightier-status" role="status" aria-live="polite">Live preview</span></div>'
            '<pre id="mightier-error" role="alert" hidden></pre></aside>'
        )
        body = re.search(r"<body\b[^>]*>", html, flags=re.IGNORECASE)
        if body:
            html = html[: body.end()] + panel + html[body.end() :]
        else:
            html = panel + html
        script = '<script src="/__mightier/client.js" defer></script>'
        return re.sub(
            r"</body>", script + "</body>", html, count=1, flags=re.IGNORECASE
        ).encode("utf-8")

    def snapshot(self):
        # Keep watching the last valid metadata path while TOML is being edited.
        try:
            self.metadata = self.project.metadata_dir
            self.bibliography = self.project.bibliography
        except BookError, OSError, UnicodeError:
            pass
        paths = {self.project.root / CONFIG, self.bibliography}
        for directory, directories, filenames in self.project.root.walk():
            directories[:] = [
                name
                for name in directories
                if not name.startswith(".") and name != "build"
            ]
            for name in filenames:
                if name.startswith(".") or name in ("mightier.log", "mightier.log.1"):
                    continue
                if not name.endswith(("~", ".swp", ".tmp")):
                    paths.add(directory / name)
        for folder in ("characters", "places"):
            paths.update((self.metadata / folder).glob("*.md"))
        signature = []
        for path in sorted(paths):
            try:
                stat = path.stat()
            except OSError:
                continue
            signature.append((str(path), stat.st_mtime_ns, stat.st_size))
        return tuple(signature)

    def watch(self, stop, interval=0.25, initial=None):
        previous = self.snapshot() if initial is None else initial
        while not stop.wait(interval):
            current = self.snapshot()
            if current == previous:
                continue
            # Require one quiet interval so a multi-file save causes one build.
            while not stop.wait(interval):
                latest = self.snapshot()
                if latest == current:
                    break
                current = latest
            if stop.is_set():
                return
            self.rebuild()
            previous = current


class PreviewHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.respond()

    def do_HEAD(self):
        self.respond(head=True)

    def respond(self, head=False):
        try:
            host = urlsplit("http://" + self.headers.get("Host", "")).hostname
        except ValueError:
            host = None
        if host not in ("localhost", "127.0.0.1", "::1"):
            self.send_error(403, "Use a loopback address.")
            return
        path = urlsplit(self.path).path
        preview = self.server.preview
        status = 200
        notes_route = re.fullmatch(
            r"/__mightier/notes/(character|place)/([a-z0-9]+(?:-[a-z0-9]+)*)", path
        )
        if path in ("/", "/index.html"):
            content, media = preview.page(), "text/html; charset=utf-8"
        elif notes_route:
            kind, entity_id = notes_route.groups()
            try:
                metadata_file = (
                    preview.project.metadata_dir
                    / ENTITY_FOLDERS[kind]
                    / f"{entity_id}.md"
                )
                if not metadata_file.is_file():
                    self.send_error(404)
                    return
                content = preview.notes_page(kind, entity_id)
            except (BookError, OSError, UnicodeError) as error:
                logger.exception(
                    "Could not render author notes for %s:%s", kind, entity_id
                )
                content = preview.error_page(str(error))
                status = 422
            media = "text/html; charset=utf-8"
        elif path == "/__mightier/status":
            content = json.dumps(preview.status()).encode("utf-8")
            media = "application/json; charset=utf-8"
        elif path == "/__mightier/client.js":
            content, media = (
                ASSETS.joinpath("preview.js").read_bytes(),
                "text/javascript; charset=utf-8",
            )
        elif path == "/__mightier/logo.svg":
            content, media = (
                ASSETS.joinpath("mightier.svg").read_bytes(),
                "image/svg+xml",
            )
        else:
            self.send_error(404)
            return
        self.send_response(status)
        self.send_header("Content-Type", media)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head:
            try:
                self.wfile.write(content)
            except BrokenPipeError, ConnectionResetError:
                pass

    def log_message(self, format, *args):
        logger.debug("Preview HTTP: " + format, *args)


class PreviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, preview, port=8000):
        self.preview = preview
        super().__init__(("127.0.0.1", port), PreviewHandler)


def serve(project, port=8000, open_browser=False):
    if not 0 <= port <= 65535:
        raise BookError("Port must be between 0 and 65535.")
    preview = Preview(project)
    with PreviewServer(preview, port) as server:
        # Take the baseline before the initial build so edits during it are seen.
        stop = threading.Event()
        initial = preview.snapshot()
        watcher = threading.Thread(
            target=preview.watch, args=(stop,), kwargs={"initial": initial}, daemon=True
        )
        with console.status("Building HTML preview..."):
            preview.rebuild()
        url = f"http://127.0.0.1:{server.server_port}/"
        console.print("Live preview", style="bold cyan")
        console.print(url, markup=False)
        console.print(
            "Save a chapter, metadata record, or mightier.toml to update it. Ctrl-C stops the server.",
            style="dim",
        )
        watcher.start()
        if open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever(poll_interval=0.2)
        finally:
            stop.set()
            watcher.join(timeout=2)
