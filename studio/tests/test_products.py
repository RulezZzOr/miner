"""Product releases, follow-up work, bounded automation and crash consistency."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio.missions import Missions
from studio.products import Products
from studio.server import Studio, write_config
from studio.tests import test_missions as mission_helpers
from studio.tests.test_missions import plan_task, report


class ProductTests(unittest.TestCase):
    # Reuse only the deterministic runner helpers, not the original test methods.
    launch = mission_helpers.MissionTests.launch
    stop = mission_helpers.MissionTests.stop
    current = mission_helpers.MissionTests.current
    finish = mission_helpers.MissionTests.finish

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
        self.now = 1000
        self.controller = Missions(self.studio, clock=lambda: self.now)
        self.addCleanup(self.controller.close)
        self.studio.missions = self.controller
        self.products = Products(self.studio, clock=lambda: self.now)
        self.studio.products = self.products
        self.pid = next(iter(self.studio.projects))
        self.launched = []
        self.studio.launch = self.launch
        self.studio.stop = self.stop

    def create(self):
        return self.products.create({"project": self.pid, "kind": "web", "title": "Demo",
            "goal": "Create product.", "criteria": ["Product is readable."],
            "profile": "coder", "review_profile": "reviewer", "isolated": False,
            "verification_checks": [{"argv": [sys.executable, "-c", "from pathlib import Path; assert Path('deliverable.txt').read_text().startswith('OK')"]}]})

    def ready(self, p, item=None, content="OK"):
        item = item or p["backlog"][0]["id"]
        p = self.products.action({"id": p["id"], "action": "start", "item": item})
        self.key = p["cycles"][-1]["mission"]
        self.controller.tick()
        self.finish({"status": "plan", "tasks": [plan_task()], "questions": []})
        self.controller.action({"id": self.key, "action": "approve_plan"})
        self.controller.tick()
        (self.project / "deliverable.txt").write_text(content)
        self.finish(report())
        self.finish(report("pass"))
        final = report("pass", "Product is readable.")
        final["checks"] = [{"criterion": c, "passed": True, "evidence": "read_file: OK", "outcome": "supported", "issue": "none"}
                           for c in self.current()["criteria"]]
        self.finish(final)
        return self.current()

    def accept(self, p):
        self.controller.action({"id": self.key, "action": "accept"})
        self.products.tick()
        return self.products.get(p["id"])

    def test_two_releases_keep_history_and_regression_requirements(self):
        p = self.create()
        self.assertEqual(self.controller.list(), [])
        self.ready(p)
        self.products.tick()
        self.assertEqual(self.products.get(p["id"])["releases"], [])
        p = self.accept(p)
        original = p["releases"][0]
        p = self.products.action({"id": p["id"], "action": "add", "kind": "feature",
            "title": "Additional features", "goal": "Add feature.", "criteria": ["New feature works."]})
        m = self.ready(p, p["backlog"][-1]["id"], "OK changed")
        self.assertEqual(m["criteria"], ["Product is readable.", "New feature works."])
        self.assertEqual(m["product_context"]["previous_release"], original["mission"])
        p = self.accept(p)
        self.assertEqual(p["releases"][0], original)
        self.assertEqual(len(p["releases"]), 2)
        self.assertNotEqual(p["releases"][0]["artifacts"], p["releases"][1]["artifacts"])
        restored = Products(self.studio, clock=lambda: self.now)
        restored.tick()
        self.assertEqual(restored.get(p["id"])["releases"], p["releases"])

    def test_parent_pause_cannot_be_bypassed_by_mission_resume_or_restart(self):
        p = self.create()
        p = self.products.action({"id": p["id"], "action": "start", "item": p["backlog"][0]["id"]})
        self.key = p["cycles"][0]["mission"]
        self.controller.tick()
        self.products.action({"id": p["id"], "action": "pause"})
        self.controller.tick()
        with self.assertRaisesRegex(ValueError, "parent product"):
            self.controller.action({"id": self.key, "action": "resume"})
        # Reconcile legacy inconsistent state too, not only API transitions.
        m = self.current()
        m["status"] = "running"
        self.controller.save(m)
        Missions(self.studio, clock=lambda: self.now).tick()
        self.assertEqual(self.current()["status"], "paused")
        self.assertEqual(len(self.launched), 1)

    def test_restore_undo_recovers_owner_edits_and_rejects_stale_preview(self):
        p = self.create()
        self.ready(p)
        p = self.accept(p)
        path = self.project / "deliverable.txt"
        path.write_text("owner changes")
        extra = self.project / "owner.txt"
        extra.write_text("extra file")
        preview = self.products.action({"id": p["id"], "action": "restore_preview", "number": 1})
        p = self.products.action({"id": p["id"], "action": "restore", "number": 1,
                                  "revision": preview["revision"]})
        self.assertEqual(path.read_text(), "OK")
        self.assertFalse(extra.exists())
        request = {"id": p["id"], "operation": p["restorations"][-1]["operation"]}
        # Restart preserves both the backup and the capability to undo it.
        self.products = Products(self.studio, clock=lambda: self.now)
        preview = self.products.action({**request, "action": "undo_restore_preview"})
        path.write_text("newer edit")
        with self.assertRaisesRegex(ValueError, "preview"):
            self.products.action({**request, "action": "undo_restore", "revision": preview["revision"]})
        self.assertEqual(path.read_text(), "newer edit")
        with self.assertRaisesRegex(ValueError, "last restore"):
            self.products.action({**request, "operation": "wrong", "action": "undo_restore_preview"})
        preview = self.products.action({**request, "action": "undo_restore_preview"})
        p = self.products.action({**request, "action": "undo_restore", "revision": preview["revision"]})
        self.assertEqual(path.read_text(), "owner changes")
        self.assertEqual(extra.read_text(), "extra file")
        self.assertEqual(p["active_release"], 1)
        self.assertEqual(p["restorations"][-1]["undoes"], request["operation"])
        backup = self.controller.versions.get(p["restorations"][-1]["backup"])
        self.assertEqual(self.controller.versions.object_path(backup["files"]["deliverable.txt"]).read_text(), "newer edit")
        with self.assertRaisesRegex(ValueError, "No backup"):
            self.products.action({**request, "action": "undo_restore_preview"})

    def test_restore_metadata_commit_failure_rolls_back_files_and_product(self):
        p = self.create()
        self.ready(p)
        p = self.accept(p)
        path = self.project / "deliverable.txt"
        path.write_text("owner changes")
        preview = self.products.action({"id": p["id"], "action": "restore_preview", "number": 1})
        with patch.object(self.products, "store", side_effect=RuntimeError("Synthetic commit failure")):
            with self.assertRaisesRegex(RuntimeError, "Synthetic commit"):
                self.products.action({"id": p["id"], "action": "restore", "number": 1,
                                      "revision": preview["revision"]})
        self.assertEqual(path.read_text(), "owner changes")
        restored = Products(self.studio, clock=lambda: self.now).get(p["id"])
        self.assertEqual(restored, p)
        self.assertNotIn("restorations", restored)

    def test_late_edit_prevents_release_and_checks_detect_missing_files(self):
        p = self.create()
        self.ready(p)
        (self.project / "deliverable.txt").write_text("changed")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.accept(p)
        self.products.tick()
        self.assertEqual(self.products.get(p["id"])["releases"], [])
        (self.project / "deliverable.txt").write_text("OK")
        p = self.accept(p)
        p = self.products.action({"id": p["id"], "action": "check"})
        self.assertEqual(p["health"]["status"], "unchanged")
        (self.project / "deliverable.txt").unlink()
        p = self.products.action({"id": p["id"], "action": "check"})
        self.assertEqual(p["health"]["findings"][0]["path"], "deliverable.txt")

    def test_schedule_survives_restart_and_does_not_duplicate_or_start_without_optin(self):
        p = self.create()
        self.ready(p)
        p = self.accept(p)
        self.products.action({"id": p["id"], "action": "settings", "maintenance_days": 1})
        self.now += 86401
        self.products = Products(self.studio, clock=lambda: self.now)
        self.products.tick()
        self.now += 86401
        self.products.tick()
        p = self.products.get(p["id"])
        self.assertEqual(len(p["backlog"]), 2)
        self.assertEqual(p["backlog"][-1]["kind"], "maintenance")
        self.assertEqual(p["backlog"][-1]["status"], "queued")
        self.assertEqual(len(self.controller.list()), 1)
        self.assertEqual(p["health"]["status"], "unchanged")

    def test_autopilot_respects_questions_budget_and_manual_acceptance(self):
        p = self.create()
        self.products.action({"id": p["id"], "action": "settings", "autopilot": True, "cycle_limit": 1})
        self.products.tick()
        self.key = self.products.get(p["id"])["cycles"][0]["mission"]
        self.controller.tick()
        m = self.finish({"status": "plan", "tasks": [plan_task()],
                         "questions": [{"question": "What color?", "reason": "I need a decision."}]})
        self.products.tick()
        self.assertEqual(self.current()["status"], "waiting")
        self.controller.action({"id": self.key, "action": "answer", "question": m["questions"][0]["id"], "answer": "Blue"})
        self.products.tick()
        self.assertEqual(self.current()["phase"], "build")
        # Simulate a finished delivery here; evidence acceptance is covered by the real phases above.
        m = self.current()
        m.update(status="ready", active_attempt=None)
        self.controller.save(m)
        self.products.action({"id": p["id"], "action": "add", "title": "Next", "goal": "More work", "criteria": ["New"]})
        self.products.tick()
        self.assertEqual(self.current()["status"], "ready")
        self.assertEqual(len(self.controller.list()), 1)
        self.controller.action({"id": self.key, "action": "cancel"})
        self.products.tick()
        self.assertEqual(len(self.controller.list()), 1)
        self.assertIn("limit", self.products.get(p["id"])["message"])

    def test_queue_launch_is_atomic_and_repeat_start_cannot_duplicate(self):
        p = self.create()
        self.products.action({"id": p["id"], "action": "settings", "autopilot": True})
        original = self.products.store
        calls = 0
        def fail_once(db, product):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("Synthetic commit failure")
            return original(db, product)
        with patch.object(self.products, "store", side_effect=fail_once):
            self.products.tick()
        self.assertEqual(self.controller.list(), [])
        self.assertEqual(self.products.get(p["id"])["cycles"], [])
        self.products.tick()
        self.assertEqual(len(self.controller.list()), 1)
        with self.assertRaises(ValueError):
            self.products.action({"id": p["id"], "action": "start", "item": p["backlog"][0]["id"]})
        self.assertEqual(len(self.controller.list()), 1)

    def test_pause_stops_work_and_persists_across_restart(self):
        p = self.create()
        self.products.action({"id": p["id"], "action": "settings", "autopilot": True})
        self.products.tick()
        self.controller.tick()
        p = self.products.action({"id": p["id"], "action": "pause"})
        self.assertEqual(p["cycles"][0]["status"], "paused")
        self.assertEqual(next(iter(self.studio.runs.values()))["status"], "cancelled")
        restored = Products(self.studio, clock=lambda: self.now)
        restored.tick()
        self.assertEqual(restored.get(p["id"])["status"], "paused")
        self.assertEqual(len(self.controller.list()), 1)

    def test_adoption_requires_accepted_same_project_unlinked_delivery(self):
        p = self.create()
        self.ready(p)
        with self.assertRaises(ValueError):
            self.products.create({"project": self.pid, "source_mission": self.key})
        self.accept(p)
        with self.assertRaises(ValueError):
            self.products.create({"project": self.pid, "source_mission": self.key})
        m = self.current()
        m.pop("product_context")
        m["id"] = "legacy"
        m["verification_id"] = None
        m["acceptance"] = {"kind": "manual", "at": self.now}
        self.controller.save(m)
        adopted = self.products.create({"project": self.pid, "source_mission": "legacy"})
        self.assertEqual(adopted["releases"][0]["mission"], "legacy")
        self.assertEqual(self.controller.get("legacy")["product_context"]["id"], adopted["id"])
        with self.assertRaises(ValueError):
            self.products.create({"project": self.pid, "source_mission": "legacy"})
