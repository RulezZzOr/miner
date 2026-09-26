"""Durability, dependency scheduling, evidence gates and restart boundaries."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from studio.missions import Missions, continue_answer, failure_kind, parse_plan, used_runs
from studio.server import Studio, atomic_json, write_config
from studio.tests.sandbox_support import requires_sandbox


def plan_task(key="one", deps=None):
    return {"id": key, "title": key, "instructions": "Create deliverable.txt and check its content.",
            "depends_on": deps or [], "criteria": ["File contains OK."]}


def report(status="done", criterion="File contains OK."):
    return {"status": status, "summary": "Content verified.", "artifacts": ["deliverable.txt"],
            "checks": [{"criterion": criterion, "passed": True, "evidence": "read_file: OK",
                        "outcome": "supported", "issue": "none", "needs_owner": False}]}


class MissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.config = self.root / "agent.toml"
        write_config(self.config, {name: {"model": name, "auth": "none", "protocol": "chat_completions",
            "base_url": "http://127.0.0.1:1/v1"} for name in ["coder", "reviewer"]}, "coder")
        self.studio = Studio(self.project, self.root / "state", self.config)
        self.pid = next(iter(self.studio.projects))
        self.now = 1000
        self.controller = Missions(self.studio, clock=lambda: self.now)
        self.addCleanup(self.controller.close)
        self.launched = []
        self.studio.launch = self.launch
        self.studio.stop = self.stop

    def launch(self, body, *, mission):
        key = mission["attempt"]
        self.assertNotIn(key, self.studio.runs)
        directory = self.studio.data / "runs" / key
        directory.mkdir(parents=True)
        run = {"id": key, "created": self.now, "status": "running", "project": self.pid, "mission": mission}
        self.studio.runs[key] = run
        atomic_json(directory / "run.json", run)
        self.launched.append((body, mission))
        return run

    def stop(self, key):
        self.studio.runs[key]["status"] = "cancelled"

    def create(self, **kw):
        return self.controller.create({"project": self.pid, "title": "Pilot", "goal": "Create verified file.",
            "criteria": ["Product is readable."], "profile": "coder", "review_profile": "reviewer", "isolated": False,
            "verification_checks": [{"argv": [sys.executable, "-c", "from pathlib import Path; assert Path('deliverable.txt').read_text().startswith('OK')"]}], **kw})

    def test_runtime_settings_require_stopped_worker_and_preserve_deadline(self):
        self.begin_build()
        with self.assertRaises(ValueError):
            self.controller.action({"id": self.key, "action": "runtime_settings", "review_profile": "coder"})
        self.controller.action({"id": self.key, "action": "pause"})
        self.controller.tick()
        before = self.current()
        m = self.controller.action({"id": self.key, "action": "runtime_settings", "review_profile": "coder", "attempt_minutes": 10})
        self.assertEqual(m["status"], "paused")
        self.assertEqual(m["deadline"], before["deadline"])
        self.assertEqual(m["tasks"], before["tasks"])
        self.assertEqual(m["attempts"], before["attempts"])
        self.assertEqual(m["review_profile"], "coder")
        self.assertEqual(m["attempt_minutes"], 10)
        with self.assertRaisesRegex(ValueError, "available model"):
            self.controller.action({"id": self.key, "action": "runtime_settings", "profile": "nonexistent"})

    def start(self, **kw):
        m = self.create(**kw)
        self.key = m["id"]
        self.controller.action({"id": self.key, "action": "start"})
        self.controller.tick()
        return self.current()

    def current(self):
        return self.controller.get(self.key)

    def finish(self, value):
        m = self.current()
        a = m["attempts"][-1]
        self.studio.save_file({"project": self.pid, "path": self.controller.report_path(m, a),
                              "content": json.dumps(value), "revision": None})
        self.studio.runs[a["id"]]["status"] = "completed"
        atomic_json(self.studio.run_dir(a["id"]) / "run.json", self.studio.runs[a["id"]])
        self.controller.tick()
        deadline = time.monotonic() + 5
        while self.current()["status"] == "verifying" and time.monotonic() < deadline:
            self.controller.tick()
            time.sleep(0.02)
        if self.current()["status"] == "running" and not self.current()["active_attempt"]:
            self.controller.tick()
        return self.current()

    def begin_build(self, tasks=None):
        self.start()
        self.finish({"status": "plan", "tasks": tasks or [plan_task()], "questions": []})
        self.controller.action({"id": self.key, "action": "approve_plan"})
        self.controller.tick()
        (self.project / "deliverable.txt").write_text("OK")

    def deliver(self):
        self.begin_build()
        self.finish(report())
        self.finish(report("pass"))
        return self.finish(report("pass", "Product is readable."))

    @requires_sandbox
    def test_full_lifecycle_has_separate_review_and_survives_restart(self):
        m = self.deliver()
        self.assertEqual(m["status"], "ready")
        self.assertEqual([a["phase"] for a in m["attempts"]], ["plan", "build", "review", "final"])
        self.assertEqual([b["profile"] for b, _ in self.launched], ["coder", "coder", "reviewer", "reviewer"])
        # Plan/review/final follow the mission limits with floors for slow local models.
        minutes = m["attempt_minutes"] * 60
        self.assertEqual([meta["attempt_seconds"] for _, meta in self.launched],
                         [max(minutes, 600), minutes, max(minutes, 1200), max(minutes, 1200)])
        self.assertEqual(self.launched[0][0]["max_turns"], max(m["max_turns"], 16))
        self.assertEqual(len({a["id"] for a in m["attempts"]}), 4)
        self.assertEqual(len(m["evidence"]), 4)
        restored = Missions(self.studio, clock=lambda: self.now)
        result = restored.action({"id": self.key, "action": "accept"})
        self.assertEqual(result["status"], "accepted")

    @requires_sandbox
    def test_late_edit_prevents_acceptance(self):
        self.deliver()
        (self.project / "deliverable.txt").write_text("changed")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.controller.action({"id": self.key, "action": "accept"})
        self.controller.action({"id": self.key, "action": "recheck"})
        self.controller.tick()
        deadline = time.monotonic() + 5
        while not self.current()["active_attempt"] and time.monotonic() < deadline:
            self.controller.tick()
            time.sleep(0.02)
        self.assertEqual(self.current()["attempts"][-1]["phase"], "build")
        self.assertIn("Independent check", self.current()["tasks"][-1]["feedback"])

    @requires_sandbox
    def test_functional_evidence_precedes_final_review_and_is_reused_for_acceptance(self):
        self.begin_build()
        self.finish(report())
        m = self.finish(report("pass"))
        self.assertEqual(m["attempts"][-1]["phase"], "final")
        record = self.controller.verifications.verify(m)
        prompt = self.launched[-1][0]["task"]
        self.assertIn('FUNCTIONALITY:', prompt)
        packet, _ = json.JSONDecoder().raw_decode(prompt[prompt.index('{"version"'):])
        self.assertEqual(packet['independent']['checks'][0]['exit_code'], 0)
        self.assertEqual(packet['independent']['id'], record['id'])
        self.assertIsNone(m["final_report"])
        m = self.finish(report("pass", "Product is readable."))
        self.assertEqual(m["status"], "ready")
        self.assertEqual(m["verification_id"], record["id"])

    def test_review_handoff_excludes_unrelated_tasks_and_duplicate_history(self):
        self.begin_build([plan_task(), plan_task("later", ["one"])])
        m = self.current()
        m["tasks"][1]["instructions"] = "UNRELATED_LATER_INSTRUCTIONS"
        m["tasks"][1]["summary"] = "UNRELATED_LATER_SUMMARY"
        m["tasks"][0]["instructions"] = "LONG_IMPLEMENTATION_INSTRUCTIONS"
        self.controller.save(m)
        self.finish(report())
        prompt = self.launched[-1][0]["task"]
        self.assertIn("ARCHITECTURE:", prompt)
        self.assertIn("not code style or a line-by-line source audit", prompt)
        self.assertNotIn("UNRELATED_LATER", prompt)
        self.assertNotIn("LONG_IMPLEMENTATION_INSTRUCTIONS", prompt)

    def test_source_change_during_final_review_cannot_pass(self):
        self.begin_build()
        self.finish(report())
        self.finish(report("pass"))
        (self.project / "deliverable.txt").write_text("OK changed")
        m = self.finish(report("pass", "Product is readable."))
        self.assertNotEqual(m["status"], "ready")
        self.assertIsNone(m["final_report"])

    @requires_sandbox
    def test_new_check_spec_requires_a_new_functional_review(self):
        self.deliver()
        m = self.controller.action({"id": self.key, "action": "set_checks",
            "verification_checks": [{"argv": [sys.executable, "-c", "print('NEW SCENARIO')"]}]})
        self.assertIsNone(m["final_report"])
        deadline = time.monotonic() + 5
        while not self.current()["active_attempt"] and time.monotonic() < deadline:
            self.controller.tick()
            time.sleep(0.02)
        self.assertEqual(self.current()["attempts"][-1]["phase"], "final")
        self.assertIn("NEW SCENARIO", self.launched[-1][0]["task"])
        with self.assertRaises(ValueError):
            self.controller.action({"id": self.key, "action": "accept"})

    def test_plan_cannot_schedule_its_own_controller_report_as_product_work(self):
        m = self.start()
        task = plan_task()
        task['instructions'] = 'Check report ' + m['active_attempt'] + '.json'
        m = self.finish({'status': 'plan', 'tasks': [task], 'questions': []})
        self.assertEqual(m['tasks'], [])
        self.assertNotEqual(m['status'], 'awaiting_plan')
        self.assertIn("own planning report", m['message'])

    def test_once_serialized_report_is_validated_without_losing_original_evidence(self):
        self.start()
        m = self.finish(json.dumps({'status': 'plan', 'tasks': [plan_task()], 'questions': []}))
        self.assertEqual(m['status'], 'awaiting_plan')
        self.assertEqual(m['evidence'][0]['report']['status'], 'plan')
        self.assertTrue(m['evidence'][0]['report_sha256'])

    def test_serialized_report_still_rejects_invalid_structure(self):
        self.start()
        m = self.finish(json.dumps({'status': 'plan', 'tasks': []}))
        self.assertNotEqual(m['status'], 'awaiting_plan')
        self.assertEqual(m['tasks'], [])

    def test_pausing_a_blocked_mission_does_not_require_two_resumes(self):
        m = self.create()
        m.update(status="blocked", phase="plan")
        self.controller.save(m)
        self.controller.action({"id": m["id"], "action": "pause"})
        resumed = self.controller.action({"id": m["id"], "action": "resume"})
        self.assertEqual(resumed["status"], "running")

    def test_missing_file_and_incomplete_checks_never_advance(self):
        self.begin_build()
        (self.project / "deliverable.txt").unlink()
        m = self.finish(report())
        self.assertEqual(m["tasks"][0]["status"], "pending")
        self.assertEqual(m["failures"], 1)
        self.assertEqual(len(m["evidence"]), 1)
        self.now += 31
        self.controller.tick()
        (self.project / "deliverable.txt").write_text("OK")
        m = self.finish(report(criterion="Wrong criterion"))
        self.assertEqual(m["tasks"][0]["status"], "pending")
        self.assertEqual(m["failures"], 2)

    def test_source_file_change_during_review_invalidates_pass(self):
        self.begin_build()
        self.finish(report())
        (self.project / "deliverable.txt").write_text("Changed by reviewer")
        m = self.finish(report("pass"))
        self.assertEqual(m["tasks"][0]["status"], "review")
        self.assertEqual(m["failures"], 1)

    def test_unmet_requirement_waits_without_retry_and_revision_preserves_evidence(self):
        self.begin_build()
        output = report()
        output["checks"][0].update(passed=False, evidence="Searched documented roots; component not found.")
        m = self.finish(output)
        self.assertEqual(m["status"], "waiting")
        self.assertEqual(m["failures"], 0)
        self.assertEqual(m["tasks"][0]["status"], "waiting")
        self.assertEqual(m["questions"][-1]["kind"], "criterion")
        attempts = len(m["attempts"])
        self.now += 600
        self.controller.tick()
        self.assertEqual(len(self.current()["attempts"]), attempts)
        self.controller.action({"id":self.key,"action":"pause"})
        revised = self.controller.action({"id":self.key,"action":"revise_task","task":"one",
            "expected_criteria":m["tasks"][0]["criteria"],"criteria":["Finding or documented absence"],
            "reason":"Original inventory scope permits unknowns."})
        self.assertEqual(revised["status"], "paused")
        self.assertEqual(revised["tasks"][0]["status"], "pending")
        self.assertEqual(revised["criteria"], m["criteria"])
        self.assertEqual(revised["evidence"], m["evidence"])
        self.assertEqual(revised["plan_revisions"][0]["before"]["criteria"],m["tasks"][0]["criteria"])
        self.assertIsNotNone(revised["questions"][-1]["answer"])
        with self.assertRaisesRegex(ValueError,"meanwhile"):
            self.controller.action({"id":self.key,"action":"revise_task","task":"one",
                "expected_criteria":m["tasks"][0]["criteria"],"criteria":["changed again"],"reason":"stale"})

    def test_task_revision_cannot_edit_active_or_reviewed_work(self):
        self.begin_build()
        body={"id":self.key,"action":"revise_task","task":"one","expected_criteria":["File contains OK."],"criteria":["New"],"reason":"Reason"}
        with self.assertRaises(ValueError):self.controller.action(body)
        self.finish(report())
        self.controller.action({"id":self.key,"action":"pause"})
        self.controller.tick()
        with self.assertRaises(ValueError):self.controller.action(body)

    def test_review_changes_return_to_builder(self):
        self.begin_build()
        self.finish(report())
        m = self.finish({"status": "changes", "summary": "Fix formatting error."})
        self.assertEqual(m["attempts"][-1]["phase"], "build")
        self.assertEqual(m["tasks"][0]["feedback"], "Fix formatting error.")
        self.assertEqual(m["tasks"][0]["cycles"], 1)

    def test_unanswered_task_does_not_block_independent_work(self):
        self.begin_build([plan_task("one"), plan_task("two"), plan_task("three", ["one"])])
        m = self.finish({"status": "blocked", "questions": [{"question": "What color?", "reason": "Label is not specified."}]})
        self.assertEqual(m["attempts"][-1]["task"], "two")
        self.assertEqual(m["tasks"][0]["status"], "waiting")
        q = m["questions"][0]
        restored = Missions(self.studio, clock=lambda: self.now)
        restored.action({"id": self.key, "action": "answer", "question": q["id"], "answer": "Green"})
        self.assertEqual(self.current()["tasks"][0]["status"], "pending")
        self.assertEqual(self.current()["questions"][0]["answer"], "Green")

    def test_initial_questions_require_answers_and_plan_confirmation(self):
        self.start()
        m = self.finish({"status": "plan", "tasks": [plan_task()], "questions": [{"question": "Target user?", "reason": "Task brief is missing."}]})
        self.assertEqual(m["status"], "waiting")
        self.controller.tick()
        self.assertEqual(len(self.launched), 1)
        self.controller.action({"id": self.key, "action": "answer", "question": m["questions"][0]["id"], "answer": "Manager"})
        self.assertEqual(self.current()["status"], "awaiting_plan")
        self.controller.tick()
        self.assertEqual(len(self.launched), 1)

    def test_blocked_planner_without_plan_replans_after_answer(self):
        self.start()
        m = self.finish({"status": "blocked", "questions": [{"question": "Which output?", "reason": "The scope is unclear."}]})
        self.controller.action({"id": self.key, "action": "answer", "question": m["questions"][0]["id"], "answer": "File"})
        self.controller.tick()
        self.assertEqual(self.current()["attempts"][-1]["phase"], "plan")
        self.assertEqual(len(self.launched), 2)

    def test_restart_retries_interrupted_attempt_without_duplicate_launch(self):
        m = self.start()
        key = m["active_attempt"]
        restored_studio = Studio(self.project, self.root / "state", self.config)
        self.assertEqual(restored_studio.runs[key]["status"], "interrupted")
        self.studio.runs = restored_studio.runs
        self.controller.tick()
        self.assertEqual(len(self.launched), 1)
        self.now += 31
        self.controller.tick()
        self.controller.tick()
        self.assertEqual(len(self.launched), 2)
        self.assertNotEqual(self.current()["active_attempt"], key)

    def test_reserved_attempt_without_a_process_recovers(self):
        self.start()
        self.studio.runs.clear()
        self.controller.tick()
        self.assertIsNone(self.current()["active_attempt"])
        self.now += 31
        self.controller.tick()
        self.assertEqual(len(self.launched), 2)

    def test_three_invalid_reports_stop_retry_loop(self):
        self.start()
        for _ in range(3):
            m = self.finish({"status": "plan", "tasks": []})
            self.now += 121
            self.controller.tick()
        self.assertEqual(m["status"], "blocked")
        self.assertEqual(len(self.launched), 3)

    def test_pause_resume_and_cancel_preserve_artifacts(self):
        self.begin_build()
        self.controller.action({"id": self.key, "action": "pause"})
        self.controller.tick()
        self.assertEqual(self.current()["status"], "paused")
        self.assertEqual((self.project / "deliverable.txt").read_text(), "OK")
        self.controller.action({"id": self.key, "action": "resume"})
        self.controller.tick()
        self.assertEqual(len(self.launched), 3)
        self.controller.action({"id": self.key, "action": "cancel"})
        self.controller.tick()
        self.assertEqual(self.current()["status"], "cancelled")
        self.assertEqual(len(self.launched), 3)

    def test_seven_day_limit_expires_without_wall_clock_wait(self):
        self.start(days=7)
        self.now += 7 * 86400
        self.controller.tick()
        self.assertEqual(self.current()["status"], "expired")
        self.assertEqual(len(self.launched), 1)

    def test_attempt_timeout_stops_worker_and_backs_off(self):
        self.begin_build()
        m = self.current()
        a = m["attempts"][-1]
        # The runner ends a bounded attempt itself; the controller kill is a last resort after a grace period.
        self.now += a["budget_seconds"] + 1
        self.controller.tick()
        self.assertEqual(self.studio.runs[m["active_attempt"]]["status"], "running")
        self.now += 120
        self.controller.tick()
        self.controller.tick()
        self.assertEqual(self.studio.runs[m["active_attempt"]]["status"], "cancelled")
        m = self.current()
        self.assertGreater(m["retry_at"], self.now)
        self.assertEqual(m["attempts"][-1]["failure_kind"], "time_limit")
        self.assertEqual((m["status"], m["failures"]), ("running", 1))

    def test_human_approval_wait_does_not_consume_worker_budget(self):
        m = self.start(attempt_minutes=1)
        run = m['active_attempt']
        directory = self.studio.run_dir(run)
        approval = directory / ('approval-' + 'a' * 32 + '.json')
        self.now += 10
        approval.write_text('{}')
        os.utime(approval, (self.now, self.now))
        self.now += 3600
        self.controller.tick()
        self.assertEqual(self.studio.runs[run]['status'], 'running')
        decision = directory / approval.name.replace('approval-', 'decision-')
        decision.write_text('{"allow":true}')
        os.utime(decision, (self.now, self.now))
        budget = self.current()['attempts'][-1]['budget_seconds']
        self.now += budget + 120
        self.controller.tick()
        self.controller.tick()
        m = self.current()
        self.assertEqual(self.studio.runs[run]['status'], 'cancelled')
        # A first plan time limit retries once with a larger budget instead of blocking.
        self.assertEqual(m['status'], 'running')
        self.assertIn('larger budget', m['message'])

    def test_overlapping_approval_waits_are_not_counted_twice(self):
        m = self.start()
        run = m['active_attempt']
        directory = self.studio.run_dir(run)
        for name, start, end in [('a', 1010, 1040), ('b', 1020, 1060)]:
            for prefix, stamp in [('approval', start), ('decision', end)]:
                path = directory / f'{prefix}-{name * 32}.json'
                path.write_text('{}')
                os.utime(path, (stamp, stamp))
        self.assertEqual(self.studio.approval_wait_seconds(run, 1100), 50)

    def test_attempt_budget_stops_launching(self):
        self.begin_build()
        m = self.current()
        m["max_attempts"] = 2
        self.controller.save(m)
        m = self.finish(report())
        self.assertEqual(m["status"], "blocked")
        self.assertEqual(len(self.launched), 2)

    def test_report_cannot_reference_secret_symlink_or_itself(self):
        self.start()
        (self.project / "escape").symlink_to(self.root)
        m = self.current()
        for path in [".env", "../agent.toml", "escape/agent.toml", "company/projects/report.json"]:
            with self.subTest(path=path), self.assertRaises(Exception):
                self.controller.artifacts(m, [path])

    def test_plan_rejects_cycle_unknown_dependency_and_duplicate_ids(self):
        for tasks in [[plan_task("a", ["a"])], [plan_task("a", ["missing"])], [plan_task("a"), plan_task("a")]]:
            with self.assertRaises(ValueError):
                parse_plan({"tasks": tasks})

    def test_other_studio_run_owns_the_only_worker_slot(self):
        m = self.create()
        self.studio.runs["interactive"] = {"status": "running"}
        self.controller.action({"id": m["id"], "action": "start"})
        self.controller.tick()
        self.assertEqual(self.launched, [])

    def test_closed_database_connections_allow_checkpointing(self):
        self.start()
        for _ in range(20):
            self.controller.tick()
        with self.controller.connect() as db:
            self.assertEqual(db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0], 0)

    def test_missing_search_is_visible_without_exposing_credentials(self):
        with patch.dict("os.environ", {"SERPER_API_KEY": ""}):
            self.assertFalse(self.controller.capabilities()["web_search_configured"])
        with patch.dict("os.environ", {"SERPER_API_KEY": "secret-test"}):
            result = self.controller.capabilities()
            self.assertTrue(result["web_search_configured"])
            self.assertNotIn("secret-test", json.dumps(result))

    def test_binary_product_artifacts_can_be_verified(self):
        self.start()
        (self.project / "image.png").write_bytes(b"\x89PNG\0\xfftest")
        checked = self.controller.artifacts(self.current(), ["image.png"])
        self.assertEqual(checked[0]["path"], "image.png")
        self.assertEqual(len(checked[0]["sha256"]), 64)

    def test_answer_while_paused_unblocks_on_resume(self):
        self.start()
        m = self.finish({"status": "blocked", "questions": [{"question": "Which output?", "reason": "The scope is unclear."}]})
        self.controller.action({"id": self.key, "action": "pause"})
        self.controller.action({"id": self.key, "action": "answer", "question": m["questions"][0]["id"], "answer": "File"})
        self.assertEqual(self.current()["status"], "paused")
        self.controller.action({"id": self.key, "action": "resume"})
        self.controller.tick()
        self.assertEqual(len(self.launched), 2)

    # Production failure modes: each must recover in a bounded, visible way.

    def end_run(self, status, **fields):
        m = self.current()
        run = self.studio.runs[m["active_attempt"]]
        run.update(status=status, **fields)
        atomic_json(self.studio.run_dir(run["id"]) / "run.json", run)
        self.controller.tick()
        return self.current()

    def test_studio_restarts_never_count_as_failures_or_runs(self):
        m = self.start()
        for restart in range(3):
            m = self.end_run("interrupted")
            self.assertEqual((m["status"], m["failures"], used_runs(m)), ("running", 0, 0), m["message"])
            self.assertTrue(m["attempts"][-1]["refunded"])
            self.assertIn("does not count", m["message"])
            self.now = m["retry_at"]
            self.controller.tick()
        self.assertEqual(len(self.launched), 4)
        self.assertEqual(self.current()["used_runs"], 1)  # only the attempt that is running now
        # Restart and shutdown errors are not the model's fault and stay out of the next prompt.
        self.assertNotIn("Studio was restarted", self.launched[-1][0]["task"].split("Errors from previous attempts")[1][:200])

    def test_interruptions_are_bounded(self):
        self.start()
        for _ in range(7):
            m = self.end_run("interrupted")
            if m["status"] == "blocked":
                break
            self.now = m["retry_at"]
            self.controller.tick()
        self.assertEqual((m["status"], m["blocked_kind"]), ("blocked", "interrupted"))
        self.assertEqual(m["interruptions"], 7)
        self.assertEqual(m["questions"][-1]["kind"], "failure")

    def test_provider_503_loading_model_waits_without_using_runs(self):
        self.start()
        delays = []
        for _ in range(12):
            before = self.now
            m = self.end_run("failed", reason="Provider/model: local/qwen\nReason: exhausted\n"
                                              "Provider response: Error code: 503 - {'error': 'Loading model'}")
            self.assertEqual((m["status"], m["failures"], used_runs(m)), ("running", 0, 0), m["message"])
            self.assertIn("Waiting for the model server", m["message"])
            delays.append(m["retry_at"] - before)
            self.now = m["retry_at"]
            self.controller.tick()
        self.assertEqual(delays[:5], [60, 120, 240, 480, 900])
        self.assertEqual(max(delays), 900)
        m = self.end_run("failed", reason="Error code: 503 Loading model")
        self.assertEqual((m["status"], m["blocked_kind"]), ("blocked", "provider_unavailable"))
        self.assertEqual(used_runs(m), 0)

    def test_runner_failure_kind_wins_over_reason_text(self):
        self.start()
        m = self.end_run("failed", reason="Connection refused", failure_kind="setup")
        self.assertEqual((m["failures"], m["attempts"][-1]["failure_kind"]), (1, "setup"))
        self.assertFalse(m["attempts"][-1].get("refunded"))
        self.assertEqual(failure_kind("Run exceeded time limit."), "time_limit")
        self.assertEqual(failure_kind("max_turns"), "max_turns")
        self.assertEqual(failure_kind("progress_guard: Repetition without progress."), "progress_guard")
        self.assertEqual(failure_kind("Invalid output: Report does not cover all criteria: 'time limit is 2s'"), "invalid_output")
        self.assertEqual(failure_kind("Invalid output: No phase report was saved; call save_mission_report before finishing."), "no_report")
        self.assertEqual(failure_kind("Error code: 401 invalid key"), "")

    def test_crashed_worker_reports_a_controller_reason_not_a_false_resume(self):
        self.start()
        m = self.end_run("failed", exit_code=1)
        self.assertIn("Worker exited without a result (exit code 1)", m["message"])
        self.assertNotIn("Interrupted run", m["message"])
        self.assertEqual((m["failures"], m["attempts"][-1]["failure_kind"]), (1, "crash"))

    def test_time_limit_in_review_retries_once_with_larger_budget_then_blocks(self):
        self.begin_build()
        self.finish(report())
        m = self.current()
        first = m["attempts"][-1]
        self.assertEqual(first["phase"], "review")
        self.now += first["budget_seconds"] + 200
        self.controller.tick()
        m = self.end_run("cancelled")
        self.assertEqual((m["status"], m["failures"]), ("running", 1))
        self.assertIn("larger budget", m["message"])
        self.now = m["retry_at"]
        self.controller.tick()
        second = self.current()["attempts"][-1]
        self.assertEqual(second["phase"], "review")
        self.assertEqual(second["budget_seconds"], int(first["budget_seconds"] * 1.5))
        self.assertEqual(self.launched[-1][1]["max_turns"], self.launched[-2][1]["max_turns"] + 8)
        m = self.end_run("incomplete", reason="Run exceeded time limit.", failure_kind="time_limit")
        self.assertEqual((m["status"], m["blocked_kind"]), ("blocked", "time_limit"))
        self.assertEqual(sum(q["answer"] is None for q in m["questions"]), 1)

    def test_max_turns_in_build_backs_off_and_blocks_after_three(self):
        self.begin_build()
        for expected in (1, 2):
            m = self.end_run("incomplete", reason="max_turns", failure_kind="max_turns")
            self.assertEqual((m["status"], m["failures"]), ("running", expected))
            self.assertEqual(m["retry_at"] - self.now, 30 * 2 ** (expected - 1))
            self.now = m["retry_at"]
            self.controller.tick()
        m = self.end_run("incomplete", reason="max_turns", failure_kind="max_turns")
        self.assertEqual((m["status"], m["blocked_kind"]), ("blocked", "max_turns"))
        self.assertEqual(used_runs(m), 4)

    def test_progress_guard_in_plan_retries_in_save_only_mode(self):
        self.start()
        m = self.end_run("incomplete", reason="progress_guard: Repetition without progress.", failure_kind="progress_guard")
        self.assertEqual(m["status"], "running")
        self.now = m["retry_at"]
        self.controller.tick()
        self.assertTrue(self.launched[-1][1].get("save_only"))
        self.assertEqual(self.current()["attempts"][-1]["budget_seconds"], int(max(1800, 600) * 1.5))

    def test_no_saved_report_is_named_in_the_next_prompt(self):
        self.start()
        a = self.current()["attempts"][-1]
        self.studio.runs[a["id"]]["status"] = "completed"
        self.controller.tick()
        m = self.current()
        self.assertIn("No phase report was saved", m["message"])
        self.assertEqual(m["attempts"][-1]["failure_kind"], "no_report")
        self.now = m["retry_at"]
        self.controller.tick()
        self.assertIn("No phase report was saved", self.launched[-1][0]["task"])

    def test_output_paths_accept_workspace_alias_and_name_missing_files(self):
        self.start()
        (self.project / "docs").mkdir()
        (self.project / "docs" / "product.txt").write_text("OK")
        m = self.current()
        checked = self.controller.artifacts(m, ["/workspace/docs/product.txt", str(self.project) + "/docs/product.txt",
                                               "./docs/product.txt"])
        self.assertEqual([x["path"] for x in checked], ["docs/product.txt"])
        with self.assertRaisesRegex(ValueError, "Output file not found: docs/missing.txt"):
            self.controller.artifacts(m, ["docs/missing.txt"])

    def test_build_report_with_workspace_paths_is_accepted(self):
        self.begin_build()
        output = report()
        output["artifacts"] = ["/workspace/deliverable.txt"]
        m = self.finish(output)
        self.assertEqual(m["tasks"][0]["status"], "review", m["message"])
        self.assertEqual(m["tasks"][0]["artifacts"][0]["path"], "deliverable.txt")

    def test_complex_brief_requires_readiness_in_run_and_in_controller(self):
        from studio.mission_report import MissionReport
        criteria = [f"Criterion {i} is documented." for i in range(5)]
        self.start(criteria=criteria)
        m = self.current()
        self.assertTrue(m["readiness"]["semantic_required"])
        meta = self.launched[-1][1]
        self.assertTrue(meta["readiness_required"])
        self.assertIn('"readiness"', self.launched[-1][0]["task"])
        plan = {"status": "plan", "tasks": [plan_task()], "questions": []}
        in_run = MissionReport({"mission": meta, "cwd": str(self.project)}, Path("/fixed/report.json"))
        with self.assertRaisesRegex(ValueError, "readiness assessment") as tool_error:
            in_run.validate(plan)
        m = self.finish(plan)
        self.assertIn(str(tool_error.exception), m["message"])
        self.now = m["retry_at"]
        self.controller.tick()
        plan["readiness"] = {"status": "ready", "reason": "Scope and criteria agree."}
        self.assertEqual(in_run.validate(plan)["readiness"]["status"], "ready")
        m = self.finish(plan)
        self.assertEqual(m["status"], "awaiting_plan")
        self.assertEqual(m["readiness"]["semantic_status"], "ready")

    def test_in_run_tool_rejects_what_the_controller_rejects_for_build(self):
        from studio.mission_report import MissionReport
        self.begin_build()
        meta = self.launched[-1][1]
        self.assertEqual(meta["criteria"], ["File contains OK."])
        in_run = MissionReport({"mission": meta, "cwd": str(self.project)}, Path("/fixed/report.json"))
        paraphrased = report(criterion="The file has OK in it.")
        with self.assertRaisesRegex(ValueError, "Missing precise wording"):
            in_run.validate(paraphrased)
        missing = report()
        missing["artifacts"] = ["nowhere.txt"]
        with self.assertRaisesRegex(ValueError, "Output file not found: nowhere.txt"):
            in_run.validate(missing)
        self.assertEqual(in_run.validate(report())["artifacts"], ["deliverable.txt"])
        m = self.finish(paraphrased)
        self.assertIn("Missing precise wording", m["message"])

    def test_prompts_require_english_output(self):
        from studio.mission_report import ENGLISH
        self.begin_build()
        self.finish(report())
        self.assertTrue(all(ENGLISH in body["task"] for body, _ in self.launched))

    def test_interrupted_checks_rerun_without_counting_a_round(self):
        m = self.create()
        self.key = m["id"]
        self.controller.action({"id": self.key, "action": "start"})
        m = self.current()
        record = {"id": "c" * 16, "mission": m["id"], "status": "cancelled", "started": 1, "ended": 2,
                  "error": "Verification was paused.", "checks": []}
        self.controller.verifications.save(record)
        m.update(status="verifying", verification_id=record["id"], verification_stage="before_final_review",
                 tasks=parse_plan({"tasks": [plan_task()]}))
        m["tasks"][0]["status"] = "done"
        self.controller.save(m)
        started = []
        def fake_start(mission):
            started.append(mission["id"])
            self.controller.verifications.save({**record, "id": "d" * 16, "status": "running"})
            return "d" * 16
        with patch.object(self.controller.verifications, "start", side_effect=fake_start):
            self.controller.tick()
            m = self.current()
            self.assertEqual((m["status"], m["final_cycles"], m["tasks"][0]["status"]), ("verifying", 0, "done"))
            self.assertIn("re-running", m["message"])
            self.controller.tick()
        self.assertEqual(started, [m["id"]])
        self.assertEqual(self.current()["verification_id"], "d" * 16)

    def test_revised_task_gets_fresh_correction_rounds(self):
        self.begin_build()
        for _ in range(3):
            self.finish(report())
            m = self.finish({"status": "changes", "summary": "Fix formatting error."})
        self.assertEqual((m["status"], m["blocked_kind"]), ("blocked", "review_rounds"))
        revised = self.controller.action({"id": self.key, "action": "revise_task", "task": "one",
            "expected_criteria": m["tasks"][0]["criteria"], "criteria": ["File contains OK or ok."], "reason": "Accept both cases."})
        self.assertEqual((revised["tasks"][0]["cycles"], revised["tasks"][0]["cycles_total"]), (0, 3))
        self.assertEqual(revised["plan_revisions"][-1]["cycles_before"], 3)

    def test_answering_the_block_question_resumes_unless_declined(self):
        self.start()
        m = self.end_run("incomplete", reason="Run exceeded time limit.", failure_kind="time_limit")
        self.now = m["retry_at"]
        self.controller.tick()
        m = self.end_run("incomplete", reason="Run exceeded time limit.", failure_kind="time_limit")
        self.assertEqual(m["status"], "blocked")
        question = next(q for q in m["questions"] if q["answer"] is None)
        self.assertEqual(question["kind"], "failure")
        m = self.controller.action({"id": self.key, "action": "answer", "question": question["id"], "answer": "No, wait."})
        self.assertEqual(m["status"], "blocked")
        m = self.controller.action({"id": self.key, "action": "resume"})
        self.assertEqual((m["status"], m["failures"], m.get("blocked_kind")), ("running", 0, None))
        self.assertIsNotNone(question["id"])
        self.assertEqual(next(q for q in m["questions"] if q["id"] == question["id"])["answer"], "No, wait.")
        self.controller.tick()
        m = self.end_run("incomplete", reason="max_turns", failure_kind="max_turns")
        self.now = m["retry_at"]
        self.controller.tick()
        m = self.end_run("incomplete", reason="max_turns", failure_kind="max_turns")
        question = next(q for q in m["questions"] if q["answer"] is None)
        m = self.controller.action({"id": self.key, "action": "answer", "question": question["id"], "answer": "Continue."})
        self.assertEqual(m["status"], "running", m["message"])

    def test_only_an_explicit_continue_answer_resumes_a_block(self):
        for answer in ("Yes", "y", "OK", "Continue.", "Continue with the current limits.", "Yes, continue.",
                       "Please retry", "Go ahead", "resume"):
            self.assertTrue(continue_answer(answer), answer)
        for answer in ("No", "No, wait.", "Not yet", "Not now", "Nope", "Never", "Later", "Please wait",
                       "not until I raise limits", "Don't", "Continue later", "Yes, but wait for my approval",
                       "Continue, but do not touch the tests", "Why did it fail?", "Use the smaller dataset", ""):
            self.assertFalse(continue_answer(answer), answer)
        m = self.start()
        self.studio.runs[m["active_attempt"]]["status"] = "cancelled"
        m["active_attempt"] = None
        self.controller.block(m, "time_limit", "Run exceeded time limit.", question=True)
        self.controller.save(m)
        question = next(q for q in self.current()["questions"] if q["answer"] is None)
        self.assertIn("'continue'", question["question"])
        m = self.controller.action({"id": self.key, "action": "answer", "question": question["id"], "answer": "Not yet"})
        self.assertEqual((m["status"], m["blocked_kind"]), ("blocked", "time_limit"))
        self.assertIn("did not say to continue", m["message"])
        # The inbox's "answer and retry" sends guidance with an explicit resume flag.
        self.controller.block(m, "time_limit", "Run exceeded time limit.", question=True)
        self.controller.save(m)
        question = next(q for q in self.current()["questions"] if q["answer"] is None)
        m = self.controller.action({"id": self.key, "action": "answer", "question": question["id"],
                                    "answer": "Use the smaller dataset.", "resume": True})
        self.assertEqual((m["status"], m.get("blocked_kind")), ("running", None), m["message"])

    def test_owner_stop_pauses_without_a_strike_or_an_automatic_relaunch(self):
        self.start()
        m = self.end_run("cancelled")  # Studio.stop() from the run view, not a controller shutdown
        a = m["attempts"][-1]
        self.assertEqual((m["status"], m["resume_status"], m["failures"]), ("paused", "running", 0))
        self.assertEqual((a["stopped_by"], a["outcome"], used_runs(m)), ("owner_stop", "interrupted", 1))
        self.assertFalse(a.get("refunded"))
        self.assertIn("owner stopped the run", m["message"])
        self.now += 3600
        self.controller.tick()
        self.assertEqual((self.current()["status"], len(self.launched)), ("paused", 1))
        m = self.controller.action({"id": self.key, "action": "resume"})
        self.assertEqual(m["status"], "running")
        self.controller.tick()
        self.assertEqual(len(self.launched), 2)

    def test_launch_failure_is_refunded_but_bounded(self):
        self.start()
        self.end_run("interrupted")
        def refuse(body, *, mission):
            raise RuntimeError("Another task is currently running.")
        self.studio.launch = refuse
        for _ in range(3):
            self.now = self.current()["retry_at"]
            self.controller.tick()
        m = self.current()
        self.assertEqual((m["status"], m["blocked_kind"], used_runs(m)), ("blocked", "setup", 0))

    def test_one_broken_mission_does_not_starve_others(self):
        first = self.create()
        second = self.create()
        for m in (first, second):
            self.controller.action({"id": m["id"], "action": "start"})
        original = self.controller.advance
        def advance(m):
            if m["id"] == first["id"]:
                raise RuntimeError("broken mission")
            return original(m)
        with patch.object(self.controller, "advance", side_effect=advance):
            with self.assertRaisesRegex(RuntimeError, "broken mission"):
                self.controller.tick()
        self.assertEqual([meta["id"] for _, meta in self.launched], [second["id"]])

    def test_owner_pause_counts_the_run_but_is_not_a_failure_even_if_resumed_quickly(self):
        self.start()
        self.controller.action({"id": self.key, "action": "pause"})
        self.controller.action({"id": self.key, "action": "resume"})
        self.controller.tick()
        m = self.current()
        # The stopped run counts; the next run starts at once without a failure strike.
        self.assertEqual((m["status"], m["failures"], used_runs(m), len(self.launched)), ("running", 0, 2, 2))
        self.assertEqual((m["attempts"][0]["outcome"], m["attempts"][0]["stopped_by"]), ("interrupted", "pause"))
        self.assertFalse(m["attempts"][0].get("refunded"))
