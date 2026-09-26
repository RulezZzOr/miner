"""Activation and parent-loss watchdog for controller-approved checks."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

# Upper bound for a requested stop. The controller's process tree normally ends the
# sandbox first; this only prevents a worker from waiting forever for a stuck child.
STOP_LIMIT_SECONDS = 30


def command_outcome(directory, wrapper_code):
    """Keep the actual child's signal while treating wrapper failure as failure."""
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


def die_with_parent(parent):
    """Child hook: the sandbox monitor receives SIGKILL if this worker disappears (Linux)."""
    def hook():
        try:
            import ctypes
            ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0)  # PR_SET_PDEATHSIG
        except (OSError, AttributeError):
            pass
        if os.getppid() != parent:
            os._exit(1)  # The worker died before the hook was armed.
    return hook if sys.platform == "linux" else None


def main():
    directory = Path(sys.argv[1])
    request = json.loads((directory / "request.json").read_text())
    parent = request["parent"]
    # A stop request must never kill this worker between spawning the sandbox and
    # waiting for it: the controller discovers the sandbox through this process, so
    # dying first would orphan the service. Record the request and wait instead.
    stop_requested = []
    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, lambda signum, frame: stop_requested.append(time.monotonic()))
    deadline = time.monotonic() + 30
    while not (directory / "activated").exists():
        if os.getppid() != parent or time.monotonic() > deadline or stop_requested:
            return 1
        time.sleep(0.05)
    if os.getppid() != parent or stop_requested:
        return 1
    try:
        from studio.isolation import isolated_command, clean_environment
    except ImportError:
        from isolation import isolated_command, clean_environment
    environment = request.get('environment', {})
    writable = [environment['SWITCH_DATA_DIR']] if environment.get('SWITCH_DATA_DIR') else []
    status = directory.resolve() / 'child-status.json'
    fd = os.open(status, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    child_argv = [sys.executable, str(Path(__file__).with_name('isolated_check.py').resolve()), str(status), json.dumps(request['argv'])]
    data = request.get('controller_data')
    command = isolated_command(child_argv, request['cwd'], writable=[*writable, status], environment=environment,
                               protected=[data] if data else [], managed_root=data)
    process = subprocess.Popen(command, cwd=request['cwd'], env=clean_environment(os.environ),
                               preexec_fn=die_with_parent(os.getpid()))
    while process.poll() is None:
        if os.getppid() != parent or stop_requested and time.monotonic() - stop_requested[0] > STOP_LIMIT_SECONDS:
            os.killpg(os.getpgrp(), signal.SIGKILL)
        time.sleep(0.05)
    code = process.returncode
    try:
        raw = json.loads(status.read_text())["exit_code"]
        if isinstance(raw, int) and not isinstance(raw, bool) and code == (raw if raw >= 0 else 1):
            code = raw
    except (OSError, ValueError, KeyError, TypeError):
        # No valid child outcome means the check cannot be reported as a success.
        code = code or 1
    result = {"exit_code": code, "sandbox_exit_code": process.returncode, "ended": time.time()}
    temporary = directory / "command-result.tmp"
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(result, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(directory / "command-result.json")
    return code if code >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
