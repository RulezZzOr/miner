"""Behavioral tests for file conflicts, HTTP boundary, and the real runner bridge."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from studio.server import Handler, Problem, Studio, write_config


class ModelHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(data)
        if self.server.fail:
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                b'{"error":{"message":"test invalid API key","type":"authentication_error"}}'
            )
            return
        if self.server.block:
            self.server.release.wait(20)
        messages = data.get("messages", [])
        has_result = any(m.get("role") == "tool" for m in messages)
        if has_result:
            delta = {"role": "assistant", "content": "Soubor byl vytvořen. STUDIO_TEST_OK"}
            finish = "stop"
        else:
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_studio",
                        "type": "function",
                        "function": {
                            "name": getattr(self.server, "tool_name", "create_file"),
                            "arguments": json.dumps(
                                getattr(self.server, "tool_args",
                                        {"path": "/outputs/studio-check.txt", "content": "STUDIO_TEST_OK"})
                            ),
                        },
                    }
                ],
            }
            finish = "tool_calls"
        if data.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for d, f in [(delta, None), ({}, finish)]:
                payload = {
                    "id": "chatcmpl-test",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": "test",
                    "choices": [{"index": 0, "delta": d, "finish_reason": f}],
                }
                self.wfile.write(("data: " + json.dumps(payload) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "id": "chatcmpl-test",
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": "test",
                        "choices": [{"index": 0, "message": delta, "finish_reason": finish}],
                        "usage": {
                            "prompt_tokens": 100,
                            "completion_tokens": 20,
                            "total_tokens": 120,
                        },
                    }
                ).encode()
            )


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.config = self.root / "agent.toml"
        write_config(
            self.config,
            {
                "test": {
                    "model": "test",
                    "protocol": "chat_completions",
                    "chat_dialect": "openai",
                    "base_url": "http://127.0.0.1:1/v1",
                    "auth": "none",
                    "context_window": 32768,
                    "max_output_tokens": 4096,
                }
            },
            "test",
        )
        self.studio = Studio(self.project, self.root / "state", self.config)
        self.pid = next(iter(self.studio.projects))

    def tearDown(self):
        for run_id in list(self.studio.processes):
            self.studio.stop(run_id)
        for process in list(self.studio.processes.values()):
            self.studio.kill_later(process)
        self.temp.cleanup()

    def test_file_conflict_and_escape(self):
        (self.project / "a.txt").write_text("original")
        f = self.studio.read_file(self.pid, "a.txt")
        (self.project / "a.txt").write_text("agent edit")
        with self.assertRaises(Problem) as ctx:
            self.studio.save_file(
                {
                    "project": self.pid,
                    "path": "a.txt",
                    "revision": f["revision"],
                    "content": "overwrite",
                }
            )
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual((self.project / "a.txt").read_text(), "agent edit")
        (self.project / "escape").symlink_to(self.root, target_is_directory=True)
        for path in ["../agent.toml", "/etc/passwd", "escape/agent.toml", ".env", ".git/config"]:
            with self.assertRaises(Problem):
                self.studio.read_file(self.pid, path)
        result = self.studio.save_file(
            {"project": self.pid, "path": "src/new.py", "content": "print(42)\n", "revision": None}
        )
        self.assertEqual(
            self.studio.read_file(self.pid, "src/new.py")["revision"], result["revision"]
        )
        with self.assertRaises(Problem):
            self.studio.save_file(
                {"project": self.pid, "path": "src/new.py", "content": "", "revision": None}
            )

    def test_model_override_preserves_reasoning_and_source(self):
        original = self.config.read_bytes()
        self.studio.save_model(
            {
                "id": "coder",
                "model": "local",
                "protocol": "chat_completions",
                "chat_dialect": "ollama",
                "base_url": "http://localhost:11434/v1",
                "auth": "none",
                "context_window": 32768,
                "max_output_tokens": 4096,
            }
        )
        self.assertEqual(self.config.read_bytes(), original)
        self.assertIn("coder", self.studio.profiles()[0])
        with self.assertRaises(Problem):
            self.studio.save_model(
                {"id": "coder", "context_window": 3000, "max_output_tokens": 4096}
            )

    def test_lan_host_origin_and_token_boundary(self):
        handler = Handler.__new__(Handler)
        handler.server = SimpleNamespace(server_port=4318, server_address=("192.0.2.10", 4318), studio=self.studio)
        valid = {"Host": "192.0.2.10:4318", "Origin": "http://192.0.2.10:4318",
                 "X-Studio-Token": self.studio.token}
        handler.headers = valid.copy()
        handler.check_origin(mutation=True)
        for key, value in [("Host", "attacker.test:4318"), ("Origin", "http://192.0.2.10:9999"),
                           ("Origin", "https://attacker.test"), ("X-Studio-Token", "wrong")]:
            handler.headers = {**valid, key: value}
            with self.assertRaises(Problem) as caught:
                handler.check_origin(mutation=True)
            self.assertEqual(caught.exception.status, 403)

    def test_host_origin_token_and_static(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.studio = self.studio
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(base + "/") as response:
                self.assertIn(b"Switch Studio", response.read())
                self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
            for asset, marker in [("help.js", b"initStudioHelp"), ("office.js", b"officeModel"), ("office.css", b"office-world")]:
                with urllib.request.urlopen(base + "/" + asset) as response:
                    self.assertIn(marker, response.read())
            for headers in [{"Host": "attacker.test"}, {"Origin": "https://attacker.test"}]:
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(
                        urllib.request.Request(base + "/api/state", headers=headers)
                    )
                self.assertEqual(ctx.exception.code, 403)
            payload = json.dumps(
                {"project": self.pid, "path": "safe.txt", "content": "ok", "revision": None}
            ).encode()
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(
                    urllib.request.Request(
                        base + "/api/file",
                        data=payload,
                        headers={"Content-Type": "application/json"},
                    )
                )
            self.assertEqual(ctx.exception.code, 403)
            with urllib.request.urlopen(
                urllib.request.Request(
                    base + "/api/file",
                    data=payload,
                    headers={
                        "Content-Type": "application/json",
                        "X-Studio-Token": self.studio.token,
                    },
                )
            ) as response:
                self.assertEqual(response.status, 200)
            (self.project / "artifact.bin").write_bytes(b"\x00\xffstudio")
            with urllib.request.urlopen(
                base + f"/api/download?project={self.pid}&path=artifact.bin"
            ) as response:
                self.assertEqual(response.read(), b"\x00\xffstudio")
                self.assertIn("attachment", response.headers["Content-Disposition"])
                self.assertEqual(response.headers["Content-Type"], "application/octet-stream")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def model_server(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
        server.requests = []
        server.block = False
        server.fail = False
        server.release = threading.Event()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        profile = self.studio.profiles()[0]["test"]
        profile["base_url"] = f"http://127.0.0.1:{server.server_port}/v1"
        write_config(self.config, {"test": profile}, "test")
        return server

    def wait_until(self, check, seconds=45):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = check()
            if value:
                return value
            time.sleep(0.1)
        logs = "\n".join(
            p.read_text()[-8000:] for p in (self.root / "state/runs").glob("*/console.log")
        )
        self.fail("Timed out. " + logs)

    def test_real_mission_report_has_fixed_path_approval_and_stops_after_save(self):
        server = self.model_server()
        attempt = '1234567890abcdef'
        path = f'company/projects/report-test/reports/{attempt}.json'
        report = {'status': 'plan', 'questions': [], 'tasks': [{'id': 'one', 'title': 'Build',
                  'instructions': 'Write product.txt', 'depends_on': [], 'criteria': ['File exists']}]}
        server.tool_name = 'save_mission_report'
        server.tool_args = report
        try:
            run = self.studio.launch({'project': self.pid, 'task': 'Save the prescribed report.',
                                     'profile': 'test', 'max_turns': 3, 'auto_approve': False},
                                    mission={'id': 'report-test', 'attempt': attempt,
                                             'phase': 'plan', 'attempt_seconds': 60})
            approval = self.wait_until(lambda: self.studio.approvals(run['id']))[0]
            self.assertFalse((self.project / path).exists())
            self.assertEqual(Path(approval['target']).resolve(), (self.project / path).resolve())
            self.studio.decide({'run': run['id'], 'approval': approval['id'], 'allow': True})
            self.wait_until(lambda: self.studio.runs[run['id']]['status'] not in {'running', 'waiting', 'stopping'})
            self.assertEqual(json.loads((self.project / path).read_text()), report)
            self.assertEqual(self.studio.runs[run['id']]['status'], 'completed')
            self.assertEqual(len(server.requests), 1)
            names = [t['function']['name'] for t in server.requests[0]['tools']]
            self.assertIn('save_mission_report', names)
            self.assertNotIn('create_file', names)
        finally:
            server.shutdown()
            server.server_close()

    def test_real_worker_exposes_scoped_ssh_tool_and_saves_evidence_after_approval(self):
        server = self.model_server()
        server.tool_name = 'ssh_inventory'
        server.tool_args = {'target': 'cloud', 'section': 'system'}
        (self.root / 'ssh-targets.json').write_text(json.dumps({'targets': [{
            'id': 'cloud', 'host': '192.0.2.1', 'user': 'test', 'port': 22222,
            'identity_file': str(self.root / 'key'), 'missions': ['ssh-test']}]}))
        binary = self.root / 'bin'
        binary.mkdir()
        fake = binary / 'ssh'
        fake.write_text(f'#!{sys.executable}\nimport sys,json\nsys.stdin.read()\n'
                        'assert sys.argv[-1] == "python3 -I -B - system"\n'
                        'assert "StrictHostKeyChecking=yes" in sys.argv\n'
                        'print(json.dumps({"version":1,"section":"system","data":{"hostname":"fixture-server"}}))\n')
        fake.chmod(0o755)
        try:
            with patch.dict(os.environ, {'PATH': str(binary) + os.pathsep + os.environ['PATH']}):
                run = self.studio.launch({'project': self.pid, 'task': 'Inspect configured server.',
                    'profile': 'test', 'max_turns': 3, 'auto_approve': False},
                    mission={'id': 'ssh-test', 'attempt': 'aabbccddeeff0011', 'phase': 'build', 'attempt_seconds': 60})
                approval = self.wait_until(lambda: self.studio.approvals(run['id']))[0]
                self.assertEqual(approval['name'], 'ssh_inventory')
                self.assertFalse(list(self.project.glob('company/ssh-evidence/*.json')))
                self.studio.decide({'run': run['id'], 'approval': approval['id'], 'allow': True})
                self.wait_until(lambda: self.studio.runs[run['id']]['status'] not in {'running', 'waiting', 'stopping'})
            evidence = list(self.project.glob('company/ssh-evidence/*.json'))
            self.assertEqual(len(evidence), 1, self.studio.events(run['id']))
            self.assertEqual(json.loads(evidence[0].read_text())['data']['hostname'], 'fixture-server')
            names = [t['function']['name'] for t in server.requests[0]['tools']]
            self.assertIn('ssh_inventory', names)
        finally:
            server.shutdown()
            server.server_close()

    def test_standalone_file_tools_write_into_the_open_project(self):
        server = self.model_server()
        try:
            for index, path in enumerate((str((self.project / "physical.txt").resolve()),
                                           "/workspace/alias.txt")):
                with self.subTest(path=path):
                    content = f"PROJECT_FILE_{index}"
                    server.tool_args = {"path": path, "content": content}
                    run = self.studio.launch({"project": self.pid, "profile": "test",
                        "task": "Write the requested project file.", "max_turns": 4,
                        "auto_approve": True})
                    self.wait_until(lambda: self.studio.runs[run["id"]]["status"]
                                    not in {"running", "waiting", "stopping"})
                    target = self.project / Path(path).name
                    self.assertTrue(target.is_file(), self.studio.events(run["id"]))
                    self.assertEqual(target.read_text(), content)
                    self.assertEqual(self.studio.read_file(self.pid, target.name)["content"], content)
        finally:
            server.shutdown()
            server.server_close()

    def test_real_runner_approval_artifact_and_completion(self):
        (self.project / "PROJECT.md").write_text("# PROJECT_NOTES_CONTEXT_OK\n[Decisions](notes/DECISIONS.md)\n")
        server = self.model_server()
        try:
            run = self.studio.launch(
                {
                    "project": self.pid,
                    "task": "Create /outputs/studio-check.txt with STUDIO_TEST_OK.",
                    "profile": "test",
                    "max_turns": 4,
                }
            )
            self.wait_until(lambda: self.studio.approvals(run["id"]))
            self.assertIn("PROJECT_NOTES_CONTEXT_OK", json.dumps(server.requests, ensure_ascii=False))
            self.assertNotIn("PROJECT_NOTES_CONTEXT_OK", run["task"])
            request = json.loads((self.studio.run_dir(run["id"]) / "request.json").read_text())
            self.assertIn("PROJECT_NOTES_CONTEXT_OK", request["task"])
            self.assertEqual(run["project_notes"]["sha256"], self.studio.project_notes(self.pid)["sha256"])
            approval = self.studio.approvals(run["id"])[0]
            self.assertEqual(approval["name"], "create_file")
            self.assertEqual(self.studio.artifacts(run["id"]), [])
            with self.assertRaises(Problem):
                self.studio.launch({"project": self.pid, "task": "overlap"})
            self.studio.decide({"run": run["id"], "approval": approval["id"], "allow": True})
            self.wait_until(
                lambda: (
                    self.studio.runs[run["id"]]["status"] not in {"running", "waiting", "stopping"}
                )
            )
            done = self.studio.runs[run["id"]]
            self.assertEqual(done["status"], "completed", self.studio.events(run["id"]))
            files = self.studio.artifacts(run["id"])
            self.assertEqual(len(files), 1)
            self.assertEqual((self.project / files[0]["path"]).read_text(), "STUDIO_TEST_OK")
            events = self.studio.events(run["id"])
            kinds = {e["type"] for e in events["events"]}
            self.assertTrue(
                {
                    "started",
                    "session",
                    "tool_call",
                    "approval",
                    "decision",
                    "tool_result",
                    "final",
                    "finished",
                }
                <= kinds,
                kinds,
            )
            self.assertEqual(self.studio.events(run["id"], events["offset"])["events"], [])
            restarted = Studio(self.project, self.root / "state", self.config)
            self.assertEqual(restarted.runs[run["id"]]["status"], "completed")
        finally:
            server.release.set()
            server.shutdown()
            server.server_close()

    def test_markdown_notes_are_scoped_bounded_and_saved_only(self):
        self.assertIsNone(self.studio.project_notes(self.pid))
        index = self.project / "PROJECT.md"
        index.write_text("# My project\n[Notes](notes/NOTES.md)\n", encoding="utf-8")
        original = self.studio.project_notes(self.pid)
        other = self.root / "other-project"
        other.mkdir()
        other_id = self.studio.add_project(str(other))["id"]
        self.assertIsNone(self.studio.project_notes(other_id))
        index.write_text("# Updated index", encoding="utf-8")
        self.assertNotEqual(original["sha256"], self.studio.project_notes(self.pid)["sha256"])
        self.assertIn("My project", original["context"])
        index.write_text("a" * 12001)
        with self.assertRaisesRegex(Problem, "12 000"):
            self.studio.project_notes(self.pid)
        index.write_bytes(b"\xff")
        with self.assertRaisesRegex(Problem, "UTF-8"):
            self.studio.project_notes(self.pid)
        index.unlink()
        private = self.root / "private.md"
        private.write_text("Do not send this")
        index.symlink_to(private)
        with self.assertRaises(Problem):
            self.studio.project_notes(self.pid)

    def test_real_runner_cancel(self):
        server = self.model_server()
        server.block = True
        try:
            run = self.studio.launch(
                {"project": self.pid, "task": "Test cancellation", "profile": "test"}
            )
            self.wait_until(lambda: server.requests)
            self.studio.stop(run["id"])
            self.wait_until(
                lambda: self.studio.runs[run["id"]]["status"] == "cancelled", seconds=10
            )
            self.assertNotIn(run["id"], self.studio.processes)
        finally:
            server.release.set()
            server.shutdown()
            server.server_close()

    def test_declining_action_does_not_create_file(self):
        server = self.model_server()
        try:
            run = self.studio.launch(
                {"project": self.pid, "task": "Create file", "profile": "test", "max_turns": 3}
            )
            approvals = self.wait_until(lambda: self.studio.approvals(run["id"]))
            self.studio.decide({"run": run["id"], "approval": approvals[0]["id"], "allow": False})
            self.wait_until(
                lambda: (
                    self.studio.runs[run["id"]]["status"] not in {"running", "waiting", "stopping"}
                )
            )
            self.assertEqual(self.studio.artifacts(run["id"]), [])
            self.assertNotEqual(self.studio.runs[run["id"]]["status"], "completed")
        finally:
            server.shutdown()
            server.server_close()

    def test_provider_error_is_not_completed_even_if_cli_exits_zero(self):
        server = self.model_server()
        server.fail = True
        try:
            run = self.studio.launch(
                {"project": self.pid, "task": "Read file", "profile": "test", "max_turns": 2}
            )
            self.wait_until(
                lambda: (
                    self.studio.runs[run["id"]]["status"] not in {"running", "waiting", "stopping"}
                )
            )
            self.assertEqual(self.studio.runs[run["id"]]["status"], "failed")
            self.assertIn("error", {e["type"] for e in self.studio.events(run["id"])["events"]})
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
