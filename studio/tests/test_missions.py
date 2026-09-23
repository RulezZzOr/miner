"""Durability, dependency scheduling, evidence gates and restart boundaries."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from studio.missions import Missions, parse_plan
from studio.server import Studio, atomic_json, write_config


def plan_task(key="one", deps=None):
    return {"id": key, "title": key, "instructions": "Vytvoř deliverable.txt a zkontroluj obsah.",
            "depends_on": deps or [], "criteria": ["Soubor obsahuje OK."]}


def report(status="done", criterion="Soubor obsahuje OK."):
    return {"status": status, "summary": "Obsah ověřen.", "artifacts": ["deliverable.txt"],
            "checks": [{"criterion": criterion, "passed": True, "evidence": "read_file: OK"}]}


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
        return self.controller.create({"project": self.pid, "title": "Pilot", "goal": "Vytvoř ověřený soubor.",
            "criteria": ["Produkt lze přečíst."], "profile": "coder", "review_profile": "reviewer", "isolated": False,
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
        with self.assertRaisesRegex(ValueError, "dostupný model"):
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
        return self.finish(report("pass", "Produkt lze přečíst."))

    def test_full_lifecycle_has_separate_review_and_survives_restart(self):
        m = self.deliver()
        self.assertEqual(m["status"], "ready")
        self.assertEqual([a["phase"] for a in m["attempts"]], ["plan", "build", "review", "final"])
        self.assertEqual([b["profile"] for b, _ in self.launched], ["coder", "coder", "reviewer", "reviewer"])
        self.assertEqual([meta["attempt_seconds"] for _, meta in self.launched],
                         [300] + [m["attempt_minutes"] * 60] * 3)
        self.assertEqual(self.launched[0][0]["max_turns"], 8)
        self.assertEqual(len({a["id"] for a in m["attempts"]}), 4)
        self.assertEqual(len(m["evidence"]), 4)
        restored = Missions(self.studio, clock=lambda: self.now)
        result = restored.action({"id": self.key, "action": "accept"})
        self.assertEqual(result["status"], "accepted")

    def test_late_edit_prevents_acceptance(self):
        self.deliver()
        (self.project / "deliverable.txt").write_text("changed")
        with self.assertRaisesRegex(ValueError, "změnil"):
            self.controller.action({"id": self.key, "action": "accept"})
        self.controller.action({"id": self.key, "action": "recheck"})
        self.controller.tick()
        self.assertEqual(self.current()["attempts"][-1]["phase"], "final")

    def test_plan_cannot_schedule_its_own_controller_report_as_product_work(self):
        m = self.start()
        task = plan_task()
        task['instructions'] = 'Zkontroluj report ' + m['active_attempt'] + '.json'
        m = self.finish({'status': 'plan', 'tasks': [task], 'questions': []})
        self.assertEqual(m['tasks'], [])
        self.assertNotEqual(m['status'], 'awaiting_plan')
        self.assertIn('vlastní plánovací report', m['message'])

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
        with self.assertRaisesRegex(ValueError,"mezitím"):
            self.controller.action({"id":self.key,"action":"revise_task","task":"one",
                "expected_criteria":m["tasks"][0]["criteria"],"criteria":["changed again"],"reason":"stale"})

    def test_task_revision_cannot_edit_active_or_reviewed_work(self):
        self.begin_build()
        body={"id":self.key,"action":"revise_task","task":"one","expected_criteria":["Soubor obsahuje OK."],"criteria":["New"],"reason":"Reason"}
        with self.assertRaises(ValueError):self.controller.action(body)
        self.finish(report())
        self.controller.action({"id":self.key,"action":"pause"})
        self.controller.tick()
        with self.assertRaises(ValueError):self.controller.action(body)

    def test_review_changes_return_to_builder(self):
        self.begin_build()
        self.finish(report())
        m = self.finish({"status": "changes", "summary": "Oprav chybu formátu."})
        self.assertEqual(m["attempts"][-1]["phase"], "build")
        self.assertEqual(m["tasks"][0]["feedback"], "Oprav chybu formátu.")
        self.assertEqual(m["tasks"][0]["cycles"], 1)

    def test_unanswered_task_does_not_block_independent_work(self):
        self.begin_build([plan_task("one"), plan_task("two"), plan_task("three", ["one"])])
        m = self.finish({"status": "blocked", "questions": [{"question": "Jaká barva?", "reason": "Značka není určená."}]})
        self.assertEqual(m["attempts"][-1]["task"], "two")
        self.assertEqual(m["tasks"][0]["status"], "waiting")
        q = m["questions"][0]
        restored = Missions(self.studio, clock=lambda: self.now)
        restored.action({"id": self.key, "action": "answer", "question": q["id"], "answer": "Zelená"})
        self.assertEqual(self.current()["tasks"][0]["status"], "pending")
        self.assertEqual(self.current()["questions"][0]["answer"], "Zelená")

    def test_initial_questions_require_answers_and_plan_confirmation(self):
        self.start()
        m = self.finish({"status": "plan", "tasks": [plan_task()], "questions": [{"question": "Cílový uživatel?", "reason": "Chybí zadání."}]})
        self.assertEqual(m["status"], "waiting")
        self.controller.tick()
        self.assertEqual(len(self.launched), 1)
        self.controller.action({"id": self.key, "action": "answer", "question": m["questions"][0]["id"], "answer": "Správce"})
        self.assertEqual(self.current()["status"], "awaiting_plan")
        self.controller.tick()
        self.assertEqual(len(self.launched), 1)

    def test_blocked_planner_without_plan_replans_after_answer(self):
        self.start()
        m = self.finish({"status": "blocked", "questions": [{"question": "Co?", "reason": "Rozsah."}]})
        self.controller.action({"id": self.key, "action": "answer", "question": m["questions"][0]["id"], "answer": "Soubor"})
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
        self.now += m["attempt_minutes"] * 60 + 1
        self.controller.tick()
        self.controller.tick()
        self.assertEqual(self.studio.runs[m["active_attempt"]]["status"], "cancelled")
        self.assertGreater(self.current()["retry_at"], self.now)

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
        self.now += 51
        self.controller.tick()
        self.controller.tick()
        self.assertEqual(self.current()['status'], 'blocked')

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
        m = self.finish({"status": "blocked", "questions": [{"question": "Co?", "reason": "Rozsah."}]})
        self.controller.action({"id": self.key, "action": "pause"})
        self.controller.action({"id": self.key, "action": "answer", "question": m["questions"][0]["id"], "answer": "Soubor"})
        self.assertEqual(self.current()["status"], "paused")
        self.controller.action({"id": self.key, "action": "resume"})
        self.controller.tick()
        self.assertEqual(len(self.launched), 2)
