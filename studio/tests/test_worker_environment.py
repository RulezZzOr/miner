"""Worker shells must use the installed runtime, not a system Python found on PATH.

The worker PATH is the one isolated_command passes to bubblewrap; no other code
path builds it.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio.isolation import isolated_command
from studio.tests.sandbox_support import requires_sandbox


class WorkerEnvironmentTests(unittest.TestCase):
    def test_sandbox_path_starts_with_the_unresolved_runtime_directory(self):
        with tempfile.TemporaryDirectory() as project, \
             patch('studio.isolation.sys.platform', 'linux'), \
             patch('studio.isolation.shutil.which', return_value='/usr/bin/bwrap'):
            cmd = isolated_command(['true'], project)
        environment = {cmd[i + 1]: cmd[i + 2] for i, arg in enumerate(cmd) if arg == '--setenv'}
        # Do not resolve the executable symlink: its directory identifies the venv.
        self.assertEqual(environment['PATH'].split(':')[0], str(Path(sys.executable).parent))
        self.assertEqual(environment['HOME'], '/home/worker')
        self.assertNotIn('--setenv', cmd[cmd.index('--'):])

    @requires_sandbox
    def test_python_on_worker_path_sees_runtime_packages(self):
        with tempfile.TemporaryDirectory() as project:
            code = 'import sys, psutil, yaml; print(sys.prefix)'
            p = subprocess.run(isolated_command(['python3', '-c', code], project), capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), sys.prefix)


if __name__ == '__main__':
    unittest.main()
