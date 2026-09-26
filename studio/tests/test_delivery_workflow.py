"""Readiness, resumption, evidence freshness and conflict-safe note regression tests."""
import json
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from studio.tests import test_missions as helpers
from studio.tests.sandbox_support import requires_sandbox
from studio.workflow import readiness, process_policy, handoff
from studio.delivery import sync_notes, note_updates, notes_restricted
from studio.test_evidence import test_count

READ_ONLY_INVENTORY = "Read-only inventory of the office network. Record what exists; make no changes."


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

    def test_read_only_audit_that_reports_service_status_is_ready(self):
        for criterion in ("inventory.md lists all services found and states for each whether it is working or not working",
                          "Every integration is documented with its status (working, failing or unknown).",
                          "Explain how to restore each unavailable service.",
                          "Report which services are operational and which are not."):
            with self.subTest(criterion=criterion):
                result = readiness({"goal": READ_ONLY_INVENTORY, "criteria": [criterion]})
                self.assertEqual(result["status"], "ready")
                self.assertFalse(result["questions"])
        # Status wording that could hide an outcome goes to the planner's semantic check instead of blocking.
        self.assertTrue(readiness({"goal": READ_ONLY_INVENTORY, "criteria": ["All services found are working."]})["semantic_required"])

    def test_read_only_scope_with_a_demanded_repair_needs_clarification(self):
        for goal, criterion in (("Read-only audit of the server.", "All integrations must be functional."),
                                ("Audit only; do not change anything.", "Fix the failing mail service."),
                                ("Inspect the server without making any changes.", "Make every service work again."),
                                ("Only inspect the router.", "The VPN service should be up and running."),
                                ("Read only review.", "Restore the backup integration."),
                                ("Inventory the hosts. The work is strictly read-only.", "All endpoints must be reachable."),
                                ("Survey the LAN and change nothing.", "Then fix the DNS service.")):
            with self.subTest(goal=goal, criterion=criterion):
                result = readiness({"goal": goal, "criteria": [criterion]})
                self.assertEqual(result["status"], "clarify")
                self.assertIn(criterion, result["questions"][0]["reason"])
        for constraints, criterion in (("Read-only.", "The backup service must be restored."),
                                       ("Scope: read-only; make no changes.", "The sync service is restored.")):
            with self.subTest(constraints=constraints):
                self.assertEqual(readiness({"goal": "Check the NAS.", "constraints": constraints,
                                            "criteria": [criterion]})["status"], "clarify")
        # A narrow file restriction is not a read-only scope.
        self.assertEqual(readiness({"goal": "Repair the API. Do not modify PROJECT.md.",
                                    "criteria": ["All services must work."]})["status"], "ready")

    def test_narrow_restrictions_and_negated_criteria_do_not_block(self):
        company = "Read PROJECT.md. Local deliverables only; read-only remote discovery."
        for goal, constraints, criterion in (
                ("Build the order API.", company, "The order API must be running locally on port 8080 and pass its tests."),
                ("Fix sync.", company, "Fix the failing sync endpoint handler so the unit tests pass."),
                ("Prepare readiness.", company, "Make the local readiness service work with the sample data."),
                ("Build the dashboard.", "Use read-only access to production.", "The dashboard service must be running."),
                ("Repair the payment API. Make no changes to PROJECT.md.", "", "The payment API must work again."),
                ("Fix the login service without making changes to the database schema.", "", "The login service must work again."),
                ("Refactor the service. Do not modify anything outside src/.", "", "The service must work."),
                ("Implement the API and only document the deprecated endpoints.", "", "The API must be running.")):
            with self.subTest(goal=goal, criterion=criterion):
                result = readiness({"goal": goal, "constraints": constraints, "criteria": [criterion]})
                self.assertEqual(result["status"], "ready")
                self.assertFalse(result["questions"])
        # Read-only wording that qualifies one activity is uncertain: the planner assesses it.
        self.assertTrue(readiness({"goal": "Build the order API.", "constraints": company,
                                   "criteria": ["The order API must be running locally."]})["semantic_required"])
        for criterion in ("Keep all services running during the audit (no restarts).",
                          "Never attempt to repair a service.",
                          "The audit must not fix or reconfigure any service.",
                          "Do not restart anything, and never fix a service without owner approval."):
            with self.subTest(criterion=criterion):
                result = readiness({"goal": "Read-only audit of the office network.", "criteria": [criterion]})
                self.assertEqual(result["status"], "ready")
                self.assertFalse(result["semantic_required"])

    def test_sensitive_scope_uses_english_vocabulary(self):
        for goal in ("Rotate the database passwords", "Migrate customer databases", "Review user permissions",
                     "Change the payment provider", "Store the passphrase securely"):
            with self.subTest(goal=goal):
                self.assertEqual(process_policy({"goal": goal, "criteria": ["Done"]})["effective"], "sensitive")
        self.assertEqual(process_policy({"goal": "Fix README spelling", "criteria": ["Done"]})["effective"], "light")

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

    def test_handoff_lists_partial_outputs_of_interrupted_attempts(self):
        m = self.create()
        m["tasks"] = [{"id": "store", "title": "Store readiness", "status": "pending", "artifacts": []},
                      {"id": "other", "title": "Other", "status": "pending", "artifacts": []}]
        m["attempts"] = [
            {"id": "a1", "phase": "build", "task": "store", "error": "Run exceeded time limit.",
             "file_changes": [{"path": "store-readiness.py", "before": None, "after": "c" * 64},
                              {"path": "company/projects/x/reports/a1.json", "before": None, "after": "d" * 64},
                              {"path": "removed.txt", "before": "e" * 64, "after": None}]},
            {"id": "a2", "phase": "build", "task": "other", "error": "max_turns",
             "file_changes": [{"path": "other.md", "before": None, "after": "f" * 64}]}]
        manifest = {"store-readiness.py": "c" * 64, "company/projects/x/reports/a1.json": "d" * 64, "other.md": "f" * 64}
        result = handoff(m, manifest, task_id="store")
        self.assertEqual([p["path"] for p in result["partial_outputs"]], ["store-readiness.py"])
        self.assertEqual(result["partial_outputs"][0]["state"], "unchanged")
        self.assertEqual(result["partial_outputs"][0]["attempt"], "a1")
        self.assertIn("do not recreate them", result["partial_outputs_instruction"])
        self.assertIn("ran out of time", result["recovery_guidance"])
        # Without a selected task every unfinished task's partial work is routed.
        self.assertEqual(len(handoff(m, manifest)["partial_outputs"]), 2)
        self.assertIn("all its turns", handoff(m, manifest)["recovery_guidance"])
        # A reported attempt or a finished task no longer produces partial outputs.
        m["attempts"][0]["outcome"] = "reported"
        self.assertNotIn("partial_outputs", handoff(m, manifest, task_id="store"))
        m["attempts"][0].pop("outcome")
        m["tasks"][0]["status"] = "done"
        self.assertNotIn("partial_outputs", handoff(m, manifest, task_id="store"))
        # The list stays inside the handoff budget.
        m["tasks"][0]["status"] = "pending"
        m["process"] = {"handoff_bytes": 1500}
        m["attempts"][0]["file_changes"] = [{"path": f"part-{i}-" + "x" * 200, "before": None, "after": "c" * 64} for i in range(40)]
        manifest = {c["path"]: "c" * 64 for c in m["attempts"][0]["file_changes"]}
        result = handoff(m, manifest, task_id="store")
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False).encode()), 1500)
        self.assertTrue(result["omitted_partial_outputs"])

    def test_notes_restriction_is_read_from_the_whole_brief(self):
        production = {"goal": "Prepare the store readiness report under reports/. Do not modify PROJECT.md.",
                      "constraints": "Read PROJECT.md. Local deliverables only; read-only remote discovery.",
                      "criteria": ["reports/store.md exists"]}
        self.assertTrue(notes_restricted(production))
        for text in ("Leave PROJECT.md unchanged.", "PROJECT.md must remain untouched.", "Never edit the notes folder.",
                     "No changes to the documentation.", "Don't write to NOTES.md.", "Work without modifying any project files."):
            with self.subTest(text=text):
                self.assertTrue(notes_restricted({"goal": "Build the page.", "constraints": text, "criteria": ["Done"]}))
        self.assertFalse(notes_restricted({"goal": "Build the page. Read PROJECT.md first.", "constraints": "Local only.",
                                           "criteria": ["Page renders"]}))

    @requires_sandbox
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

    @requires_sandbox
    def test_notes_quote_model_markup_and_bound_summary_and_output_references(self):
        self.deliver()
        m = self.controller.action({"id": self.key, "action": "accept"})
        m["id"] = "new-acceptance-fixture"
        m["final_report"]["summary"] = "![tracker](https://example.invalid/image)\n<script>bad</script>\n" + "\u20ac" * 2000
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
        self.assertNotIn("\u20ac" * 1200, text)

    @requires_sandbox
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
        # A retry rebuilds the plan from the current files instead of repeating the stale conflict.
        sync_notes(self.controller, m)
        self.assertEqual(m["notes_sync"]["status"], "complete")
        self.assertEqual(m["notes_sync"]["rebuilds"], 1)
        project = (self.project / "PROJECT.md").read_text()
        self.assertTrue(project.startswith("Owner edit during notes update"))
        self.assertIn("Delivery records", project)
        self.assertEqual(self.controller.get(self.key)["notes_sync"]["status"], "complete")

    @requires_sandbox
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

    @requires_sandbox
    def test_explicit_notes_write_restriction_is_respected(self):
        self.deliver()
        m = self.current()
        m["constraints"] = "Do not edit PROJECT.md or notes/ files."
        self.controller.save(m)
        m = self.controller.action({"id": self.key, "action": "accept"})
        self.assertEqual(m["notes_sync"]["status"], "restricted")
        self.assertFalse((self.project / "PROJECT.md").exists())

    @requires_sandbox
    def test_notes_restriction_in_the_goal_is_respected(self):
        self.deliver()
        m = self.current()
        m["goal"] += " Do not modify PROJECT.md."
        self.controller.save(m)
        m = self.controller.action({"id": self.key, "action": "accept"})
        self.assertEqual(m["notes_sync"]["status"], "restricted")
        self.assertFalse((self.project / "PROJECT.md").exists())
        self.assertFalse((self.project / "notes").exists())

    @requires_sandbox
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
