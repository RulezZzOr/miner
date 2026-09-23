"""Crash recovery must clean recorded processes without killing reused PIDs."""
import subprocess
import sys
import unittest

import psutil

from studio.process_tree import ProcessTree


class RecoveryTests(unittest.TestCase):
    def test_matching_identity_is_stopped_but_stale_identity_is_not(self):
        process = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        identity = psutil.Process(process.pid).create_time()
        try:
            self.assertTrue(ProcessTree.recover([{"pid": process.pid, "created": identity - 1}]))
            self.assertIsNone(process.poll())
            self.assertTrue(ProcessTree.recover([{"pid": process.pid, "created": identity}]))
            process.wait(timeout=3)
            self.assertIsNotNone(process.returncode)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
