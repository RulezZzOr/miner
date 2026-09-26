"""Durable company scheduling, limits, pause authority and real delivery bridge."""
import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from studio.companies import Companies
from studio.missions import parse_plan
from studio.server import Handler, Studio, atomic_json, write_config
from studio.tests.sandbox_support import requires_sandbox
from studio.tests.test_mission_integration import ProjectModel


def authenticated_open(studio, value):
    import urllib.request
    req = value if isinstance(value, urllib.request.Request) else urllib.request.Request(value)
    req.add_header('Authorization', 'Bearer '+(studio.data/'access-key').read_text().strip())
    return urllib.request.urlopen(req)


class CompanyDriverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        config = self.root / "agent.toml"
        write_config(config, {"test": {"model": "test", "protocol": "chat_completions",
                                     "auth": "none", "base_url": "http://127.0.0.1:1/v1"}}, "test")
        self.studio = Studio(self.project, self.root / "state", config)
        self.addCleanup(self.studio.oauth.close)
        self.addCleanup(self.studio.missions.close)
        self.pid = next(iter(self.studio.projects))
        self.now = 1000
        self.studio.missions.clock = lambda: self.now
        self.driver = Companies(self.studio, clock=lambda: self.now)
        self.studio.companies = self.driver
        self.c = self.driver.create({"name": "Test Company", "purpose": "Deliver verified work", "projects": [self.pid]})
        # Workers are simulated: these tests cover the controller, not the sandboxed runtime.
        self.launched = []
        self.studio.launch = self.launch
        self.studio.stop = self.stop

    def launch(self, body, *, mission):
        key = mission["attempt"]
        directory = self.studio.data / "runs" / key
        directory.mkdir(parents=True)
        run = {"id": key, "created": self.now, "status": "running", "project": body["project"], "mission": mission}
        self.studio.runs[key] = run
        atomic_json(directory / "run.json", run)
        self.launched.append((body, mission))
        return run

    def stop(self, key):
        self.studio.runs[key]["status"] = "cancelled"

    def mission(self, index=-1):
        return self.studio.missions.get(self.current()["cycles"][index]["mission"])

    def tick(self):
        self.driver.tick()
        self.studio.missions.tick()

    def end_run(self, m, status, **fields):
        m = self.studio.missions.get(m["id"])
        run = self.studio.runs[m["active_attempt"]]
        run.update(status=status, **fields)
        self.studio.missions.tick()
        return self.studio.missions.get(m["id"])

    def block(self, m, kind, message):
        m = self.studio.missions.get(m["id"])
        if m["active_attempt"]:
            self.studio.runs[m["active_attempt"]]["status"] = "cancelled"
            m["active_attempt"] = None
        self.studio.missions.block(m, kind, message)
        self.studio.missions.save(m)
        return m

    def block_by_time_limit(self, m):
        # Plan time limit: one retry with a larger budget, then a typed block.
        for _ in range(2):
            m = self.studio.missions.get(m["id"])
            if not m["active_attempt"]:
                self.now = max(self.now, m["retry_at"])
                self.studio.missions.tick()
            m = self.end_run(m, "incomplete", reason="Run exceeded time limit.", failure_kind="time_limit")
        self.assertEqual((m["status"], m["blocked_kind"]), ("blocked", "time_limit"), m["message"])
        return m

    def current(self):
        return self.driver.get(self.c["id"])

    def action(self, action, **kw):
        c = self.current()
        return self.driver.action({"id": c["id"], "revision": c["revision"], "action": action, **kw})

    def task(self, **kw):
        c = self.action("add_task", project=self.pid, title="Work", goal="Create product.txt",
                        criteria=["Product ready"], **kw)
        return c["tasks"][-1]

    def begin(self):
        self.action("start")
        self.driver.tick()
        c = self.current()
        return self.studio.missions.get(c["cycles"][-1]["mission"])

    def test_draft_is_idle_and_start_restart_does_not_duplicate(self):
        self.task()
        self.driver.tick()
        self.assertEqual(self.current()["cycles"], [])
        m = self.begin()
        self.studio.companies = self.driver = Companies(self.studio, clock=lambda: self.now)
        for _ in range(5):
            self.driver.tick()
        self.assertEqual([c["mission"] for c in self.current()["cycles"]], [m["id"]])
        self.assertEqual(self.driver.usage(self.current())["reserved_runs"], 10)

    def test_read_only_snapshot_never_changes_revision(self):
        self.task()
        before = self.current()
        for _ in range(3):
            self.driver.snapshot()
            self.driver.report(before["id"])
        self.assertEqual(before, self.current())

    def test_conflicting_form_cannot_overwrite_state(self):
        stale = self.current()
        self.task()
        with self.assertRaisesRegex(ValueError, "meanwhile"):
            self.driver.action({"id": stale["id"], "revision": stale["revision"], "action": "start"})

    def test_atomic_dispatch_rolls_back_mission_and_reservation(self):
        self.task()
        self.action("start")
        c = self.current()
        original = self.driver.store
        def fail_store(db, value, **kw):
            original(db, value, **kw)
            raise RuntimeError("disk failure")
        with patch.object(self.driver, "store", side_effect=fail_store):
            with self.assertRaisesRegex(RuntimeError, "disk failure"):
                self.driver.dispatch(c, c["tasks"][0])
        self.assertEqual(self.current()["cycles"], [])
        self.assertEqual(self.studio.missions.list(), [])
        self.driver.tick()
        self.assertEqual(len(self.current()["cycles"]), 1)

    def test_run_reservations_bound_parallel_projects(self):
        p2 = self.root / "second"
        p2.mkdir()
        pid2 = self.studio.add_project(p2)["id"]
        self.action("add_project", project=pid2)
        self.task()
        self.action("add_task", project=pid2, title="Second", goal="Second task", criteria=["Done"])
        self.action("settings", run_budget=10)
        self.begin()
        self.driver.tick()
        self.assertEqual(len(self.current()["cycles"]), 1)
        self.assertEqual(self.driver.usage(self.current())["remaining_runs"], 0)
        self.action("pause")
        with self.assertRaisesRegex(ValueError, "reservations"):
            self.action("settings", run_budget=9)

    def test_parent_pause_and_expiry_block_direct_resume_and_accept(self):
        self.task()
        m = self.begin()
        self.action("pause")
        self.assertEqual(self.studio.missions.get(m["id"])["status"], "paused")
        for action in ["resume", "approve_plan", "accept", "manual_accept"]:
            with self.assertRaisesRegex(ValueError, "parent company"):
                self.studio.missions.action({"id": m["id"], "action": action})
        self.action("start")
        self.driver.tick()
        self.assertEqual(self.studio.missions.get(m["id"])["status"], "running")
        self.now += 8 * 86400
        self.driver.tick()
        self.assertEqual(self.current()["status"], "paused")
        with self.assertRaisesRegex(ValueError, "Horizon"):
            self.action("start")

    def test_direct_runtime_change_cannot_bypass_reserved_budget(self):
        self.task()
        m = self.begin()
        self.action("pause")
        with self.assertRaisesRegex(ValueError, "reserved"):
            self.studio.missions.action({"id": m["id"], "action": "runtime_settings", "max_attempts": 1000})
        self.assertEqual(self.studio.missions.get(m["id"])["max_attempts"], 10)

    def test_tool_permissions_follow_parent_in_both_directions_without_budget_changes(self):
        self.task()
        m = self.begin()
        original = {k: m[k] for k in ("max_attempts", "attempt_minutes", "max_turns", "deadline", "tasks")}
        for enabled in (True, False):
            self.action("pause")
            self.action("settings", auto_tools=enabled, attempts_per_cycle=20)
            updated = self.studio.missions.get(m["id"])
            self.assertEqual(updated["auto_approve"], enabled)
            self.assertEqual(updated["company_context"]["auto_tools"], enabled)
            self.assertEqual({k: updated[k] for k in original}, original)
            self.action("start")
            self.driver.tick()
            self.assertEqual(self.studio.missions.get(m["id"])["status"], "running")
        self.studio.missions.action({"id": m["id"], "action": "cancel"})
        self.action("pause")
        self.action("settings", auto_tools=True)
        self.assertFalse(self.studio.missions.get(m["id"])["auto_approve"])

    def test_start_repairs_legacy_permission_snapshot(self):
        self.task()
        m = self.begin()
        self.action("pause")
        c = self.current()
        c["policy"]["auto_tools"] = True
        self.driver.save(c)
        self.action("start")
        self.assertTrue(self.studio.missions.get(m["id"])["auto_approve"])

    def test_permission_update_rolls_back_with_company_save_failure(self):
        self.task()
        m = self.begin()
        self.action("pause")
        with patch.object(self.driver, "store", side_effect=RuntimeError("disk failure")):
            with self.assertRaisesRegex(RuntimeError, "disk failure"):
                self.action("settings", auto_tools=True)
        self.assertFalse(self.current()["policy"]["auto_tools"])
        self.assertFalse(self.studio.missions.get(m["id"])["auto_approve"])

    def test_dependencies_and_external_work_never_execute(self):
        external = self.task(kind="external")
        self.task(depends_on=[external["id"]])
        self.action("start")
        self.driver.tick()
        self.assertEqual(self.current()["cycles"], [])
        self.action("resolve_external", task=external["id"], outcome="done", note="Owner supplied approved inputs")
        self.driver.tick()
        self.assertEqual(len(self.current()["cycles"]), 1)
        self.assertNotEqual(self.current()["cycles"][0]["task"], external["id"])

    def test_reservation_extension_keeps_company_budget_and_rolls_back_on_failure(self):
        self.task()
        m = self.begin()
        with self.assertRaises(ValueError):self.action("reserve_runs", mission=m["id"], max_attempts=20)
        self.action("pause")
        original = self.current()["policy"]["run_budget"]
        self.action("reserve_runs", mission=m["id"], max_attempts=20)
        self.assertEqual(self.studio.missions.get(m["id"])["max_attempts"],20)
        self.assertEqual(self.current()["policy"]["run_budget"],original)
        self.assertEqual(self.driver.usage(self.current())["remaining_runs"],80)
        with self.assertRaisesRegex(ValueError,"budget"):
            self.action("reserve_runs",mission=m["id"],max_attempts=101)
        with patch.object(self.driver,"store",side_effect=RuntimeError("disk")):
            with self.assertRaises(RuntimeError):self.action("reserve_runs",mission=m["id"],max_attempts=25)
        self.assertEqual(self.studio.missions.get(m["id"])["max_attempts"],20)
        with self.assertRaises(ValueError):self.action("reserve_runs",mission="other",max_attempts=30)

    def test_cancelled_dependency_does_not_release_followup(self):
        first = self.task()
        self.task(depends_on=[first["id"]])
        m = self.begin()
        self.studio.missions.action({"id": m["id"], "action": "cancel"})
        self.driver.tick()
        self.assertEqual(len(self.current()["cycles"]), 1)

    def test_recurrence_has_no_catchup_storm_and_honors_task_limit(self):
        self.task(interval_hours=1, max_cycles=2)
        m = self.begin()
        # Unit fixture sets terminal status; the integration test below proves real acceptance.
        m["status"] = "accepted"
        self.studio.missions.save(m)
        self.driver.tick()
        self.now += 20 * 3600
        self.driver.tick()
        self.driver.tick()
        self.assertEqual(len(self.current()["cycles"]), 2)
        m2 = self.studio.missions.get(self.current()["cycles"][-1]["mission"])
        m2["status"] = "accepted"
        self.studio.missions.save(m2)
        self.driver.tick()
        self.now += 20 * 3600
        self.driver.tick()
        self.assertEqual(len(self.current()["cycles"]), 2)
        self.assertEqual(self.current()["tasks"][0]["status"], "done")

    def test_failed_acceptance_is_isolated_and_never_marks_done(self):
        self.task()
        self.action("settings", auto_accept=True)
        m = self.begin()
        m["status"] = "ready"
        self.studio.missions.save(m)
        for _ in range(3):
            self.driver.tick()
        # One child's failed acceptance neither pauses the Driver nor repeats every tick.
        self.assertEqual(self.current()["status"], "active")
        self.assertNotEqual(self.current()["tasks"][0]["status"], "done")
        m = self.studio.missions.get(m["id"])
        self.assertEqual(m["status"], "ready")
        self.assertIn("Automatic acceptance failed", m["message"])
        events = [e for e in self.driver.snapshot()["companies"][0]["events"] if e["action"] == "accept_failed"]
        self.assertEqual(len(events), 1)

    def test_unanswered_plan_is_not_automatically_approved(self):
        self.task()
        m = self.begin()
        m.update(status="awaiting_plan", questions=[{"id":"q", "answer":None, "question":"Need input"}])
        self.studio.missions.save(m)
        self.driver.tick()
        self.assertEqual(self.studio.missions.get(m["id"])["status"], "awaiting_plan")

    def test_same_workspace_not_dispatched_to_competing_companies(self):
        self.task()
        self.begin()
        other = self.driver.create({"name":"Other", "purpose":"Other", "projects":[self.pid]})
        other = self.driver.action({"id":other["id"],"revision":other["revision"],"action":"add_task",
                                   "project":self.pid,"title":"Conflict","goal":"Change", "criteria":["done"]})
        self.driver.action({"id":other["id"],"revision":other["revision"],"action":"start"})
        self.driver.tick()
        self.assertEqual(len(self.studio.missions.list()), 1)

    # Production failure modes of an onboarded company (bounded, visible recovery).

    def events(self, action):
        return [e for e in self.driver.snapshot()["companies"][0]["events"] if e["action"] == action]

    def test_blocked_time_limit_is_recovered_in_bounded_rounds_then_one_owner_question(self):
        self.task()
        m = self.begin()
        self.studio.missions.tick()
        limits = []
        for round_ in range(1, 4):
            m = self.block_by_time_limit(m)
            self.driver.tick()
            self.assertEqual(self.studio.missions.get(m["id"])["status"], "blocked")  # backoff first
            self.now += 300 * 2 ** (round_ - 1)
            self.driver.tick()
            m = self.studio.missions.get(m["id"])
            self.assertEqual(m["status"], "running", m["message"])
            self.assertTrue(m["message"].startswith(f"Driver recovery {round_}/3:"), m["message"])
            limits.append((m["attempt_minutes"], m["max_turns"]))
            self.assertEqual(m["driver_recoveries"], round_)
        self.assertEqual(limits, [(30, 48), (45, 56), (68, 64)])
        self.assertEqual(len(self.events("recovery")), 3)
        self.assertTrue(self.events("recovery")[0]["message"].startswith("Driver recovery"))
        m = self.block_by_time_limit(m)
        reserved = self.driver.usage(self.current())["reserved_runs"]
        self.assertGreater(reserved, 0)  # still under Driver recovery: the reservation is kept
        for _ in range(3):
            self.now += 3600
            self.driver.tick()
        m = self.studio.missions.get(m["id"])
        self.assertEqual(m["status"], "blocked")
        self.assertTrue(m["owner_needed"])
        open_questions = [q for q in m["questions"] if q["answer"] is None]
        self.assertEqual([q["kind"] for q in open_questions], ["recovery"])
        self.assertIn("Options:", open_questions[0]["question"])
        self.assertEqual(self.driver.usage(self.current())["reserved_runs"], 0)  # released for the owner
        self.assertIn("Owner decision needed", self.current()["message"])
        # The owner's answer continues the work and re-reserves its runs.
        m = self.studio.missions.action({"id": m["id"], "action": "answer", "question": open_questions[0]["id"], "answer": "continue"})
        self.assertEqual(m["status"], "running", m["message"])
        self.assertGreater(self.driver.usage(self.current())["reserved_runs"], 0)

    def test_legacy_production_blocks_are_recovered(self):
        # State written by 0.4.0-alpha.10: untyped blocks, a generic question, blocked turned into paused.
        p2 = self.root / "second"
        p2.mkdir()
        pid2 = self.studio.add_project(p2)["id"]
        self.action("add_project", project=pid2)
        self.task()
        self.action("add_task", project=pid2, title="Second", goal="Second task", criteria=["Done"])
        self.action("start")
        self.driver.tick()
        self.driver.tick()
        first, second = self.block(self.mission(0), "x", "x"), self.block(self.mission(1), "x", "x")
        for m in (first, second):
            m.pop("blocked_kind")
            m.pop("blocked_at")
        first.update(message="Three unsuccessful attempts. Invalid output: Complex brief requires a readiness assessment before execution.",
                     questions=[{"id": "q1", "question": "How should I adjust the approach before restarting the task?",
                                 "reason": "legacy", "task": None, "answer": None, "created": 1}])
        second.update(status="paused", resume_status="blocked", message="First, restore the parent company and its time horizon.",
                      attempts=[{"id": "a1", "phase": "plan", "task": None, "started": 1,
                                 "error": "Run exceeded time limit."}])
        for m in (first, second):
            self.studio.missions.save(m)
        self.driver.tick()
        self.driver.tick()
        first, second = self.studio.missions.get(first["id"]), self.studio.missions.get(second["id"])
        self.assertEqual(first["status"], "running", first["message"])
        self.assertEqual(first["questions"][0]["answer"], "Superseded by automatic Driver recovery (not an owner decision).")
        self.assertEqual((second["status"], second["blocked_kind"]), ("blocked", "time_limit"))
        self.assertIn("Run exceeded time limit.", second["message"])
        self.now += 300
        self.driver.tick()
        self.assertEqual(self.studio.missions.get(second["id"])["status"], "running")

    def test_non_recoverable_block_is_never_auto_resumed(self):
        self.task()
        m = self.begin()
        self.studio.missions.tick()
        m = self.block(m, "log_limit", "Project exceeded log limit.")
        for _ in range(4):
            self.now += 7200
            self.driver.tick()
        m = self.studio.missions.get(m["id"])
        self.assertEqual((m["status"], m.get("driver_recoveries", 0)), ("blocked", 0))
        self.assertEqual(sum(q["answer"] is None for q in m["questions"]), 1)

    def test_provider_503_does_not_consume_company_runs(self):
        self.task()
        m = self.begin()
        self.studio.missions.tick()
        for _ in range(3):
            m = self.end_run(m, "failed", reason="Error code: 503 - Loading model", failure_kind="provider_unavailable")
            self.assertEqual(m["status"], "running")
            self.now = m["retry_at"]
            self.tick()
        usage = self.driver.usage(self.current())
        self.assertEqual((usage["used_runs"], usage["reserved_runs"]), (1, 9))  # only the attempt now running

    def test_full_reservations_are_released_for_owner_blocks_and_re_reserved_on_resume(self):
        projects = [self.pid]
        for name in ("second", "third"):
            (self.root / name).mkdir()
            key = self.studio.add_project(self.root / name)["id"]
            self.action("add_project", project=key)
            projects.append(key)
        for key in projects:
            self.action("add_task", project=key, title="Work " + key, goal="Create product.txt", criteria=["Product ready"])
        self.action("settings", run_budget=20)
        self.action("start")
        self.driver.tick()
        self.driver.tick()
        self.assertEqual(len(self.current()["cycles"]), 2)
        self.assertEqual(self.driver.usage(self.current())["remaining_runs"], 0)
        blocked = self.block(self.mission(0), "review_rounds", "Three rounds of corrections without acceptance.")
        self.driver.tick()  # owner question; unused runs released
        self.driver.tick()  # the third task can start with the released runs
        self.assertEqual(len(self.current()["cycles"]), 3)
        self.assertEqual(self.driver.usage(self.current())["remaining_runs"], 0)
        with self.assertRaisesRegex(ValueError, "company run budget"):
            self.studio.missions.action({"id": blocked["id"], "action": "resume"})
        self.studio.missions.action({"id": self.mission(2)["id"], "action": "cancel"})
        m = self.studio.missions.action({"id": blocked["id"], "action": "resume"})
        self.assertEqual(m["status"], "running")
        usage = self.driver.usage(self.current())
        self.assertLessEqual(usage["used_runs"] + usage["reserved_runs"], 20)
        self.assertEqual(m["final_cycles"], 0)

    def test_company_pause_keeps_blocked_status_and_reason_and_resumes_its_own_work(self):
        p2 = self.root / "second"
        p2.mkdir()
        pid2 = self.studio.add_project(p2)["id"]
        self.action("add_project", project=pid2)
        self.task()
        self.action("add_task", project=pid2, title="Second", goal="Second task", criteria=["Done"])
        self.action("start")
        self.driver.tick()
        self.driver.tick()
        blocked, running = self.block(self.mission(0), "log_limit", "Project exceeded log limit."), self.mission(1)
        self.action("pause")
        for _ in range(2):
            self.tick()
        blocked, running = self.studio.missions.get(blocked["id"]), self.studio.missions.get(running["id"])
        self.assertEqual((blocked["status"], blocked["message"]), ("blocked", "Project exceeded log limit."))
        self.assertEqual(running["status"], "paused")
        self.action("start")
        self.driver.tick()
        self.assertEqual(self.studio.missions.get(running["id"])["status"], "running")
        self.assertEqual(self.studio.missions.get(blocked["id"])["status"], "blocked")
        self.assertEqual(self.current()["resume_missions"], [])

    def test_mission_paused_by_parent_state_is_resumed_on_start(self):
        self.task()
        m = self.begin()
        c = self.current()
        c["status"] = "paused"  # e.g. a crash between the company save and pausing children
        self.driver.save(c)
        self.studio.missions.tick()
        m = self.studio.missions.get(m["id"])
        self.assertEqual((m["status"], m["paused_by_parent"]), ("paused", True))
        self.action("start")
        self.driver.tick()
        self.assertEqual(self.studio.missions.get(m["id"])["status"], "running")

    def test_restart_on_last_reserved_run_does_not_deadlock_the_driver(self):
        self.task()
        m = self.begin()
        self.studio.missions.tick()
        m = self.studio.missions.get(m["id"])
        m["max_attempts"] = 1
        self.studio.missions.save(m)
        self.action("pause")
        for _ in range(2):
            self.action("start")
            for _ in range(4):
                self.tick()
            self.assertEqual(self.current()["status"], "active")
            self.action("pause")
        m = self.studio.missions.get(m["id"])
        self.assertEqual((m["status"], m["blocked_kind"]), ("blocked", "run_limit"))
        self.assertIn("reserve_runs", m["message"])
        self.assertEqual(self.current()["resume_missions"], [])
        self.assertEqual(len(self.events("resume_failed")), 1)

    def test_horizon_pauses_work_and_renewal_continues_it(self):
        self.task()
        m = self.begin()
        self.studio.missions.tick()
        self.now = self.current()["deadline"] + 1
        self.tick()
        self.tick()
        self.assertEqual(self.current()["status"], "paused")
        self.assertEqual(self.studio.missions.get(m["id"])["status"], "paused")  # not expired
        with self.assertRaisesRegex(ValueError, "Horizon"):
            self.action("start")
        self.action("settings", renew_horizon=True)
        self.action("start")
        self.assertGreater(self.studio.missions.get(m["id"])["deadline"], self.now)
        self.driver.tick()
        self.assertEqual(self.studio.missions.get(m["id"])["status"], "running")
        self.assertEqual(self.current()["tasks"][0]["status"], "running")

    def test_early_renewal_extends_child_deadlines(self):
        self.task()
        m = self.begin()
        old = self.studio.missions.get(m["id"])["deadline"]
        self.now += 6 * 86400
        self.action("pause")
        self.action("settings", renew_horizon=True)
        self.action("start")
        self.assertGreater(self.studio.missions.get(m["id"])["deadline"], old)
        self.now = old + 3600
        self.tick()
        self.assertEqual(self.studio.missions.get(m["id"])["status"], "running")
        self.assertEqual(self.current()["status"], "active")

    def test_renewal_requeues_expired_tasks_and_owner_can_requeue_cancelled(self):
        first = self.task()
        m = self.begin()
        m["status"] = "expired"  # legacy state: expired with the old horizon
        self.studio.missions.save(m)
        self.driver.tick()
        self.assertEqual(self.current()["tasks"][0]["status"], "expired")
        self.action("pause")
        self.action("settings", renew_horizon=True)
        self.action("start")
        self.assertEqual(self.current()["tasks"][0]["status"], "queued")
        self.driver.tick()
        second = self.mission()
        self.assertNotEqual(second["id"], m["id"])
        self.studio.missions.action({"id": second["id"], "action": "cancel"})
        self.driver.tick()
        self.assertEqual(self.action("enable_task", task=first["id"])["tasks"][0]["status"], "cancelled")
        c = self.action("requeue_task", task=first["id"])
        self.assertEqual(c["tasks"][0]["status"], "queued")
        self.driver.tick()
        self.assertEqual(len(self.current()["cycles"]), 3)
        with self.assertRaisesRegex(ValueError, "cancelled or expired"):
            self.action("requeue_task", task=first["id"])

    def test_draft_on_same_project_does_not_hold_dispatch(self):
        self.studio.missions.create({"project": self.pid, "title": "Abandoned", "goal": "Draft", "criteria": ["x"]})
        self.task()
        self.action("start")
        self.driver.tick()
        self.assertEqual(len(self.current()["cycles"]), 1)

    def test_busy_project_is_named_in_the_driver_message_once(self):
        other = self.studio.missions.create({"project": self.pid, "title": "Standalone", "goal": "Other", "criteria": ["x"],
                                             "isolated": False})
        self.studio.missions.action({"id": other["id"], "action": "start"})
        self.task()
        self.action("start")
        for _ in range(3):
            self.driver.tick()
        self.assertEqual(self.current()["cycles"], [])
        self.assertIn(other["id"], self.current()["message"])
        self.assertEqual(len(self.events("project_busy")), 1)

    def test_policy_limit_changes_reach_unfinished_executions(self):
        self.task()
        m = self.begin()
        self.action("pause")
        self.action("settings", attempt_minutes=45, max_turns=60)
        m = self.studio.missions.get(m["id"])
        self.assertEqual((m["attempt_minutes"], m["max_turns"]), (45, 60))
        self.assertEqual((m["company_context"]["attempt_minutes"], m["company_context"]["max_turns"]), (45, 60))
        self.assertEqual(m["max_attempts"], 10)

    def rounds_blocked(self, kind, message, cycles=0, final_cycles=0):
        """A company execution blocked by the correction-round cap, with the Driver's owner question raised."""
        self.task()
        m = self.begin()
        self.studio.missions.tick()
        m = self.studio.missions.get(m["id"])
        m["tasks"] = parse_plan({"tasks": [{"id": "one", "title": "one", "instructions": "Do it.", "criteria": ["Product ready"]}]})
        m["tasks"][0]["cycles"] = cycles
        m.update(phase="build", final_cycles=final_cycles, driver_recoveries=2)
        self.studio.missions.save(m)
        m = self.block(m, kind, message)
        self.driver.tick()
        m = self.studio.missions.get(m["id"])
        self.assertTrue(m["owner_needed"])
        return m

    def test_owner_answer_while_driver_paused_resumes_on_start(self):
        m = self.rounds_blocked("review_rounds", "Three rounds of corrections without acceptance.", cycles=3)
        question = next(q for q in m["questions"] if q["answer"] is None)
        self.action("pause")
        m = self.studio.missions.action({"id": m["id"], "action": "answer", "question": question["id"], "answer": "continue"})
        self.assertEqual((m["status"], m["driver_resume"]), ("blocked", True))
        self.action("start")
        self.driver.tick()
        m = self.studio.missions.get(m["id"])
        self.assertEqual(m["status"], "running")
        # The deferred answer is the owner's decision: fresh correction rounds and Driver recoveries.
        self.assertEqual((m["tasks"][0]["cycles"], m["tasks"][0]["cycles_total"], m["driver_recoveries"]), (0, 3, 0))
        self.assertEqual(self.events("resume_failed"), [])

    def test_owner_task_revision_after_final_rounds_grants_fresh_rounds(self):
        m = self.rounds_blocked("final_rounds", "Final check returned defects: missing test.", final_cycles=3)
        self.action("pause")
        m = self.studio.missions.action({"id": m["id"], "action": "revise_task", "task": "one", "reason": "Clarify.",
                                         "expected_criteria": ["Product ready"], "criteria": ["Product ready and tested"]})
        self.assertEqual((m["status"], m["driver_resume"]), ("blocked", True))
        self.action("start")
        self.driver.tick()
        m = self.studio.missions.get(m["id"])
        self.assertEqual((m["status"], m["final_cycles"], m["final_cycles_total"]), ("running", 0, 3), m["message"])
        self.assertEqual(m["driver_recoveries"], 0)
        self.assertEqual(m["questions"][-1]["answer"], "Superseded by owner resume.")

    def test_set_checks_on_paused_finished_execution_stays_within_the_budget(self):
        projects = [self.pid]
        for name in ("second", "third"):
            (self.root / name).mkdir()
            key = self.studio.add_project(self.root / name)["id"]
            self.action("add_project", project=key)
            projects.append(key)
        for key in projects:
            self.action("add_task", project=key, title="Work " + key, goal="Create product.txt", criteria=["Product ready"])
        self.action("settings", run_budget=12, attempts_per_cycle=5)
        self.action("start")
        self.driver.tick()
        self.driver.tick()
        first = self.mission(0)
        first.update(status="ready", final_report={"run": "x", "verified_artifacts": []})
        self.studio.missions.save(first)  # finished work releases its unused runs
        self.driver.tick()
        self.assertEqual(len(self.current()["cycles"]), 3)
        def committed():
            usage = self.driver.usage(self.current())
            return usage["used_runs"] + usage["reserved_runs"]
        self.studio.missions.action({"id": first["id"], "action": "pause"})
        m = self.studio.missions.action({"id": first["id"], "action": "set_checks",
                                         "verification_checks": [{"argv": [sys.executable, "-c", "print(1)"]}]})
        self.assertEqual(m["resume_status"], "verifying")
        self.assertLessEqual(committed(), 12)
        self.assertIn("company budget allowed", m["message"])
        m = self.studio.missions.action({"id": first["id"], "action": "resume"})
        self.assertEqual(m["status"], "verifying")
        self.assertLessEqual(committed(), 12)

    def test_revised_brief_of_blocked_mission_continues_after_start(self):
        self.task()
        m = self.begin()
        m = self.block(m, "invalid_output", "Invalid output: Complex brief requires a readiness assessment before execution.")
        m["owner_needed"] = True
        self.studio.missions.save(m)
        self.action("pause")
        from studio.workflow import brief_hash
        m = self.studio.missions.action({"id": m["id"], "action": "revise_brief", "expected_brief_hash": brief_hash(m),
                                         "reason": "Clarify scope.", "goal": "Create product.txt with OK"})
        self.assertEqual(m["status"], "paused")
        self.action("start")
        self.driver.tick()
        self.assertEqual(self.studio.missions.get(m["id"])["status"], "running")

    def test_http_company_api_uses_existing_origin_and_token_boundary(self):
        import urllib.error
        import urllib.request
        server = ThreadingHTTPServer(("127.0.0.1",0),Handler)
        server.studio=self.studio
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(thread.join,2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base=f"http://127.0.0.1:{server.server_port}"
        with authenticated_open(self.studio, base+"/api/companies") as response:
            self.assertEqual(json.load(response)["companies"][0]["name"],"Test Company")
        body=json.dumps({"id":self.c["id"],"revision":self.current()["revision"],"action":"start"}).encode()
        req=urllib.request.Request(base+"/api/companies/action",data=body,headers={"Content-Type":"application/json"})
        with self.assertRaises(urllib.error.HTTPError) as error:
            authenticated_open(self.studio, req)
        self.assertEqual(error.exception.code,403)
        req.add_header("X-Studio-Token",self.studio.token)
        with authenticated_open(self.studio, req) as response:
            self.assertEqual(json.load(response)["status"],"active")
        with authenticated_open(self.studio, base+"/companies.js") as response:
            self.assertIn(b"initCompanies",response.read())


class CompanyDriverIntegration(unittest.TestCase):
    @requires_sandbox
    def test_driver_real_workers_review_checks_accept_and_restart(self):
        self.run_delivery()

    @requires_sandbox
    def test_enable_auto_tools_resumes_manual_worker_without_more_approvals(self):
        self.run_delivery(enable_during_run=True)

    def run_delivery(self, enable_during_run=False):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve()
            project=root/"project"
            project.mkdir()
            model=ThreadingHTTPServer(("127.0.0.1",0),ProjectModel)
            model.frames=[]
            thread=threading.Thread(target=model.serve_forever,daemon=True)
            thread.start()
            config=root/"agent.toml"
            write_config(config,{"test":{"model":"test","protocol":"chat_completions","chat_dialect":"openai",
                "base_url":f"http://127.0.0.1:{model.server_port}/v1","auth":"none","context_window":32768,"max_output_tokens":4096}},"test")
            studio=Studio(project,root/"state",config)
            try:
                driver=studio.companies
                c=driver.create({"name":"Company Pilot","purpose":"Verified delivery", "projects":[next(iter(studio.projects))]})
                def action(name,**kw):
                    current=driver.get(c["id"])
                    return driver.action({"id":c["id"],"revision":current["revision"],"action":name,**kw})
                action("settings",auto_tools=not enable_during_run,auto_accept=True,max_turns=8)
                action("add_task",project=c["projects"][0],title="Create file",goal="Create product.txt",
                       criteria=["Product ready"],verification_checks=[{"argv":[sys.executable,"-c","from pathlib import Path; assert Path('product.txt').read_text() == 'OK'"]}])
                action("start")
                deadline=time.monotonic()+90
                reloaded=False
                switched=False
                while time.monotonic()<deadline:
                    driver.tick()
                    studio.missions.tick()
                    current=driver.get(c["id"])
                    if current["cycles"]:
                        m=studio.missions.get(current["cycles"][-1]["mission"])
                        if enable_during_run and not switched and m.get("active_attempt") and studio.approvals(m["active_attempt"]):
                            action("pause")
                            action("settings", auto_tools=True, auto_accept=True, max_turns=8)
                            action("start")
                            switched=True
                        elif switched and m.get("active_attempt"):
                            run = studio.runs.get(m["active_attempt"], {})
                            if run.get("status") == "running":
                                self.assertEqual(studio.approvals(m["active_attempt"]), [])
                        if m["status"]=="awaiting_plan" and not reloaded:
                            # Restore persistent company state before automatically approving.
                            driver=studio.companies=Companies(studio)
                            reloaded=True
                        if m["status"]=="accepted" or current["status"]=="paused" or m["status"]=="blocked":
                            break
                    time.sleep(.1)
                self.assertEqual(m["status"],"accepted",str(m))
                self.assertTrue(reloaded)
                self.assertEqual((project/"product.txt").read_text(),"OK")
                self.assertEqual(m["acceptance"]["kind"],"verified")
                self.assertEqual(switched, enable_during_run)
                self.assertEqual(len(m["attempts"]),5 if switched else 4, str(m["attempts"]))
                driver.tick()
                current=driver.get(c["id"])
                self.assertEqual(current["tasks"][0]["status"],"done")
                self.assertEqual(driver.usage(current)["used_runs"],5 if switched else 4)
                self.assertEqual(driver.usage(current)["reserved_runs"],0)
                self.assertEqual(len(current["cycles"]),1)
            finally:
                studio.missions.close()
                for key in list(studio.processes):
                    studio.stop(key)
                for process in list(studio.processes.values()):
                    studio.kill_later(process)
                studio.oauth.close()
                model.shutdown()
                model.server_close()
                thread.join(2)
