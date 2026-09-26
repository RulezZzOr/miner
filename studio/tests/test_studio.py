"""Behavioral tests for file conflicts, HTTP boundary, and the real runner bridge."""

from __future__ import annotations

import io
import json
import os
import socket
import subprocess
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

import studio.server
from studio.server import ACTIVE, RESTART_REASON, Handler, Problem, Studio, read_project_file, write_config
from studio.tests.sandbox_support import requires_sandbox


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
            delta = {"role": "assistant", "content": "File was created. STUDIO_TEST_OK"}
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

    def test_public_state_exposes_reviewer_role_metadata(self):
        (self.studio.data / "models.json").write_text(json.dumps({
            "reviewer": {"model": "review-model", "protocol": "chat_completions",
                         "base_url": "http://127.0.0.1:1/v1", "auth": "none",
                         "reviewer_default": True}
        }))
        profiles = self.studio.public_state()["profiles"]
        reviewer = next(profile for profile in profiles if profile["id"] == "reviewer")
        self.assertTrue(reviewer["reviewer_default"])

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
        original_open = urllib.request.urlopen
        def authenticated_open(value):
            req = value if isinstance(value, urllib.request.Request) else urllib.request.Request(value)
            req.add_header('Authorization', 'Bearer '+(self.studio.data/'access-key').read_text().strip())
            return original_open(req)
        try:
            with authenticated_open(base + "/") as response:
                self.assertIn(b"Switch Studio", response.read())
                self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
            for asset, marker in [("help.js", b"initStudioHelp"), ("office.js", b"officeModel"), ("office.css", b"office-world"), ("dashboard.js", b"submitDashboardTask"), ("dashboard.css", b"dash-layout")]:
                with authenticated_open(base + "/" + asset) as response:
                    self.assertIn(marker, response.read())
            for headers in [{"Host": "attacker.test"}, {"Origin": "https://attacker.test"}]:
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    authenticated_open(
                        urllib.request.Request(base + "/api/state", headers=headers)
                    )
                self.assertEqual(ctx.exception.code, 403)
            payload = json.dumps(
                {"project": self.pid, "path": "safe.txt", "content": "ok", "revision": None}
            ).encode()
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                authenticated_open(
                    urllib.request.Request(
                        base + "/api/file",
                        data=payload,
                        headers={"Content-Type": "application/json"},
                    )
                )
            self.assertEqual(ctx.exception.code, 403)
            with authenticated_open(
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
            with authenticated_open(
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

    @requires_sandbox
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
            self.assertTrue((self.studio.control_dir(run['id']) / 'run.json').exists())
            self.assertTrue((self.studio.control_dir(run['id']) / 'processes.json').exists())
            self.assertFalse((self.studio.run_dir(run['id']) / 'run.json').exists())
            self.assertFalse((self.studio.run_dir(run['id']) / 'processes.json').exists())
            (self.studio.run_dir(run['id']) / 'run.json').write_text(json.dumps({'id':'injected','status':'running'}))
            restored = Studio(self.project, self.root / 'state', self.config)
            try:
                self.assertEqual(restored.runs[run['id']]['status'], self.studio.runs[run['id']]['status'])
                self.assertNotIn('injected', restored.runs)
            finally:
                restored.missions.close(); restored.oauth.close()

            self.assertEqual(json.loads((self.project / path).read_text()), report)
            self.assertEqual(self.studio.runs[run['id']]['status'], 'completed')
            self.assertEqual(len(server.requests), 1)
            names = [t['function']['name'] for t in server.requests[0]['tools']]
            self.assertIn('save_mission_report', names)
            self.assertNotIn('create_file', names)
        finally:
            server.shutdown()
            server.server_close()

    @requires_sandbox
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
                    # Room for one tool call beside the time the runner keeps for saving the report.
                    mission={'id': 'ssh-test', 'attempt': 'aabbccddeeff0011', 'phase': 'build', 'attempt_seconds': 300})
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

    @requires_sandbox
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

    @requires_sandbox
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
        # An oversized guide never blocks launches: a bounded excerpt is attached instead.
        index.write_text("# Guide\n" + "a" * 12001 + "TAIL_NOT_ATTACHED")
        oversized = self.studio.project_notes(self.pid)
        self.assertIn("over 12,000 bytes", oversized["context"])
        self.assertNotIn("TAIL_NOT_ATTACHED", oversized["context"])
        self.assertLess(len(oversized["context"].encode()), 14_500)
        self.assertEqual(oversized["sha256"], studio.server.digest(index.read_bytes()))
        self.assertIn("regardless of the language of the input", oversized["context"])
        index.write_bytes(b"\xff")
        with self.assertRaisesRegex(Problem, "UTF-8"):
            self.studio.project_notes(self.pid)
        index.unlink()
        private = self.root / "private.md"
        private.write_text("Do not send this")
        index.symlink_to(private)
        with self.assertRaises(Problem):
            self.studio.project_notes(self.pid)

    @requires_sandbox
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

    @requires_sandbox
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

    @requires_sandbox
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

    def control_fixture(self, run_id, **fields):
        (self.studio.data / "runs" / run_id).mkdir(parents=True)
        (self.studio.data / "run-control" / run_id).mkdir(parents=True)
        self.studio.runs[run_id] = {"id": run_id, "project": self.pid, "status": "running",
                                    "created": time.time(), "isolated_control": True, **fields}
        return self.studio.data / "runs" / run_id

    def test_launch_failure_after_registration_leaves_no_phantom_run(self):
        (self.studio.data / "run-control").write_text("not a directory")
        with self.assertRaises(Problem) as caught:
            self.studio.launch({"project": self.pid, "task": "Anything", "profile": "test"})
        self.assertEqual(caught.exception.status, 500)
        self.assertNotIn(str(self.studio.data), str(caught.exception))
        (run,) = self.studio.runs.values()
        self.assertEqual((run["status"], run["failure_kind"]), ("failed", "setup"))
        self.assertTrue(run["reason"].startswith("Cannot start the worker"))
        self.assertFalse(any(r["status"] in ACTIVE for r in self.studio.runs.values()))
        self.assertEqual(self.studio.processes, {})
        # An active record without a supervised worker is ended by stop, not silently kept.
        (self.studio.data / "run-control").unlink()
        self.control_fixture("ghost")
        self.assertEqual(self.studio.stop("ghost"), {"ok": True})
        self.assertEqual(self.studio.runs["ghost"]["status"], "failed")

    def test_public_state_returns_bounded_run_summaries(self):
        self.studio.runs["big"] = {"id": "big", "project": self.pid, "status": "completed", "created": time.time(),
                                   "task": "x" * 50000, "review_reads": ["evidence"] * 100,
                                   "mission": {"id": "m", "attempt": "big", "phase": "build",
                                               "review_packet": {"large": "y" * 10000}}}
        run = self.studio.public_state()["runs"][0]
        self.assertTrue(run["task_truncated"])
        self.assertEqual(run["task"], "x" * 1000 + studio.server.RUN_SUMMARY_MORE)
        self.assertEqual(run["mission"], {"id": "m", "attempt": "big", "phase": "build"})
        self.assertNotIn("review_reads", run)
        self.assertEqual(len(self.studio.events("big")["run"]["task"]), 50000)

    def test_directory_reads_do_not_leak_descriptors(self):
        (self.project / "folder").mkdir()
        before = len(os.listdir("/dev/fd"))
        for _ in range(20):
            with self.assertRaises(Problem) as caught:
                read_project_file(self.project, "folder", 1000)
            self.assertEqual(caught.exception.status, 400)
        self.assertLess(len(os.listdir("/dev/fd")), before + 5)
        with self.assertRaises(Problem) as caught:
            self.studio.save_file({"project": self.pid, "path": "folder", "content": "x", "revision": None})
        self.assertEqual(caught.exception.status, 400)
        gone = self.root / "gone"
        gone.mkdir()
        gone_id = self.studio.add_project(str(gone))["id"]
        gone.rmdir()
        with self.assertRaises(Problem) as caught:
            self.studio.tree(gone_id)
        self.assertEqual((caught.exception.status, str(caught.exception)), (404, "Project folder is missing."))

    def test_probe_reads_provider_keys_without_changing_the_process_environment(self):
        received = []

        class Capture(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                received.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"data":[{"id":"test"}, "not-an-object"]}')

        http = ThreadingHTTPServer(("127.0.0.1", 0), Capture)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        env = self.config.parent / ".env"
        env.write_text("STUDIO_PROBE_API_KEY=old-key\n")
        base = {"model": "test", "protocol": "chat_completions", "auth": "env"}
        self.studio.save_model({**base, "id": "keyed", "api_key_env": "STUDIO_PROBE_API_KEY",
                                "base_url": f"http://127.0.0.1:{http.server_port}/v1"})
        with patch.dict(os.environ):
            os.environ.pop("STUDIO_PROBE_API_KEY", None)
            self.assertTrue(self.studio.probe("keyed")["model_found"])
            self.assertNotIn("STUDIO_PROBE_API_KEY", os.environ)
            env.write_text("STUDIO_PROBE_API_KEY=rotated-key\n")
            self.studio.probe("keyed")
        self.assertEqual(received, ["Bearer old-key", "Bearer rotated-key"])
        # A key goes only to its intended server, and never in clear text off this computer.
        for extra in ({"api_key_env": "HOME", "base_url": "https://models.example.com/v1"},
                      {"api_key_env": "STRIPE_API_KEY", "base_url": "https://collector.example/v1"},
                      {"api_key_env": "OPENAI_API_KEY", "base_url": "https://collector.example/v1"},
                      {"api_key_env": "STUDIO_PROBE_API_KEY", "base_url": "http://models.example.com/v1"},
                      {"api_key_env": "STUDIO_PROBE_API_KEY", "base_url": "http://192.168.1.20:8080/v1"}):
            with self.subTest(**extra), self.assertRaises(Problem):
                self.studio.save_model({**base, "id": "leak", **extra})
        self.studio.save_model({**base, "id": "openai", "api_key_env": "OPENAI_API_KEY",
                                "base_url": "https://api.openai.com/v1"})
        self.studio.save_model({**base, "id": "own", "api_key_env": "STUDIO_PROBE_API_KEY",
                                "base_url": "https://models.example.com/v1"})

    def test_saved_profiles_are_checked_again_before_a_key_is_sent(self):
        lan = {"model": "test", "protocol": "chat_completions", "auth": "env",
               "api_key_env": "LAN_API_KEY", "base_url": "http://192.168.1.20:8080/v1"}
        write_config(self.config, {"test": lan}, "test")
        # Plain http on a private network only for the exact target the owner declared in agent.toml.
        self.studio.check_credential_target(self.studio.profiles()[0]["test"])
        self.studio.save_model({**lan, "id": "same"})
        with self.assertRaises(Problem):
            self.studio.save_model({**lan, "id": "other", "base_url": "http://192.168.1.99:8080/v1"})
        # A profile saved by an older release is refused where it would be used.
        (self.studio.data / "models.json").write_text(json.dumps({"old": {
            **lan, "api_key_env": "STRIPE_API_KEY", "base_url": "https://collector.example/v1"}}))
        with patch.dict(os.environ, {"STRIPE_API_KEY": "SYNTHETIC_ONLY"}):
            with self.assertRaises(Problem):
                self.studio.probe("old")
            with self.assertRaises(Problem):
                self.studio.launch({"project": self.pid, "task": "Check the notes.", "profile": "old"})
            self.assertEqual(self.studio.runs, {})
            frame = {"query": "", "filtered": False, "visibleItems": ["Blue desk"]}
            result = self.studio.browser_pilot.choose({"profile": "old", "frame": frame})
        self.assertEqual(result["status"], "fallback")
        self.assertIn("not allowed for this server", result["reason"])

    def test_slow_download_completes_and_a_late_failure_never_splices_a_second_response(self):
        """The connection timeout bounds each body slice, not the whole transfer."""
        (self.project / "big.bin").write_bytes(b"x" * 6_000_000)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.studio = self.studio
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        key = (self.studio.data / "access-key").read_text().strip()
        with patch.object(Handler, "timeout", 1.0), socket.socket() as client:
            client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 262144)
            client.connect(("127.0.0.1", server.server_port))
            client.sendall(f"GET /api/download?project={self.pid}&path=big.bin HTTP/1.1\r\n"
                           f"Host: 127.0.0.1:{server.server_port}\r\nAuthorization: Bearer {key}\r\n\r\n".encode())
            started, data = time.monotonic(), b""
            while chunk := client.recv(65536):
                data += chunk
                time.sleep(0.04)  # A steady reader at no more than about 1.6 MB/s.
            elapsed = time.monotonic() - started
        head, _, body = data.partition(b"\r\n\r\n")
        self.assertTrue(head.startswith(b"HTTP/1.0 200"), head[:80])
        self.assertGreater(elapsed, 1.0)  # The transfer outlived the timeout and still completed.
        self.assertEqual(len(body), 6_000_000)
        self.assertNotIn(b'"error"', body)
        # A failure after the status line closes the connection; it never writes a second response.
        handler = Handler.__new__(Handler)
        handler.response_started, handler.wfile = True, io.BytesIO()
        handler.fail("The request timed out.", 408)
        self.assertTrue(handler.close_connection)
        self.assertEqual(handler.wfile.getvalue(), b"")

    def test_watch_copies_failure_kind_and_explains_crashes(self):
        cases = [("incomplete", 0, {"status": "incomplete", "reason": "No report was saved.", "failure_kind": "no_report"}, "no_report"),
                 ("crashed", 3, None, "crash"),
                 ("unknown-kind", 0, {"status": "failed", "reason": "Synthetic.", "failure_kind": "invented"}, None)]
        for run_id, code, result, kind in cases:
            with self.subTest(run_id=run_id):
                directory = self.control_fixture(run_id)
                if result:
                    (directory / "result.json").write_text(json.dumps(result))
                process = subprocess.Popen([sys.executable, "-c", f"raise SystemExit({code})"], start_new_session=True)
                self.studio.processes[run_id] = process
                self.studio.watch(run_id, process)
                run = self.studio.runs[run_id]
                self.assertEqual(run.get("failure_kind"), kind)
                self.assertTrue(run["reason"])
                saved = json.loads((self.studio.control_dir(run_id) / "run.json").read_text())
                self.assertEqual(saved["status"], run["status"])
                self.assertNotIn(run_id, self.studio.processes)
        self.assertEqual(self.studio.runs["incomplete"]["status"], "incomplete")
        self.assertIn("exit code 3", self.studio.runs["crashed"]["reason"])

    def test_controller_shutdown_and_restart_interrupt_runs_without_failing_them(self):
        self.control_fixture("live")
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True,
                                   stderr=subprocess.DEVNULL)
        self.studio.processes["live"] = process
        self.studio.process_tree(process).grace = 0.3
        watcher = threading.Thread(target=self.studio.watch, args=("live", process))
        process._studio_watcher = watcher
        watcher.start()
        self.studio.stop("live", interrupted=True)
        watcher.join(10)
        run = self.studio.runs["live"]
        self.assertEqual((run["status"], run["reason"], run["reason_kind"]),
                         ("interrupted", RESTART_REASON, "controller_restart"))
        # Records left active by a crash restore the same way; an owner stop stays a cancellation.
        for run_id, status in (("crashed", "running"), ("owner-stop", "stopping")):
            self.control_fixture(run_id)
            control = self.studio.data / "run-control" / run_id
            (control / "run.json").write_text(json.dumps({**self.studio.runs[run_id], "status": status}))
            (control / "processes.json").write_text("[]")
        restored = Studio(self.project, self.root / "state", self.config)
        try:
            self.assertEqual((restored.runs["crashed"]["status"], restored.runs["crashed"]["reason_kind"]),
                             ("interrupted", "controller_restart"))
            self.assertEqual(restored.runs["owner-stop"]["status"], "cancelled")
            self.assertEqual(restored.runs["live"]["status"], "interrupted")
        finally:
            restored.missions.close()
            restored.oauth.close()

    def test_script_launch_imports_the_server_package_once_and_help_has_no_side_effects(self):
        root = Path(studio.server.__file__).resolve().parents[1]
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        direct = subprocess.run([sys.executable, "-X", "importtime", str(root / "studio" / "server.py"), "--help"],
                                cwd=self.root, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(direct.returncode, 0, direct.stderr[-2000:])
        self.assertIn("switch-studio", direct.stdout)
        imported = {line.rpartition("|")[2].strip() for line in direct.stderr.splitlines() if line.startswith("import time:")}
        self.assertIn("studio.server", imported)
        self.assertFalse({"server", "access", "missions"} & imported)  # No second, flat copy.
        config = root / "agent.toml"
        before = config.read_bytes() if config.exists() else None
        launcher = subprocess.run([sys.executable, str(root / "scripts" / "start_studio.py"), "--help"],
                                  cwd=self.root, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(launcher.returncode, 0, launcher.stderr[-2000:])
        self.assertIn("--state-dir", launcher.stdout)
        self.assertEqual(config.read_bytes() if config.exists() else None, before)


if __name__ == "__main__":
    unittest.main()
