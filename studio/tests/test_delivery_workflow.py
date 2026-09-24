"""Readiness, resumption, evidence freshness and conflict-safe note regression tests."""
import json
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from studio.tests import test_missions as helpers
from studio.workflow import readiness, process_policy, handoff
from studio.delivery import sync_notes, note_updates
from studio.test_evidence import test_count


class WorkflowTests(unittest.TestCase):
    setUp = helpers.MissionTests.setUp
    launch = helpers.MissionTests.launch
    stop = helpers.MissionTests.stop
    create = helpers.MissionTests.create
    start = helpers.MissionTests.start
    current = helpers.MissionTests.current
    finish = helpers.MissionTests.finish
    begin_build = helpers.MissionTests.begin_build
    deliver = helpers.MissionTests.deliver

    def test_conflicting_brief_never_starts_worker_and_revision_is_required(self):
        m = self.start(goal="Read-only audit of the server.", criteria=["All integrations must be functional."])
        self.assertEqual(m["status"], "blocked")
        self.assertEqual(self.launched, [])
        self.assertEqual(len(m["questions"]), 1)
        self.controller.action({"id": m["id"], "action": "resume"})
        self.controller.tick()
        self.assertEqual(len(self.current()["questions"]), 1)
        with self.assertRaisesRegex(ValueError, "changed meanwhile"):
            self.controller.action({"id": m["id"], "action": "revise_brief", "expected_brief_hash": "stale"})
        revised = self.controller.action({"id": m["id"], "action": "revise_brief",
            "expected_brief_hash": m["readiness"]["brief_hash"], "reason": "Audit may report unknown services.",
            "criteria": ["Save the inventory and document unknown services."]})
        self.assertEqual(revised["readiness"]["status"], "ready")
        self.assertEqual(revised["brief_revisions"][0]["before"]["criteria"], m["criteria"])
        self.controller.action({"id": m["id"], "action": "resume"})
        self.controller.tick()
        self.assertEqual(len(self.launched), 1)

    def test_complex_brief_requires_explicit_planning_assessment(self):
        m = self.start(goal="Change authentication to support a new identity provider.")
        self.assertTrue(m["readiness"]["semantic_required"])
        m = self.finish({"status": "plan", "tasks": [helpers.plan_task()], "questions": []})
        self.assertFalse(m["tasks"])
        self.assertIn("readiness assessment", m["attempts"][-1]["error"])

    def test_sensitive_scope_cannot_downgrade_and_path_changes_escalate(self):
        for goal in ("Change OAuth authentication", "Database schema migration", "Update production firewall"):
            self.assertEqual(process_policy({"goal": goal, "criteria": ["Verified"], "process_mode": "light"})["effective"], "sensitive")
        m = self.create(goal="Fix README spelling", process_mode="light")
        self.assertEqual(m["process"]["effective"], "light")
        self.assertEqual(process_policy(m, ["src/auth/login.py"])["effective"], "sensitive")
        m["process"] = process_policy(m, ["migrations/001.sql"])
        self.assertEqual(process_policy(m)["effective"], "sensitive")

    def test_light_process_keeps_reviewer_and_smaller_worker_budget(self):
        self.start(goal="Fix README spelling", process_mode="light")
        self.finish({"status": "plan", "tasks": [helpers.plan_task()], "questions": []})
        self.controller.action({"id": self.key, "action": "approve_plan"})
        self.controller.tick()
        self.assertEqual(self.launched[-1][0]["max_turns"], 16)
        self.assertEqual(self.launched[-1][1]["attempt_seconds"], 600)
        (self.project / "deliverable.txt").write_text("OK")
        self.finish(helpers.report())
        self.assertEqual(self.launched[-1][0]["profile"], "reviewer")
        self.assertEqual(self.launched[-1][1]["phase"], "review")

    def test_handoff_flags_changed_files_and_remains_bounded(self):
        m = self.create(process_mode="light")
        m["tasks"] = [{"id": str(i), "title": "File " + str(i), "status": "done", "artifacts": [{"path": f"file-{i}.md", "sha256": "a" * 64}]} for i in range(40)]
        result = handoff(m, {})
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False).encode()), 4500)
        self.assertTrue(result["omitted_references"])
        # Small packets retain precise revisions; changed files cannot be assumed already read.
        m["tasks"] = m["tasks"][:1]
        result = handoff(m, {"file-0.md": "b" * 64})
        self.assertEqual(result["references"][0]["state"], "changed_read_again")

    def test_acceptance_notes_are_idempotent_and_evidence_stales_after_source_edit(self):
        self.deliver()
        m = self.controller.action({"id": self.key, "action": "accept"})
        self.assertEqual(m["notes_sync"]["status"], "complete")
        text = (self.project / "notes/NOTES.md").read_text()
        self.assertIn("Accepted-change description", text)
        self.assertIn("> Content verified\\.", text)
        self.assertIn("[deliverable\\.txt](../deliverable.txt)", text)
        self.assertIn(m["final_report"]["verified_artifacts"][0]["sha256"], text)
        self.assertIn("not a live deployment claim", text)
        self.controller.tick()
        self.controller.action({"id": self.key, "action": "sync_notes"})
        self.assertEqual((self.project / "notes/NOTES.md").read_text(), text)
        self.assertEqual(self.controller.delivery(self.key)["freshness"], "current")
        self.controller.verify_delivery(self.current())
        (self.project / "unrelated.py").write_text("print('changed')")
        card = self.controller.delivery(self.key)
        self.assertEqual(card["freshness"], "stale")
        self.assertIn("unrelated.py", card["changed_files"])

    def test_notes_quote_model_markup_and_bound_summary_and_output_references(self):
        self.deliver()
        m = self.controller.action({"id": self.key, "action": "accept"})
        m["id"] = "new-acceptance-fixture"
        m["final_report"]["summary"] = "![tracker](https://example.invalid/image)\n<script>bad</script>\n" + "ž" * 2000
        m["final_report"]["verified_artifacts"] = [
            {"path": "output [x](y)#.md", "sha256": "a" * 64} for _ in range(26)]
        base = self.controller.versions.snapshot(self.project, label="Notes escaping fixture")
        text = note_updates(self.controller, m, base)["notes/NOTES.md"]
        self.assertNotIn("![tracker]", text)
        self.assertNotIn("<script>", text)
        self.assertIn("&lt;script&gt;", text)
        self.assertIn("../output%20%5Bx%5D%28y%29%23.md", text)
        self.assertIn("Summary excerpt truncated", text)
        self.assertIn("2 additional outputs", text)
        self.assertNotIn("ž" * 1200, text)

    def test_concurrent_notes_edit_is_preserved_and_acceptance_remains_recorded(self):
        self.deliver()
        original_apply = self.controller.versions.apply
        def race(root, target, **kwargs):
            (self.project / "PROJECT.md").write_text("Owner edit during notes update\n")
            return original_apply(root, target, **kwargs)
        with patch.object(self.controller.versions, "apply", side_effect=race):
            m = self.controller.action({"id": self.key, "action": "accept"})
        self.assertEqual(m["status"], "accepted")
        self.assertEqual(m["notes_sync"]["status"], "needs_attention")
        self.assertEqual((self.project / "PROJECT.md").read_text(), "Owner edit during notes update\n")
        sync_notes(self.controller, m)
        self.assertEqual(m["notes_sync"]["status"], "needs_attention")

    def test_zero_tests_and_unrecognized_test_output_cannot_pass(self):
        for output in ("Ran 0 tests in 0.001s", "Ran 3 tests in 0.001s\nOK (skipped=3)", "Everything is good"):
            m = self.create(verification_checks=[{"argv": [sys.executable, "-c", "print(" + repr(output) + ")"], "kind": "test"}])
            key = self.controller.verifications.start(m)
            deadline = time.monotonic() + 5
            while self.controller.verifications.get(key)["status"] == "running" and time.monotonic() < deadline:
                time.sleep(.02)
            record = self.controller.verifications.get(key)
            self.assertEqual(record["checks"][0]["exit_code"], 0)
            self.assertEqual(record["status"], "failed")

    def test_explicit_notes_write_restriction_is_respected(self):
        self.deliver()
        m = self.current()
        m["constraints"] = "Do not edit PROJECT.md or notes/ files."
        self.controller.save(m)
        m = self.controller.action({"id": self.key, "action": "accept"})
        self.assertEqual(m["notes_sync"]["status"], "restricted")
        self.assertFalse((self.project / "PROJECT.md").exists())

    def test_git_commit_change_invalidates_same_content_checks(self):
        def git(*args):
            return subprocess.run(["git", "-C", str(self.project), *args], check=True, capture_output=True)
        git("init", "-q")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--allow-empty", "-m", "First")
        self.deliver()
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--allow-empty", "-m", "New revision")
        with self.assertRaisesRegex(ValueError, "Git revision"):
            self.controller.action({"id": self.key, "action": "accept"})

    def test_runner_count_formats(self):
        for log, count in [("Ran 12 tests in 0.5s", 12), ("# tests 90\n# pass 90", 90),
                           ("=== 8 passed, 2 skipped in 0.50s ===", 8), ("Tests: 2 skipped, 5 passed, 7 total", 5),
                           ("no tests ran in 0.00s", 0), ("ok", None)]:
            self.assertEqual(test_count(log), count)
