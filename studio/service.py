"""Optional per-user macOS service for Studio (no root, no public listener)."""
import argparse
import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABEL = "local.switch-studio"


def definition(root=ROOT):
    data = root / ".switch-agent" / "studio"
    return {
        "Label": LABEL,
        "ProgramArguments": [str(root / "switch-studio"), "--no-open"],
        "WorkingDirectory": str(root),
        "RunAtLoad": True,
        "Umask": 0o077,
        "KeepAlive": True,
        "ThrottleInterval": 15,
        "ExitTimeOut": 20,
        "StandardOutPath": str(data / "service.log"),
        "StandardErrorPath": str(data / "service-error.log"),
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    }


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Switch Studio: macOS user service")
    parser.add_argument("action", choices=["enable", "disable", "status"])
    args = parser.parse_args()
    if sys.platform != "darwin":
        raise SystemExit("This installer is for macOS only. On Linux, run switch-studio as a user systemd service.")
    domain = f"gui/{os.getuid()}"
    service = f"{domain}/{LABEL}"
    path = Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")
    if args.action == "status":
        raise SystemExit(subprocess.run(["launchctl", "print", service], check=False).returncode)
    if args.action == "disable":
        subprocess.run(["launchctl", "bootout", service], check=False)
        if path.exists():
            path.unlink()
        print("Service disabled. Project data remains preserved.")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    (ROOT / ".switch-agent" / "studio").mkdir(parents=True, exist_ok=True)
    if subprocess.run(["launchctl", "print", service], capture_output=True).returncode == 0:
        print("Service is already enabled.")
        return
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(plistlib.dumps(definition()))
    temporary.replace(path)
    subprocess.run(["launchctl", "bootstrap", domain, str(path)], check=True)
    for _ in range(10):
        time.sleep(0.5)
        result = subprocess.run(["launchctl", "print", service], capture_output=True, text=True)
        if "state = running" in result.stdout:
            # A shell can start briefly before macOS denies the script path.
            import urllib.request
            try:
                with urllib.request.urlopen("http://127.0.0.1:4317/", timeout=1):
                    print("Service is responding. The computer must remain powered on and awake.")
                    return
            except OSError:
                pass
    subprocess.run(["launchctl", "bootout", service], check=False)
    path.unlink(missing_ok=True)
    raise SystemExit("Service failed to start and was disabled. Check service-error.log; macOS may block the Documents folder. Run standard Studio via ./switch-studio.")


if __name__ == "__main__":
    main()
