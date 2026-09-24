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

class DecisionBoundaries(DecisionTests):
    def test_shadow_keeps_baseline_and_records_disagreement(self):
        server = self.judge({'action': 'build:two', 'confidence': .95, 'reason': 'Review candidate'})
        m = self.mission(); m['decision_mode'] = 'shadow'
        self.assertEqual(self.controller.next_work(m), ('build', 'one'))
        self.controller.decisions.close()
        record = self.controller.decisions.get(m['decision_request'])
        self.assertEqual(record['status'], 'shadow')
        self.assertEqual(record['choice'], 'build:two')
        self.assertEqual(record['baseline'], 'build:one')
        self.assertEqual(self.controller.next_work(m), ('build', 'one'))
        self.assertEqual(len(server.requests), 1)

    def test_off_performs_no_inference(self):
        server = self.judge({})
        m = self.mission(); m['decision_mode'] = 'off'
        self.assertEqual(self.controller.next_work(m), ('build', 'one'))
        self.assertEqual(server.requests, [])

    def test_late_reply_and_changed_state_cannot_select_or_spawn_overlap(self):
        from unittest.mock import patch
        entered, release = threading.Event(), threading.Event()
        self.judge({})
        m = self.mission()
        def delayed(*args):
            entered.set(); release.wait(2)
            return {'action': 'build:two', 'confidence': .99, 'reason': 'Late'}, {}, 'fixture'
        with patch('studio.decisions.request_choice', delayed):
            self.assertIsNone(self.controller.next_work(m))
            self.assertTrue(entered.wait(1))
            key = m['decision_request']
            m['tasks'][0]['criteria'] = ['Changed criterion beyond compact frame']
            self.assertEqual(self.controller.next_work(m), ('build', 'one'))
            self.assertEqual(len(self.controller.decisions.threads), 1)
            release.set(); self.controller.decisions.close()
            self.assertEqual(self.controller.decisions.get(key)['status'], 'superseded')
        entered.clear(); release.clear()
        with patch('studio.decisions.request_choice', delayed):
            self.assertIsNone(self.controller.next_work(m))
            self.assertTrue(entered.wait(1))
            key = m['decision_request']
            record = self.controller.decisions.get(key); record['expires'] = time.time() - 1
            self.controller.decisions.save(record)
            self.assertEqual(self.controller.next_work(m), ('build', 'one'))
            release.set(); self.controller.decisions.close()
            self.assertEqual(self.controller.decisions.get(key)['status'], 'fallback')

    def test_choice_contract_rejects_malformed_untrusted_values(self):
        for action, confidence in [([], .9), ('build:one', True), ('build:one', float('nan')),
                                   ('build:one', float('inf')), ('unknown', .99)]:
            with self.subTest(action=action, confidence=confidence), self.assertRaises(ValueError):
                choose_action({'action': action, 'confidence': confidence, 'reason': 'x'}, ['build:one'], .8)
