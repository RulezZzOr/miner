"""Native file tools must use the installed runtime, not system Python."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import venv

from studio.worker import worker_environment


class WorkerEnvironmentTests(unittest.TestCase):
    def test_system_service_path_cannot_hide_runtime_packages(self):
        with tempfile.TemporaryDirectory(prefix="studio runtime ") as directory:
            root = Path(directory) / "venv"
            venv.EnvBuilder(with_pip=False).create(root)
            bindir = root / ("Scripts" if os.name == "nt" else "bin")
            python = bindir / ("python.exe" if os.name == "nt" else "python3")
            site = subprocess.check_output([str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True).strip()
            Path(site, "studio_reader_dependency.py").write_text("VALUE = 'installed-runtime'\n")
            original = {"PATH": os.defpath}
            env = worker_environment(str(python), original)
            command = "python" if os.name == "nt" else "python3"
            output = subprocess.check_output([command, "-c", "import studio_reader_dependency as d; print(d.VALUE)"], env=env, text=True)
            self.assertEqual(output.strip(), "installed-runtime")
            self.assertEqual(original, {"PATH": os.defpath})

    def test_missing_path_retains_platform_command_search(self):
        env = worker_environment(sys.executable, {})
        self.assertTrue(env["PATH"].endswith(os.pathsep + os.defpath))
