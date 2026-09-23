"Volitelná uživatelská služba macOS pro Studio (bez root, bez veřejného posluchače)."
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
        "KeepAlive": True,
        "ThrottleInterval": 15,
        "ExitTimeOut": 20,
        "StandardOutPath": str(data / "service.log"),
        "StandardErrorPath": str(data / "service-error.log"),
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    }


def main():
    parser = argparse.ArgumentParser(description="Switch Studio: uživatelská služba macOS")
    parser.add_argument("action", choices=["enable", "disable", "status"])
    args = parser.parse_args()
    if sys.platform != "darwin":
        raise SystemExit("Tento instalátor je určený pro macOS. Na Linuxu spusť switch-studio jako uživatelskou systemd službu.")
    domain = f"gui/{os.getuid()}"
    service = f"{domain}/{LABEL}"
    path = Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")
    if args.action == "status":
        raise SystemExit(subprocess.run(["launchctl", "print", service], check=False).returncode)
    if args.action == "disable":
        subprocess.run(["launchctl", "bootout", service], check=False)
        if path.exists():
            path.unlink()
        print("Služba vypnuta. Projektová data zůstávají zachovaná.")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    (ROOT / ".switch-agent" / "studio").mkdir(parents=True, exist_ok=True)
    if subprocess.run(["launchctl", "print", service], capture_output=True).returncode == 0:
        print("Služba už je zapnutá.")
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
                with urllib.request.urlopen("http://127.0.0.1:4317/api/state", timeout=1):
                    print("Služba odpovídá. Počítač musí zůstat zapnutý a vzhůru.")
                    return
            except OSError:
                pass
    subprocess.run(["launchctl", "bootout", service], check=False)
    path.unlink(missing_ok=True)
    raise SystemExit("Služba nenastartovala a byla vypnuta. Zkontroluj service-error.log; macOS může blokovat složku Dokumenty. Běžné Studio spusť přes ./switch-studio.")


if __name__ == "__main__":
    main()
