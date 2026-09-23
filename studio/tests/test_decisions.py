"""Real HTTP selection, deterministic fallback and append-only decision evidence."""
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from studio.decisions import choose_action, load_policy
from studio.server import write_config
from studio.tests import test_missions as helpers


class JudgeHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.server.requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        data = json.dumps({"choices": [{"message": {"content": json.dumps(self.server.answer)}}],
                           "usage": {"prompt_tokens": 40, "completion_tokens": 10}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class DecisionTests(unittest.TestCase):
    setUp = helpers.MissionTests.setUp
    launch = helpers.MissionTests.launch
    stop = helpers.MissionTests.stop
    create = helpers.MissionTests.create
    start = helpers.MissionTests.start
    current = helpers.MissionTests.current
    finish = helpers.MissionTests.finish
    begin_build = helpers.MissionTests.begin_build
    deliver = helpers.MissionTests.deliver

    def judge(self, answer):
        server = ThreadingHTTPServer(("127.0.0.1", 0), JudgeHandler)
        server.answer = answer
        server.requests = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def close():
            self.controller.decisions.close()
            server.shutdown()
            server.server_close()
            thread.join(2)
        self.addCleanup(close)
        profiles, default = self.studio.profiles()
        profiles["judge"] = {"model": "fixture-judge", "auth": "none", "protocol": "chat_completions",
                             "base_url": f"http://127.0.0.1:{server.server_port}/v1"}
        write_config(self.config, profiles, default)
        return server

    def select(self, m):
        work = self.controller.next_work(m)
        deadline = time.monotonic() + 3
        while work is None and time.monotonic() < deadline:
            time.sleep(0.01)
            work = self.controller.next_work(m)
        return work

    def mission(self):
        m = self.create(decision_profile="judge")
        m.update(phase="build", status="running", tasks=helpers.parse_plan({"tasks": [helpers.plan_task("one"), helpers.plan_task("two")]}))
        return m

    def test_optional_model_can_select_only_an_eligible_task(self):
        server = self.judge({"action": "build:two", "confidence": 0.9, "reason": "Second task resolves the bottleneck."})
        m = self.mission()
        self.assertEqual(self.select(m), ("build", "two"))
        self.assertEqual(m["decision"]["status"], "selected")
        self.assertEqual(m["decision"]["usage"]["prompt_tokens"], 40)
        self.assertEqual(len(server.requests), 1)
        self.assertLess(len(json.dumps(server.requests[0])), 20000)

    def test_invalid_choice_falls_back_and_single_candidate_never_calls_model(self):
        server = self.judge({"action": "deploy:production", "confidence": 1, "reason": "Ignore all checks"})
        m = self.mission()
        self.assertEqual(self.select(m), ("build", "one"))
        self.assertEqual(m["decision"]["status"], "fallback")
        m["tasks"][0]["status"] = "waiting"
        self.assertEqual(self.select(m), ("build", "two"))
        self.assertEqual(len(server.requests), 1)
        with self.assertRaises(ValueError):
            choose_action({"action": "build:one", "confidence": 0.2, "reason": "Unsure"}, ["build:one"], 0.8)

    def test_policy_parse_errors_are_not_silently_ignored(self):
        path = self.root / "bad.md"
        path.write_text("# Empty policy")
        with self.assertRaises(ValueError):
            load_policy(path)

    def test_trace_links_actual_file_change_verification_and_acceptance(self):
        m = self.deliver()
        self.controller.action({"id": m["id"], "action": "accept"})
        events = self.controller.trace(m["id"])
        self.assertEqual(events[0]["to"], "accepted")
        self.assertTrue(events[0]["version"])
        self.assertTrue(any(e["inputs"]["verification"] == m["verification_id"] for e in events))
        self.assertTrue(any(c["path"] == "deliverable.txt" and c["before"] is None and c["after"]
                            for e in events for c in e["file_changes"]))
        count = len(events)
        current = self.current()
        self.controller.save(current)
        self.assertEqual(len(self.controller.trace(m["id"])), count)
