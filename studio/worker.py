"Spusťte runner až po tom, co Studio trvale zaznamená identitu svého procesu."
import json
import os
import sys
import time
from pathlib import Path


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
    os.execv(sys.executable, [sys.executable, "-u", str(Path(__file__).with_name(runner)), str(directory)])


if __name__ == "__main__":
    raise SystemExit(main())
