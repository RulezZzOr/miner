"""Real HTTP preview lifecycle, asset isolation, and create-only scaffolding."""

import http.cookiejar
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from studio.preview import PreviewHandler
from studio.server import Handler, Problem, Studio


class PreviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.web = self.project / "web"
        self.web.mkdir()
        (self.web / "index.html").write_text('<h1>Preview</h1><script src="/app.js"></script>')
        (self.web / "app.js").write_text("document.title = 'Working';")
        self.studio = Studio(self.project, self.root / "state")
        self.pid = next(iter(self.studio.projects))
        self.addCleanup(self.studio.preview.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.studio = self.studio
        worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def post(self, path, body, token=True):
        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + (self.root / "state/access-key").read_text().strip()}
        if token:
            headers["X-Studio-Token"] = self.studio.token
        req = urllib.request.Request(self.base + path, json.dumps(body).encode(), headers)
        with urllib.request.urlopen(req, timeout=3) as response:
            return json.load(response)

    def start(self):
        session = self.post("/api/preview/start", {"project": self.pid, "entry": "web/index.html"})
        self.url = session["url"]
        self.origin = self.url.split("/index.html")[0]
        return session

    def assert_http_error(self, url, status, opener=None):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            (opener or self.browser).open(url, timeout=3)
        self.assertEqual(caught.exception.code, status)

    def test_start_load_absolute_assets_change_and_stop(self):
        session = self.start()
        self.assertNotEqual(urllib.parse.urlsplit(self.url).port, self.server.server_port)
        with self.browser.open(self.url) as response:
            self.assertIn(b"<h1>Preview</h1>", response.read())
            self.assertNotIn("__studio_preview", response.url)
            self.assertIn("worker-src 'none'", response.headers["Content-Security-Policy"])
            self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))
        with self.browser.open(self.origin + "/app.js") as response:
            self.assertIn("javascript", response.headers["Content-Type"])
            self.assertIn(b"Working", response.read())
        self.assertEqual(session["revision"], self.studio.preview.status()["revision"],
                         "Loading another asset must not reset an interactive page")
        before = self.studio.preview.status()["revision"]
        (self.web / "app.js").write_text("document.title = 'Updated';")
        self.assertNotEqual(before, self.studio.preview.status()["revision"])
        with self.browser.open(self.origin + "/app.js") as response:
            self.assertIn(b"Updated", response.read())
        self.post("/api/preview/stop", {"id": session["id"]})
        self.assertFalse(self.studio.preview.status()["running"])
        with self.assertRaises(urllib.error.URLError):
            self.browser.open(self.origin + "/index.html", timeout=2)

    def test_auth_host_and_cross_origin_boundaries(self):
        self.start()
        self.assert_http_error(self.origin + "/index.html", 403, urllib.request.build_opener())
        self.assert_http_error(self.url, 403, urllib.request.build_opener(HostOverride()))
        with self.browser.open(self.url):
            pass
        self.assert_http_error(self.origin + "/index.html?__studio_preview=%C4%8D", 403, urllib.request.build_opener())
        request = urllib.request.Request(self.base + "/api/state", headers={"Origin": self.origin})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/preview/stop", {"id": self.studio.preview.status()["id"]}, token=False)
        self.assertEqual(caught.exception.code, 403)

    def test_traversal_symlinks_hidden_and_backend_files_are_not_served(self):
        self.start()
        with self.browser.open(self.url):
            pass
        (self.project / "outside.html").write_text("private")
        (self.web / "link.html").symlink_to(self.project / "outside.html")
        for name in [".env", ".ENV.json", "server.py", "credentials.json", "package.json"]:
            (self.web / name).write_text("private")
        for route in ["/.env", "/.ENV.json", "/server.py", "/credentials.json", "/package.json", "/%2e%2e/outside.html"]:
            self.assert_http_error(self.origin + route, 403)
        self.assert_http_error(self.origin + "/link.html", 403)
        self.assert_http_error(self.origin + "/%5cexample.com", 400)

    def test_root_directory_replaced_with_symlink_is_denied(self):
        self.start()
        with self.browser.open(self.url):
            pass
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "index.html").write_text("PRIVATE")
        self.web.rename(self.project / "old-web")
        self.web.symlink_to(outside, target_is_directory=True)
        self.assert_http_error(self.origin + "/index.html", 403)

    def test_start_validation_preserves_existing_preview_and_stale_stop_is_rejected(self):
        session = self.start()
        self.assertEqual(self.start()["id"], session["id"])
        for entry in ["../outside.html", "web/.env", "/tmp/test.html", "web/missing.html"]:
            with self.assertRaises(urllib.error.HTTPError):
                self.post("/api/preview/start", {"project": self.pid, "entry": entry})
        (self.web / "other.html").write_text("Other")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/preview/start", {"project": self.pid, "entry": "web/other.html"})
        self.assertEqual(caught.exception.code, 409)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/preview/stop", {"id": "old-session"})
        self.assertEqual(caught.exception.code, 409)
        with self.assertRaises(urllib.error.HTTPError):
            self.post("/api/preview/stop", {"id": None})
        self.assertEqual(self.studio.preview.status()["id"], session["id"])

    def test_newly_created_previously_missing_asset_triggers_reload(self):
        self.start()
        with self.browser.open(self.url):
            pass
        self.assert_http_error(self.origin + "/later.css", 404)
        revision = self.studio.preview.status()["revision"]
        (self.web / "later.css").write_text("body {color: green}")
        self.assertNotEqual(revision, self.studio.preview.status()["revision"])

    def test_starter_is_real_saved_html_and_never_overwrites(self):
        result = self.post("/api/preview/starter", {"project": self.pid, "folder": "my-app"})
        target = self.project / result["entry"]
        self.assertIn("ideas-form", target.read_text())
        self.assertFalse(self.studio.preview.status()["running"])
        target.write_text("MY EDIT")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/preview/starter", {"project": self.pid, "folder": "my-app"})
        self.assertEqual(caught.exception.code, 409)
        self.assertEqual(target.read_text(), "MY EDIT")
        for folder in ["../outside", ".env", "a/b", ""]:
            with self.assertRaises(urllib.error.HTTPError):
                self.post("/api/preview/starter", {"project": self.pid, "folder": folder})

    def test_preview_uses_the_studio_host_name_the_owner_opened(self):
        headers = {"Content-Type": "application/json", "Host": f"localhost:{self.server.server_port}",
                   "Authorization": "Bearer " + (self.root / "state/access-key").read_text().strip(),
                   "X-Studio-Token": self.studio.token}
        body = json.dumps({"project": self.pid, "entry": "web/index.html"}).encode()
        with urllib.request.urlopen(urllib.request.Request(self.base + "/api/preview/start", body, headers), timeout=3) as response:
            session = json.load(response)
        # SameSite=Strict preview cookies need the same site as the Studio page (localhost here).
        self.assertTrue(session["url"].startswith("http://localhost:"), session["url"])
        with self.browser.open(session["url"], timeout=3) as response:
            self.assertIn(b"<h1>Preview</h1>", response.read())
        self.assertTrue(self.studio.preview.status()["url"].startswith("http://127.0.0.1:"))

    def test_large_assets_are_sent_in_slices_and_a_late_failure_only_closes(self):
        asset = bytes(range(256)) * 4096  # 1 MB, so sixteen 64 KB slices.
        (self.web / "big.js").write_bytes(asset)
        self.start()
        self.browser.open(self.url).close()
        with patch("studio.tls_server.WRITE_SLICE", 65536), self.browser.open(self.origin + "/big.js") as response:
            self.assertEqual(response.read(), asset)
        handler = PreviewHandler.__new__(PreviewHandler)
        handler.response_started, handler.wfile = True, io.BytesIO()
        handler.respond(404, b"Asset unavailable.")  # The body had already started.
        self.assertTrue(handler.close_connection)
        self.assertEqual(handler.wfile.getvalue(), b"")

    def test_source_requiring_a_build_is_explained(self):
        (self.web / "index.html").write_text('<script type="module" src="/src/main.tsx"></script>')
        with self.assertRaises(Problem) as caught:
            self.studio.preview.start({"project": self.pid, "entry": "web/index.html"}, self.server.server_port)
        self.assertIn("dist/index.html", str(caught.exception))
        self.assertFalse(self.studio.preview.status()["running"])


class HostOverride(urllib.request.BaseHandler):
    def http_request(self, req):
        req.add_header("Host", "attacker.example")
        return req


if __name__ == "__main__":
    unittest.main()
