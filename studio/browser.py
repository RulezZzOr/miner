"""Open a local Studio URL in the host browser, including Windows from WSL2."""
from __future__ import annotations

import re
import shutil
import subprocess
import webbrowser
from pathlib import Path


def open_browser(url: str) -> None:
    # Only our numeric loopback URL can cross the Windows command interpreter.
    if not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}", url):
        return
    try:
        wsl = "microsoft" in Path("/proc/sys/kernel/osrelease").read_text().lower()
    except OSError:
        wsl = False
    if wsl and (command := shutil.which("cmd.exe")):
        try:
            result = subprocess.run(
                [command, "/c", "start", "", url],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
            )
            if result.returncode == 0:
                return
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        webbrowser.open(url)
    except webbrowser.Error:
        pass  # A headless host can still serve the URL printed by the caller.
