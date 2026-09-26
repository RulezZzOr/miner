"""A positive model report cannot override failed or missing execution evidence."""
import os
import signal
import socket
import subprocess
import sys
import time
import unittest

import psutil

from studio.tests import test_missions as helpers
from studio.tests.sandbox_support import requires_sandbox
from studio.verification import source_manifest, validate_checks
from studio.verification_worker import command_outcome


def processes_under(root):
    """Processes whose working directory or command line refers to a test directory."""
    found = []
    for process in psutil.process_iter(["pid"]):
        try:
            if process.pid != os.getpid() and (str(root) in process.cwd() or any(str(root) in a for a in process.cmdline())):
                found.append(process)
        except psutil.Error:
            pass
    return found


class VerificationTests(unittest.TestCase):
    setUp = helpers.MissionTests.setUp
    launch = helpers.MissionTests.launch
    stop = helpers.MissionTests.stop
    create = helpers.MissionTests.create
    start = helpers.MissionTests.start
    current = helpers.MissionTests.current
    finish = helpers.MissionTests.finish
    begin_build = helpers.MissionTests.begin_build
    deliver = helpers.MissionTests.deliver

    def assert_no_leaked_processes(self):
        deadline = time.monotonic() + 5
        while processes_under(self.root) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(processes_under(self.root), [])

    @requires_sandbox
    def test_forged_success_cannot_pass_real_failing_command(self):
        self.begin_build()
        m = self.current()
        m["verification_checks"] = validate_checks([{"argv": [sys.executable, "-c", "print('ACTUAL FAILURE'); raise SystemExit(7)"]}])
        self.controller.save(m)
        self.finish(helpers.report())
        m = self.finish(helpers.report("pass"))
        self.assertEqual(m["status"], "running")
        self.assertIsNone(m["final_report"])
        self.assertEqual(m["attempts"][-1]["phase"], "build")
        self.assertNotIn("final", [a["phase"] for a in m["attempts"]])
        record = self.controller.verifications.get(m["verification_result"]["id"])
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["checks"][0]["exit_code"], 7)
        self.assertIn("ACTUAL FAILURE", m["tasks"][-1]["feedback"])
        with self.assertRaises(ValueError):
            self.controller.action({"id": self.key, "action": "accept"})

    def test_missing_checks_require_explicit_manual_acceptance(self):
        self.begin_build()
        m = self.current()
        m["verification_checks"] = []
        self.controller.save(m)
        self.finish(helpers.report())
        self.finish(helpers.report("pass"))
        m = self.finish(helpers.report("pass", "Product is readable."))
        self.assertEqual(m["status"], "awaiting_checks")
        for action in ("accept", "manual_accept"):
            with self.assertRaises(ValueError):
                self.controller.action({"id": self.key, "action": action})
        m = self.controller.action({"id": self.key, "action": "manual_accept", "acknowledge_unverified": True})
        self.assertEqual(m["acceptance"]["kind"], "manual")

    @requires_sandbox
    def test_command_termination_records_the_actual_signal(self):
        m = self.create(verification_checks=[{"argv": [sys.executable, "-c",
            "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"]}])
        key = self.controller.verifications.start(m)
        deadline = time.monotonic() + 5
        while self.controller.verifications.get(key)["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.02)
        record = self.controller.verifications.get(key)
        self.assertEqual(record["status"], "failed")
        check = record["checks"][0]
        self.assertEqual(check["exit_code"], -signal.SIGTERM)
        self.assertEqual(check["wrapper_exit_code"], 1)
        self.assertEqual(check["signal"], "SIGTERM")

    @requires_sandbox
    def test_explicit_exit_143_is_not_misreported_as_sigterm(self):
        m = self.create(verification_checks=[{"argv": [sys.executable, "-c", "raise SystemExit(143)"]}])
        key = self.controller.verifications.start(m)
        deadline = time.monotonic() + 5
        while self.controller.verifications.get(key)["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.02)
        check = self.controller.verifications.get(key)["checks"][0]
        self.assertEqual(check["exit_code"], 143)
        self.assertNotIn("signal", check)

    def test_wrapper_failure_cannot_turn_child_success_into_a_pass(self):
        (self.root / "command-result.json").write_text('{"exit_code": 0}')
        outcome = command_outcome(self.root, 1)
        self.assertEqual(outcome["exit_code"], 1)
        self.assertEqual(outcome["command_exit_code"], 0)

    @requires_sandbox
    def test_nonartifact_source_change_invalidates_success(self):
        self.deliver()
        (self.project / "unreported.py").write_text("raise RuntimeError('new bug')")
        with self.assertRaisesRegex(ValueError, "Source files"):
            self.controller.action({"id": self.key, "action": "accept"})

    @requires_sandbox
    def test_permission_change_invalidates_verified_delivery(self):
        self.deliver()
        record = self.controller.verifications.get(self.current()["verification_id"])
        relative, mode = next(iter(record["source_modes"].items()))
        (self.project / relative).chmod(mode ^ 0o100)
        with self.assertRaisesRegex(ValueError, "File permissions"):
            self.controller.action({"id": self.key, "action": "accept"})

    @requires_sandbox
    def test_cancel_stops_a_real_check_process(self):
        m = self.create(verification_checks=[{"argv": [sys.executable, "-c", "import time; print('waiting', flush=True); time.sleep(30)"]}])
        key = self.controller.verifications.start(m)
        deadline = time.monotonic() + 3
        while not self.controller.verifications.get(key)["processes"] and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(self.controller.verifications.get(key)["processes"])
        self.controller.verifications.stop(key)
        self.controller.verifications.close()
        record = self.controller.verifications.get(key)
        self.assertEqual(record["status"], "cancelled")
        self.assertFalse(self.controller.verifications.active())
        self.assert_no_leaked_processes()

    @requires_sandbox
    def test_graceful_stop_lets_the_check_record_its_own_exit(self):
        code = ("import signal, sys, time\n"
                "signal.signal(signal.SIGINT, lambda *a: sys.exit(5))\n"
                "print('ready', flush=True)\n"
                "time.sleep(30)\n")
        m = self.create(verification_checks=[{"argv": [sys.executable, "-c", code]}])
        key = self.controller.verifications.start(m)
        deadline = time.monotonic() + 10
        while not self.controller.verifications.get(key)["processes"] and time.monotonic() < deadline:
            time.sleep(0.02)
        time.sleep(1.5)  # let the sandboxed check install its handler
        self.controller.verifications.stop(key)
        self.controller.verifications.close()
        check = self.controller.verifications.get(key)["checks"][0]
        # The sandbox survived the interrupt long enough for the check's own handler to exit with 5.
        self.assertEqual(check["command_exit_code"], 5)
        self.assert_no_leaked_processes()

    def test_quoted_commands_are_arguments_not_shell_interpolation(self):
        checks = validate_checks("python -c 'print(\"hello world\")'")
        self.assertEqual(checks[0]["argv"], ["python", "-c", 'print("hello world")'])

    def test_named_pipes_and_sockets_are_skipped_without_waiting_for_a_writer(self):
        (self.project / "app.py").write_text("print('ok')")
        os.mkfifo(self.project / "pipe")
        server = socket.socket(socket.AF_UNIX)
        self.addCleanup(server.close)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.project)  # a relative name keeps the socket path short
        server.bind("service.sock")
        self.assertEqual(set(source_manifest(self.project)), {"app.py"})

    def test_virtual_environments_and_tool_caches_are_not_source(self):
        (self.project / "app.py").write_text("print('ok')")
        for name in ("venv", "py311-env"):
            subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(self.project / name)], check=True)
        for cache in (".tox", ".cache", ".next"):
            (self.project / cache).mkdir()
            (self.project / cache / "data").write_text("generated")
        self.assertEqual(set(source_manifest(self.project)), {"app.py"})
        version = self.controller.versions.snapshot(self.project, label="with venv")
        self.assertEqual(set(version["files"]), {"app.py"})

    def test_stray_pyvenv_cfg_does_not_hide_source(self):
        # Only a real, conventionally named environment is skipped; a marker file alone hides nothing.
        (self.project / "src").mkdir()
        (self.project / "src" / "app.py").write_text("print('ok')")
        (self.project / "src" / "pyvenv.cfg").write_text("home = /usr\n")
        (self.project / "env").mkdir()
        (self.project / "env" / "pyvenv.cfg").write_text("home = /usr\n")
        (self.project / "env" / "settings.py").write_text("DEBUG = False")
        self.assertEqual(set(source_manifest(self.project)),
                         {"src/app.py", "src/pyvenv.cfg", "env/pyvenv.cfg", "env/settings.py"})

    def test_project_symlink_is_named_in_the_error(self):
        (self.project / "link.txt").symlink_to(self.project / "missing.txt")
        with self.assertRaisesRegex(ValueError, "link.txt"):
            source_manifest(self.project)
