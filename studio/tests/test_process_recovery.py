"""Crash recovery must clean recorded processes without killing reused PIDs."""
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import psutil

from studio import process_tree
from studio.isolation import isolated_command
from studio.process_tree import ProcessTree
from studio.tests.sandbox_support import requires_sandbox

SLEEPER = [sys.executable, "-c", "import time;time.sleep(30)"]


def gone(pid):
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


class RecoveryTests(unittest.TestCase):
    def spawn(self, argv=SLEEPER, **kwargs):
        process = subprocess.Popen(argv, **{"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, **kwargs})

        def cleanup():
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
        self.addCleanup(cleanup)
        return process

    def test_matching_identity_is_stopped_but_stale_identity_is_not(self):
        process = self.spawn()
        identity = psutil.Process(process.pid).create_time()
        self.assertTrue(ProcessTree.recover([{"pid": process.pid, "created": identity - 1}]))
        self.assertIsNone(process.poll())
        self.assertTrue(ProcessTree.recover([{"pid": process.pid, "created": identity}]))
        process.wait(timeout=3)
        self.assertIsNotNone(process.returncode)

    def test_boot_relative_start_matches_only_within_the_recorded_boot(self):
        process = self.spawn()
        with patch.object(process_tree, "boot_id", return_value="boot-a"), \
             patch.object(process_tree, "started", return_value=120.5):
            identity = ProcessTree(process.pid).identities()[0]
            self.assertEqual((identity["boot"], identity["started"]), ("boot-a", 120.5))
            # After a reboot, another process can reuse the PID at the same offset since boot.
            for record in ({**identity, "boot": "boot-b", "created": identity["created"] - 600},
                           {k: v for k, v in identity.items() if k != "boot"} | {"created": identity["created"] - 600}):
                self.assertTrue(ProcessTree.recover([record]))
                self.assertIsNone(process.poll())
            self.assertTrue(ProcessTree.recover([{**identity, "created": identity["created"] + 30}]))
        process.wait(timeout=3)

    def test_boot_relative_start_survives_a_wall_clock_step(self):
        process = self.spawn()
        identity = ProcessTree(process.pid).identities()[0]
        if identity["started"] is None or not identity["boot"]:
            self.skipTest("no boot-relative process start time on this platform")
        stale = {**identity, "started": identity["started"] - 5}
        self.assertTrue(ProcessTree.recover([stale]))
        self.assertIsNone(process.poll())
        # An NTP step moves create_time(); the boot-relative start still identifies the process.
        stepped = {**identity, "created": identity["created"] + 30}
        self.assertTrue(ProcessTree.recover([stepped]))
        process.wait(timeout=3)

    def test_child_left_in_the_root_group_is_killed_after_its_parent_exits(self):
        code = ("import subprocess, sys, time; time.sleep(0.4);"
                "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']);"
                "print(c.pid, flush=True)")
        root = self.spawn([sys.executable, "-c", code], stdout=subprocess.PIPE, start_new_session=True)
        tree = ProcessTree(root.pid, grace=0.5)
        orphan = int(root.stdout.readline())
        root.wait(timeout=5)
        root.stdout.close()
        self.addCleanup(lambda: gone(orphan) or os.kill(orphan, signal.SIGKILL))
        self.assertFalse(gone(orphan))
        self.assertTrue(tree.finish())
        deadline = time.monotonic() + 3
        while not gone(orphan) and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(gone(orphan))

    def test_graceful_stop_spares_the_sandbox_monitor_until_the_grace_ends(self):
        monitor = self.spawn(start_new_session=True)
        tree = ProcessTree(monitor.pid, grace=0.6)
        with patch.object(ProcessTree, "sandbox_monitor", staticmethod(lambda p: p.pid == monitor.pid)):
            tree.stop()
            time.sleep(0.3)
            self.assertIsNone(monitor.poll())  # SIGINT would have ended it at once
            self.assertTrue(tree.finish())
        self.assertEqual(monitor.wait(timeout=3), -signal.SIGKILL)

    def test_refresh_scans_the_process_table_at_a_bounded_rate(self):
        process = self.spawn()
        tree = ProcessTree(process.pid)
        calls = []
        original = process_tree.ppid_map
        with patch.object(process_tree, "ppid_map", lambda: calls.append(1) or original()):
            for _ in range(20):
                tree.refresh()
        self.assertLessEqual(len(calls), 1)
        self.assertEqual({p.pid for p in tree.refresh(force=True)}, {process.pid})

    @requires_sandbox
    def test_sandboxed_payload_gets_the_interrupt_before_bubblewrap_is_killed(self):
        with tempfile.TemporaryDirectory() as d:
            workspace = Path(d)
            code = ("import signal, sys, time, pathlib\n"
                    "def stop(*_):\n"
                    "    time.sleep(0.3)\n"
                    "    pathlib.Path('interrupted').write_text('saved')\n"
                    "    sys.exit(0)\n"
                    "signal.signal(signal.SIGINT, stop)\n"
                    "pathlib.Path('ready').write_text('')\n"
                    "time.sleep(30)\n")
            sandbox = self.spawn(isolated_command([sys.executable, "-c", code], workspace), start_new_session=True)
            deadline = time.monotonic() + 15
            while not (workspace / "ready").exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            tree = ProcessTree(sandbox.pid, grace=5)
            self.assertTrue(tree.finish())
            self.assertEqual((workspace / "interrupted").read_text(), "saved")


if __name__ == "__main__":
    unittest.main()
