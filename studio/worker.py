"""Start a runner only after Studio has durably recorded its process identity."""
import json
import os
import sys
import time
from pathlib import Path


def worker_environment(executable, environ):
    """Keep native tools on the runner's Python, even under systemd's PATH."""
    env = dict(environ)
    # Do not resolve the executable symlink: its directory identifies the venv.
    bindir = str(Path(executable).absolute().parent)
    env["PATH"] = bindir + os.pathsep + env.get("PATH", os.defpath)
    return env


def main():
    directory = Path(sys.argv[1])
    parent = os.getppid()
    deadline = time.monotonic() + 30
    while not (directory / "activated").exists():
        if os.getppid() != parent or time.monotonic() >= deadline:
            return 1
        time.sleep(0.05)
    if os.getppid() != parent:
        return 1
    request = json.loads((directory / "request.json").read_text())
    runner = "codex_runner.py" if request.get("backend") == "codex" else "runner.py"
    os.execve(sys.executable, [sys.executable, "-u", str(Path(__file__).with_name(runner)), str(directory)],
              worker_environment(sys.executable, os.environ))


if __name__ == "__main__":
    raise SystemExit(main())
