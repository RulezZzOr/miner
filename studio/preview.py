"""Read-only static web previews on a separate, authenticated loopback origin."""

from __future__ import annotations

import mimetypes
import os
import secrets
import threading
import urllib.parse
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath

PUBLIC_TYPES = {
    ".html", ".htm", ".css", ".js", ".mjs", ".json", ".svg", ".png", ".jpg",
    ".jpeg", ".gif", ".webp", ".avif", ".ico", ".woff", ".woff2", ".ttf",
    ".otf", ".webmanifest", ".mp4", ".webm", ".mp3", ".wav",
}
BLOCKED_NAMES = {"node_modules", "vendor", "credentials.json", "secrets.json", "package.json", "package-lock.json", "tsconfig.json"}
MAX_ASSET = 20_000_000
STARTER = Path(__file__).parent / "templates" / "web-starter.html"


def public_path(value):
    parts = PurePosixPath(value).parts
    return bool(parts) and not value.startswith("/") and "\\" not in value and all(
        p not in {"..", "."} and not p.startswith(".") and p.casefold() not in BLOCKED_NAMES
        for p in parts
    ) and PurePosixPath(value).suffix.casefold() in PUBLIC_TYPES


class PreviewManager:
    def __init__(self, studio, read_file, file_parent, problem):
        self.studio, self.read_file, self.file_parent, self.problem = studio, read_file, file_parent, problem
        self.lock = threading.RLock()
        self.session = None

    def start(self, body, studio_port, host="127.0.0.1"):
        project = body["project"]
        root = self.studio.project(project)
        entry = str(body.get("entry", "index.html")).strip()
        if not public_path(entry) or Path(entry).suffix.lower() not in {".html", ".htm"}:
            raise self.problem("Vyber HTML soubor uvnitř projektu.")
        data = self.read_file(root, entry, MAX_ASSET)
        # Source files from Vite/React need a build before this static preview.
        if b"/@vite/client" in data or b".tsx" in data or b".jsx" in data:
            raise self.problem("Tento vstup potřebuje build. Vyber vytvořený dist/index.html; náhled nespouští npm ani backend.")
        with self.lock:
            if self.session and self.session.project == project and self.session.entry == entry:
                return self.session.status()
            if self.session:
                raise self.problem("Nejdřív zastav současný náhled. Může být otevřený v jiné záložce.", 409)
            session = PreviewSession(self, root, project, entry, studio_port, host)
            self.session = session
            return session.status()

    def status(self):
        with self.lock:
            return self.session.status() if self.session else {"running": False}

    def stop(self, expected=None):
        with self.lock:
            if self.session and expected is not None and self.session.id != expected:
                raise self.problem("Náhled se mezitím změnil. Obnov jeho stav.", 409)
            session, self.session = self.session, None
            if session:
                session.close()
        return {"running": False}

    def install(self, body):
        folder = str(body.get("folder", "web-preview")).strip()
        # One new folder only: no accidental overwrite of an existing website.
        if not folder or len(folder) > 80 or not all(c.isascii() and (c.isalnum() or c in "-_") for c in folder):
            raise self.problem("Název složky: písmena bez diakritiky, čísla, pomlčka nebo podtržítko.")
        root = self.studio.project(body["project"])
        with self.file_parent(root, "", directory=True) as (parent, _):
            try:
                os.mkdir(folder, dir_fd=parent)
            except FileExistsError:
                raise self.problem("Složka už existuje. Zvol jiný název; nic se nepřepsalo.", 409) from None
        entry = f"{folder}/index.html"
        self.studio.save_file({"project": body["project"], "path": entry,
                               "content": STARTER.read_text(encoding="utf-8"), "revision": None})
        return {"project": body["project"], "entry": entry}


class PreviewSession:
    def __init__(self, manager, root, project, entry, studio_port, host="127.0.0.1"):
        self.manager, self.root, self.project, self.entry = manager, root, project, entry
        self.directory = str(PurePosixPath(entry).parent)
        self.id = secrets.token_hex(12)
        self.token = secrets.token_urlsafe(32)
        self.cookie_name = "studio_preview_" + self.id
        self.stamps = {entry: self.stamp(entry)}
        self.revision = 0
        self.paths_lock = threading.Lock()
        self.studio_port = studio_port
        self.host = host
        self.server = ThreadingHTTPServer((host, 0), PreviewHandler)
        self.server.daemon_threads = True
        self.server.session = self
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .1}, daemon=True)
        self.thread.start()

    def stamp(self, path):
        try:
            with self.manager.file_parent(self.root, path) as (parent, name):
                st = os.stat(name, dir_fd=parent, follow_symlinks=False)
                return st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns
        except (OSError, self.manager.problem):
            return None

    def track(self, path):
        with self.paths_lock:
            if len(self.stamps) < 2000:
                self.stamps.setdefault(path, self.stamp(path))

    def status(self):
        with self.paths_lock:
            for path, old in list(self.stamps.items()):
                current = self.stamp(path)
                if current != old:
                    self.revision += 1
                    self.stamps[path] = current
            revision = self.revision
        route = urllib.parse.quote(PurePosixPath(self.entry).name)
        return {"running": True, "id": self.id, "project": self.project, "entry": self.entry,
                "url": f"http://{self.host}:{self.server.server_port}/{route}?__studio_preview={self.token}",
                "revision": revision}

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


class PreviewHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, status, data=b"", content_type="text/plain; charset=utf-8", headers=()):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
            "object-src 'none'; frame-src 'none'; worker-src 'none'; base-uri 'self'; form-action 'self'; "
            "sandbox allow-scripts allow-same-origin allow-forms; "
            f"frame-ancestors http://127.0.0.1:{self.server.session.studio_port} http://localhost:{self.server.session.studio_port} "
            f"http://{self.server.session.host}:{self.server.session.studio_port}")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        session = self.server.session
        try:
            if self.headers.get("Host") not in {f"{session.host}:{self.server.server_port}", f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}:
                return self.respond(403, b"Forbidden host")
            parsed = urllib.parse.urlsplit(self.path)
            decoded = urllib.parse.unquote(parsed.path)
            if not decoded.startswith("/") or decoded.startswith("//") or "\\" in decoded:
                return self.respond(400, b"Invalid path")
            query = urllib.parse.parse_qs(parsed.query)
            token = query.get("__studio_preview", [""])[0]
            if token and token.isascii() and secrets.compare_digest(token, session.token):
                # HttpOnly cookie permits absolute asset URLs without exposing the
                # Studio token or putting credentials in scripts / referrers.
                query.pop("__studio_preview", None)
                location = parsed.path + ("?" + urllib.parse.urlencode(query, doseq=True) if query else "")
                if location.startswith("//"):
                    return self.respond(400, b"Invalid path")
                return self.respond(303, headers=[("Location", location),
                    ("Set-Cookie", f"{session.cookie_name}={session.token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800")])
            cookie = SimpleCookie()
            cookie.load(self.headers.get("Cookie", ""))
            value = cookie.get(session.cookie_name)
            if not value or not value.value.isascii() or not secrets.compare_digest(value.value, session.token):
                return self.respond(403, b"Open this preview from Switch Studio.")
            path = urllib.parse.unquote(parsed.path).lstrip("/")
            if not path or path.endswith("/"):
                path += "index.html"
            if not public_path(path):
                return self.respond(403, b"Only public web assets are available.")
            relative = str(PurePosixPath(session.directory) / path)
            session.track(relative)
            data = session.manager.read_file(session.root, relative, MAX_ASSET)
            mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
            if Path(path).suffix in {".js", ".mjs"}:
                mime = "text/javascript"
            self.respond(200, data, mime)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except session.manager.problem as exc:
            self.respond(exc.status, b"Asset unavailable. Check the file in Studio.")
        except (OSError, ValueError, CookieError):
            self.respond(404, b"Asset unavailable.")
