"""Actual local HTTP process, incident queue, verified repair release and rollback."""
import os
import sys
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import psutil

from studio.deployments import Deployments
from studio.process_tree import ProcessTree
from studio.tests import test_products as helpers
from studio.tests.sandbox_support import requires_sandbox
from studio.tests.test_verification import processes_under

SERVICE = '''import argparse, os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
p=argparse.ArgumentParser(); p.add_argument('--host'); p.add_argument('--port',type=int); a=p.parse_args()
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def do_GET(self):
  failed = BUGGY and (Path(os.environ['SWITCH_DATA_DIR'])/'incident').exists()
  self.send_response(503 if failed else 200); self.end_headers(); self.wfile.write(b'OK VERSION_TAG')
HTTPServer((a.host,a.port),Handler).serve_forever()
'''


class DeploymentSetup:
    launch = helpers.ProductTests.launch
    stop = helpers.ProductTests.stop

    def setUp(self):
        helpers.ProductTests.setUp(self)
        # Cleanups run last-in first-out: every Deployments.close runs before this check.
        self.addCleanup(self.assert_no_leaked_processes)
        self.studio.deployments = Deployments(self.studio)
        self.addCleanup(self.studio.deployments.close)

    def assert_no_leaked_processes(self):
        deadline = time.monotonic() + 5
        while processes_under(self.root) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(processes_under(self.root), [], "a service or worker outlived its deployment")


class Health(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK direct")


class DeploymentControlTests(DeploymentSetup, unittest.TestCase):
    """Controller behaviour that does not need a sandboxed service."""

    def test_loopback_probe_ignores_http_proxy(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Health)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        manager = self.studio.deployments
        record = {"id": "probe", "url": f"http://127.0.0.1:{server.server_port}",
                  "spec": {"health_path": "/health", "expected_status": 200, "expected_text": "OK"}}
        tree = type("Tree", (), {"refresh": staticmethod(lambda: [psutil.Process()])})()
        manager.processes["probe"] = (None, tree)
        self.addCleanup(manager.processes.pop, "probe", None)
        unreachable = "http://127.0.0.1:9"
        with patch.dict(os.environ, {"http_proxy": unreachable, "HTTP_PROXY": unreachable, "no_proxy": "", "NO_PROXY": ""}):
            check = manager.probe(record)
        self.assertTrue(check["passed"], check)

    def test_failed_start_stops_the_worker_and_records_an_english_reason(self):
        manager = self.studio.deployments
        product = {"id": "product", "deployment_settings": {"argv": [sys.executable, "-c", "pass", "{host}", "{port}"],
                                                            "health_path": "/"}}
        release = {"number": 1, "version_id": "version", "verification_id": "evidence"}
        version = {"id": "version", "files": {}, "modes": {}}
        evidence = {"id": "evidence", "status": "passed", "sources": {}, "source_modes": {}}
        with patch.object(self.controller.versions, "get", return_value=version), \
             patch.object(self.controller.verifications, "get", return_value=evidence), \
             patch.object(ProcessTree, "identities", side_effect=OSError("checkpoint storage failed")):
            with self.assertRaisesRegex(OSError, "checkpoint storage failed"):
                manager.start(product, release)
        record = manager.list("product")[0]
        self.assertEqual((record["status"], record["error"]), ("failed", "checkpoint storage failed"))
        self.assertEqual(manager.processes, {})

    def test_shutdown_stops_all_services_within_one_grace_period(self):
        manager = self.studio.deployments
        signalled = []

        class SlowTree:
            """A service that uses its whole SIGTERM grace before it exits."""
            def stop(self):
                signalled.append(time.monotonic())

            def finish(self):
                time.sleep(0.6)
                return True

        class Exited:
            stdout = type("Stream", (), {"close": staticmethod(lambda: None)})()

            def wait(self, timeout=None):
                return 0

        for index in range(4):
            key = f"service-{index}"
            manager.save({"id": key, "product": "product", "created": time.time(), "status": "healthy",
                          "desired": "running", "processes": [], "log": ""})
            manager.processes[key] = (Exited(), SlowTree())
        started = time.monotonic()
        manager.close()
        self.assertLess(time.monotonic() - started, 4 * 0.6)
        self.assertEqual(len(signalled), 4)
        self.assertEqual(manager.processes, {})
        self.assertEqual({(r["status"], r["desired"]) for r in manager.list("product")}, {("interrupted", "running")})


@requires_sandbox
class DeploymentTests(DeploymentSetup, unittest.TestCase):
    current = helpers.ProductTests.current
    finish = helpers.ProductTests.finish
    create = helpers.ProductTests.create
    ready = helpers.ProductTests.ready
    accept = helpers.ProductTests.accept

    def service(self, buggy, tag):
        (self.project / "service.py").write_text(SERVICE.replace("BUGGY", str(buggy)).replace("VERSION_TAG", tag))

    def configured(self):
        self.service(True, "v1")
        p = self.create()
        self.ready(p)
        p = self.accept(p)
        return self.products.action({"id": p["id"], "action": "deployment_settings",
            "argv": [sys.executable, "service.py", "--host", "{host}", "--port", "{port}"],
            "health_path": "/health", "expected_text": "OK", "auto_repair": True})

    def until(self, key, status, timeout=8):
        manager = self.studio.deployments
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # Exercise real probes without making the test wait the production interval.
            for record in manager.list():
                record["next_check"] = 0
                manager.save(record)
            manager.tick()
            record = manager.get(key)
            if record["status"] == status:
                return record
            time.sleep(0.03)
        self.fail(str(manager.get(key)))

    def test_incident_repair_auto_release_and_rollback_use_real_health_evidence(self):
        p = self.configured()
        manager = self.studio.deployments
        first = manager.start(p, p["releases"][0])
        first = self.until(first["id"], "healthy")
        with urllib.request.urlopen(first["url"] + "/health") as response:
            self.assertEqual(response.read(), b"OK v1")
        marker = self.studio.data / "deployment-data" / p["id"] / "incident"
        marker.write_text("controlled incident")
        failed = self.until(first["id"], "unhealthy")
        p = self.products.get(p["id"])
        incident = [x for x in p["backlog"] if x.get("incident") == failed["incident"]]
        self.assertEqual(len(incident), 1)
        manager.tick()
        self.assertEqual(len(self.products.get(p["id"])["backlog"]), 2)
        # The delivery fixture proposes a repair; execution checks and deployment
        # probes remain real processes, not fabricated successful reports.
        self.service(False, "v2")
        self.ready(p, incident[0]["id"], content="OK repaired")
        self.products.action({"id": p["id"], "action": "deployment_settings", **p["deployment_settings"], "auto_release": True})
        self.products.action({"id": p["id"], "action": "settings", "autopilot": True})
        self.products.tick()  # verified acceptance + durable deployment intent
        p = self.products.get(p["id"])
        self.assertEqual(len(p["releases"]), 2)
        self.assertEqual(p["pending_deploy"], 2)
        self.products.tick()  # consume intent exactly once
        self.products.tick()
        second = next(r for r in manager.list(p["id"]) if r["number"] == 2)
        self.assertEqual(len([r for r in manager.list(p["id"]) if r["number"] == 2]), 1)
        second = self.until(second["id"], "healthy")
        with urllib.request.urlopen(second["url"] + "/health") as response:
            self.assertEqual(response.read(), b"OK v2")
        marker.unlink()
        rollback = manager.start(self.products.get(p["id"]), p["releases"][0])
        self.until(rollback["id"], "healthy")
        self.assertEqual(manager.get(second["id"])["status"], "stopped")
        with urllib.request.urlopen(rollback["url"] + "/health") as response:
            self.assertEqual(response.read(), b"OK v1")

    def test_failed_candidate_keeps_previous_service_and_restart_is_explicit(self):
        p = self.configured()
        manager = self.studio.deployments
        first = manager.start(p, p["releases"][0])
        self.until(first["id"], "healthy")
        bad = dict(p)
        bad["deployment_settings"] = {**p["deployment_settings"], "argv": [sys.executable, "-c", "raise SystemExit(9)", "{host}", "{port}"]}
        candidate = manager.start(bad, p["releases"][0])
        self.until(candidate["id"], "unhealthy")
        self.assertEqual(manager.get(first["id"])["status"], "healthy")
        self.products.action({"id": p["id"], "action": "deployment_settings", **p["deployment_settings"], "restart_on_start": True})
        manager.close()
        self.studio.deployments = Deployments(self.studio)
        self.addCleanup(self.studio.deployments.close)
        self.studio.deployments.tick()
        restored = self.studio.deployments.list(p["id"])[0]
        self.assertEqual(restored["recovery_count"], 1)
        self.until(restored["id"], "healthy")
        stable = self.studio.deployments.get(restored["id"])
        stable.update(healthy_at=time.time()-61, next_check=0)
        self.studio.deployments.save(stable)
        self.studio.deployments.tick()
        self.assertEqual(self.studio.deployments.get(restored["id"])["recovery_count"], 0)

    def test_unverified_release_cannot_be_deployed(self):
        p = self.configured()
        release = dict(p["releases"][0], verification_id=None)
        with self.assertRaisesRegex(ValueError, "independent checks"):
            self.studio.deployments.start(p, release)

    def test_service_signal_is_preserved_in_incident_diagnostics(self):
        p = self.configured()
        manager = self.studio.deployments
        first = manager.start(p, p["releases"][0])
        self.until(first["id"], "healthy")
        pair = manager.processes[first["id"]]
        children = [process for process in pair[1].refresh() if "service.py" in process.cmdline()]
        self.assertEqual(len(children), 1)
        children[0].terminate()
        pair[0].wait(timeout=3)
        failed = self.until(first["id"], "unhealthy")
        self.assertEqual(failed["health"][-1]["command_exit_code"], -15)
        self.assertEqual(failed["health"][-1]["signal"], "SIGTERM")
        self.assertIn("SIGTERM", self.products.get(p["id"])["backlog"][-1]["goal"])

    def test_unrelated_healthy_listener_cannot_validate_candidate(self):
        p = self.configured()
        manager = self.studio.deployments
        first = manager.start(p, p["releases"][0])
        first = self.until(first["id"], "healthy")
        candidate_spec = dict(p)
        candidate_spec["deployment_settings"] = {**p["deployment_settings"],
            "argv": [sys.executable, "-c", "import time; time.sleep(30)", "{host}", "{port}"]}
        candidate = manager.start(candidate_spec, p["releases"][0])
        candidate["url"] = first["url"]
        result = manager.probe(candidate)
        self.assertFalse(result["passed"])
        self.assertIn("own process", result["error"])

    def test_restart_failure_is_bounded_and_stop_cancels_pending_recovery(self):
        p = self.configured()
        manager = self.studio.deployments
        first = manager.start(p, p["releases"][0])
        self.until(first["id"], "healthy")
        self.products.action({"id": p["id"], "action": "deployment_settings", **p["deployment_settings"], "restart_on_start": True})
        manager.close()
        restarted = Deployments(self.studio)
        self.studio.deployments = restarted
        self.addCleanup(restarted.close)
        with patch.object(restarted, "start", side_effect=OSError("missing environment")) as start:
            for _ in range(5):
                restarted.tick()
            self.assertEqual(start.call_count, 3)
        self.assertIn("Service recovery failed", restarted.get(first["id"])["error"])
        restarted.pending_restart[p["id"]] = restarted.get(first["id"])
        restarted.stop(first["id"])
        with patch.object(restarted, "start") as start:
            restarted.tick()
            start.assert_not_called()

    def test_crash_after_acceptance_recovers_deployment_intent(self):
        self.service(False, "v1")
        p = self.create()
        self.ready(p)
        p = self.products.action({"id": p["id"], "action": "deployment_settings",
            "argv": [sys.executable, "service.py", "--host", "{host}", "--port", "{port}"], "auto_release": True})
        self.products.action({"id": p["id"], "action": "settings", "autopilot": True})
        accept = self.controller.action
        class Crash(BaseException):
            pass
        def accept_then_crash(body):
            result = accept(body)
            if body["action"] == "accept":
                raise Crash()
            return result
        with patch.object(self.controller, "action", side_effect=accept_then_crash):
            with self.assertRaises(Crash):
                self.products.tick()
        pending = self.products.get(p["id"])
        self.assertTrue(pending["pending_accept"])
        self.products.tick()
        recovered = self.products.get(p["id"])
        self.assertIsNone(recovered["pending_accept"])
        self.assertEqual(recovered["pending_deploy"], 1)
        self.assertEqual(len(recovered["releases"]), 1)
        self.products.tick()
        self.products.tick()
        self.assertEqual(len(self.studio.deployments.list(p["id"])), 1)

    def test_stop_cancels_queued_auto_deployment(self):
        p = self.configured()
        p.update(pending_deploy=1, pending_accept="unused")
        p["deployment_settings"]["auto_release"] = True
        self.products.save(p)
        stopped = self.products.action({"id": p["id"], "action": "deployment_stop"})
        self.assertIsNone(stopped["pending_deploy"])
        self.assertIsNone(stopped["pending_accept"])
        self.assertFalse(stopped["deployment_settings"]["auto_release"])
        self.assertEqual(self.studio.deployments.list(p["id"]), [])
