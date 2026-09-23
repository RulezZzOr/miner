"""Owner-controlled HTTP acceptance check for the isolated live-model pilot."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path


def main():
    with socket.socket() as allocation:
        allocation.bind(("127.0.0.1", 0))
        port = allocation.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="switch-http-check-") as temporary, tempfile.TemporaryFile() as log:
        process = subprocess.Popen([sys.executable, "service.py", "--host", "127.0.0.1", "--port", str(port)],
            stdout=log, stderr=subprocess.STDOUT, env={**os.environ, "SWITCH_DATA_DIR": temporary})
        try:
            base = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 15
            while True:
                if process.poll() is not None:
                    log.seek(0)
                    raise AssertionError("Service exited: " + log.read(8000).decode(errors="replace"))
                try:
                    with urllib.request.urlopen(base + "/health", timeout=1) as response:
                        health = json.load(response)
                    assert health == {"status": "ok"}, health
                    break
                except (OSError, ValueError):
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.1)
            for value, expected in [("Český Krumlov", "cesky-krumlov"), (" A___B / 42 ", "a-b-42"),
                                    ("", "item"), ("東京", "item"), ("ABC", "abc")]:
                with urllib.request.urlopen(base + "/slug?" + urllib.parse.urlencode({"text": value}), timeout=2) as response:
                    actual = json.load(response)
                assert actual == {"slug": expected}, (value, actual)
            assert Path("README.md").is_file()
            print("PASS: actual HTTP health, 5 slug requests, README")
        finally:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


if __name__ == "__main__":
    main()
