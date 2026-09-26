"""Regressions for the seven September audit findings; no live model calls."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import psutil

from studio.server import Problem, Studio, digest, read_project_file, write_config


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.config = self.root / "agent.toml"
        write_config(
            self.config,
            {
                "local": {
                    "model": "fixture",
                    "protocol": "chat_completions",
                    "auth": "none",
                    "base_url": "http://127.0.0.1:1/v1",
                }
            },
            "local",
        )
        self.studio = Studio(self.project, self.root / "state", self.config)
        self.addCleanup(self.studio.oauth.close)
        self.project_id = next(iter(self.studio.projects))

    def run_fixture(self, name, **kwargs):
        directory = self.studio.data / "runs" / name
        directory.mkdir(parents=True)
        self.studio.runs[name] = {
            "id": name,
            "project": self.project_id,
            "created": time.time(),
            "status": "running",
            **kwargs,
        }
        return directory

    def test_stop_waits_for_stubborn_descendants_in_separate_sessions(self):
        for escaped in (False, True):
            with self.subTest(escaped=escaped):
                pidfile = self.root / f"child-{escaped}.pid"
                child = (
                    "import os,signal,time;from pathlib import Path;"
                    "signal.signal(signal.SIGINT,signal.SIG_IGN);"
                    f"Path({str(pidfile)!r}).write_text(str(os.getpid()));time.sleep(60)"
                )
                parent = (
                    "import subprocess,sys,time;"
                    f'subprocess.Popen([sys.executable,"-c",{child!r}],start_new_session={escaped});'
                    "time.sleep(60)"
                )
                process = subprocess.Popen(
                    [sys.executable, "-c", parent],
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                child_process = None
                try:
                    deadline = time.monotonic() + 5
                    while not pidfile.exists() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertTrue(pidfile.exists())
                    child_process = psutil.Process(int(pidfile.read_text()))
                    run_id = f"stop-{escaped}"
                    self.run_fixture(run_id)
                    self.studio.processes[run_id] = process
                    tree = self.studio.process_tree(process)
                    tree.grace = 0.3
                    watcher = threading.Thread(target=self.studio.watch, args=(run_id, process))
                    watcher.start()
                    self.studio.stop(run_id)
                    process.wait(timeout=3)
                    self.assertEqual(self.studio.runs[run_id]["status"], "stopping")
                    watcher.join(5)
                    self.assertFalse(watcher.is_alive())
                    self.assertFalse(tree.alive(child_process))
                    self.assertEqual(self.studio.runs[run_id]["status"], "cancelled")
                    self.assertNotIn(run_id, self.studio.processes)
                finally:
                    for p in (child_process,):
                        if p:
                            try:
                                p.kill()
                            except psutil.NoSuchProcess:
                                pass
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)

    def test_probe_after_changing_env_profile_to_no_auth(self):
        received = []

        class Capture(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                received.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"data":[{"id":"fixture"}]}')

        http = ThreadingHTTPServer(("127.0.0.1", 0), Capture)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        try:
            self.studio.save_model(
                {
                    "id": "local",
                    "base_url": f"http://127.0.0.1:{http.server_port}/v1",
                    "auth": "env",
                    "api_key_env": "STUDIO_FAKE_API_KEY",
                }
            )
            with patch.dict(os.environ, {"STUDIO_FAKE_API_KEY": "SYNTHETIC_ONLY"}):
                self.studio.probe("local")
                self.studio.save_model({"id": "local", "auth": "none"})
                self.studio.probe("local")
            self.assertEqual(received, ["Bearer SYNTHETIC_ONLY", "Bearer ollama"])
        finally:
            http.shutdown()
            http.server_close()

    def test_protected_paths_block_read_write_download_and_listing(self):
        secret = self.project / ".switch-agent"
        secret.mkdir()
        (secret / "secret.txt").write_text("fixture")
        (self.project / "alias").symlink_to(secret, target_is_directory=True)
        (self.project / ".env").write_text("fixture")
        for path in ("alias/secret.txt", ".SWITCH-AGENT/secret.txt", ".ENV", ".git/config"):
            with self.subTest(path=path):
                with self.assertRaises(Problem):
                    self.studio.read_file(self.project_id, path)
                with self.assertRaises(Problem):
                    read_project_file(self.project, path, 50_000_000)
                with self.assertRaises(Problem):
                    self.studio.save_file(
                        {
                            "project": self.project_id,
                            "path": path,
                            "content": "overwrite",
                            "revision": digest(b"fixture"),
                        }
                    )
        with self.assertRaises(Problem):
            self.studio.tree(self.project_id, "alias")
        self.assertEqual(self.studio.tree(self.project_id), [])
        self.assertEqual((secret / "secret.txt").read_text(), "fixture")

    def test_parent_swapped_to_symlink_after_validation_is_rejected(self):
        for operation in ("read", "write", "download"):
            with self.subTest(operation=operation):
                folder = self.project / operation
                folder.mkdir()
                (folder / "file.txt").write_text("public")
                secret = self.root / (operation + "-secret")
                secret.mkdir()
                (secret / "file.txt").write_text("private")
                actual_open = os.open
                swapped = False

                def racing_open(path, flags, *args, **kwargs):
                    nonlocal swapped
                    if path == operation and kwargs.get("dir_fd") is not None and not swapped:
                        swapped = True
                        folder.rename(folder.with_name(operation + "-old"))
                        folder.symlink_to(secret, target_is_directory=True)
                    return actual_open(path, flags, *args, **kwargs)

                with (
                    patch("studio.server.os.open", side_effect=racing_open),
                    self.assertRaises(Problem),
                ):
                    relative = operation + "/file.txt"
                    if operation == "write":
                        self.studio.save_file(
                            {
                                "project": self.project_id,
                                "path": relative,
                                "content": "overwrite",
                                "revision": digest(b"public"),
                            }
                        )
                    else:
                        read_project_file(self.project, relative, 2_000_000)
                self.assertTrue(swapped)
                self.assertEqual((secret / "file.txt").read_text(), "private")

    def test_codex_artifacts_include_late_pages_and_skip_unsafe_paths(self):
        directory = self.run_fixture("late", backend="codex", status="completed")
        (self.project / "late.txt").write_text("artifact")
        (self.project / ".env").write_text("private")
        events = [{"type": "content", "text": "chunk"}] * 1201 + [
            {
                "type": "changed_files",
                "paths": ["late.txt", "late.txt", ".env", "../outside", "deleted.txt"],
            }
        ]
        (directory / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
        self.assertEqual([f["path"] for f in self.studio.artifacts("late")], ["late.txt"])
        self.assertEqual(len(self.studio.events("late")["events"]), 600)
        restored = Studio(self.project, self.studio.data, self.config)
        restored.runs = self.studio.runs
        try:
            self.assertEqual([f["path"] for f in restored.artifacts("late")], ["late.txt"])
        finally:
            restored.oauth.close()


if __name__ == "__main__":
    unittest.main()
