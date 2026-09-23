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
from studio.server import Handler, Studio, write_config
from studio.tests.test_mission_integration import ProjectModel


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

    def test_failed_acceptance_trips_breaker_and_never_marks_done(self):
        self.task()
        self.action("settings", auto_accept=True)
        m = self.begin()
        m["status"] = "ready"
        self.studio.missions.save(m)
        for _ in range(3):
            self.driver.tick()
        self.assertEqual(self.current()["status"], "paused")
        self.assertNotEqual(self.current()["tasks"][0]["status"], "done")
        self.assertNotEqual(self.studio.missions.get(m["id"])["status"], "accepted")

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
        with urllib.request.urlopen(base+"/api/companies") as response:
            self.assertEqual(json.load(response)["companies"][0]["name"],"Test Company")
        body=json.dumps({"id":self.c["id"],"revision":self.current()["revision"],"action":"start"}).encode()
        req=urllib.request.Request(base+"/api/companies/action",data=body,headers={"Content-Type":"application/json"})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(req)
        self.assertEqual(error.exception.code,403)
        req.add_header("X-Studio-Token",self.studio.token)
        with urllib.request.urlopen(req) as response:
            self.assertEqual(json.load(response)["status"],"active")
        with urllib.request.urlopen(base+"/companies.js") as response:
            self.assertIn(b"initCompanies",response.read())


class CompanyDriverIntegration(unittest.TestCase):
    def test_driver_real_workers_review_checks_accept_and_restart(self):
        self.run_delivery()

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
                self.assertEqual(len(m["attempts"]),5 if switched else 4)
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
