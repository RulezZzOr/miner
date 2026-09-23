"Sledování aktivace a ztráty rodiče pro kontroly schválené řídicím modulem."
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def command_outcome(directory, wrapper_code):
    "Zachovat signál skutečného potomka, při selhání obalového procesu považovat za selhání."
    result = {"exit_code": wrapper_code, "wrapper_exit_code": wrapper_code}
    try:
        data = json.loads((Path(directory) / "command-result.json").read_text())
        code = data["exit_code"]
        if isinstance(code, bool) or not isinstance(code, int):
            return result
        result["command_exit_code"] = code
        if wrapper_code == (code if code >= 0 else 1):
            result["exit_code"] = code
        if code < 0:
            result["signal"] = signal.Signals(-code).name
    except (OSError, ValueError, KeyError, TypeError):
        pass  # Interrupted or older workers may not have a child exit record.
    return result


def main():
    directory = Path(sys.argv[1])
    request = json.loads((directory / "request.json").read_text())
    parent = request["parent"]
    deadline = time.monotonic() + 30
    while not (directory / "activated").exists():
        if os.getppid() != parent or time.monotonic() > deadline:
            return 1
        time.sleep(0.05)
    if os.getppid() != parent:
        return 1
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **request.get("environment", {})}
    process = subprocess.Popen(request["argv"], cwd=request["cwd"], env=env)
    while process.poll() is None:
        if os.getppid() != parent:
            os.killpg(os.getpgrp(), signal.SIGKILL)
        time.sleep(0.05)
    result = {"exit_code": process.returncode, "ended": time.time()}
    temporary = directory / "command-result.tmp"
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(result, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(directory / "command-result.json")
    return process.returncode if process.returncode >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
