"""A positive model report cannot override failed or missing execution evidence."""
import os
import signal
import sys
import time
import unittest

from studio.tests import test_missions as helpers
from studio.verification import source_manifest, validate_checks
from studio.verification_worker import command_outcome


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

    def test_forged_success_cannot_pass_real_failing_command(self):
        self.begin_build()
        m = self.current()
        m["verification_checks"] = validate_checks([{"argv": [sys.executable, "-c", "print('ACTUAL FAILURE'); raise SystemExit(7)"]}])
        self.controller.save(m)
        self.finish(helpers.report())
        self.finish(helpers.report("pass"))
        m = self.finish(helpers.report("pass", "Produkt lze přečíst."))
        self.assertEqual(m["status"], "running")
        self.assertIsNone(m["final_report"])
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
        m = self.finish(helpers.report("pass", "Produkt lze přečíst."))
        self.assertEqual(m["status"], "awaiting_checks")
        for action in ("accept", "manual_accept"):
            with self.assertRaises(ValueError):
                self.controller.action({"id": self.key, "action": action})
        m = self.controller.action({"id": self.key, "action": "manual_accept", "acknowledge_unverified": True})
        self.assertEqual(m["acceptance"]["kind"], "manual")

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

    def test_wrapper_failure_cannot_turn_child_success_into_a_pass(self):
        (self.root / "command-result.json").write_text('{"exit_code": 0}')
        outcome = command_outcome(self.root, 1)
        self.assertEqual(outcome["exit_code"], 1)
        self.assertEqual(outcome["command_exit_code"], 0)

    def test_nonartifact_source_change_invalidates_success(self):
        self.deliver()
        (self.project / "unreported.py").write_text("raise RuntimeError('new bug')")
        with self.assertRaisesRegex(ValueError, "Zdrojové soubory"):
            self.controller.action({"id": self.key, "action": "accept"})

    def test_permission_change_invalidates_verified_delivery(self):
        self.deliver()
        record = self.controller.verifications.get(self.current()["verification_id"])
        relative, mode = next(iter(record["source_modes"].items()))
        (self.project / relative).chmod(mode ^ 0o100)
        with self.assertRaisesRegex(ValueError, "Práva souborů"):
            self.controller.action({"id": self.key, "action": "accept"})

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

    def test_quoted_commands_are_arguments_not_shell_interpolation(self):
        checks = validate_checks("python -c 'print(\"hello world\")'")
        self.assertEqual(checks[0]["argv"], ["python", "-c", 'print("hello world")'])

    def test_named_pipe_is_rejected_without_waiting_for_a_writer(self):
        os.mkfifo(self.project / "pipe")
        with self.assertRaisesRegex(ValueError, "speciální soubor"):
            source_manifest(self.project)
