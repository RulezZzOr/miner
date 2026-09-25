"""Install a release into a clean path with spaces; check HTTP, shutdown and optional tests."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path


def smoke(archive: Path, suite: bool) -> None:
    with tempfile.TemporaryDirectory(prefix="Switch Studio clean install ") as folder:
        dest = Path(folder)
        if archive.name.endswith(".zip"):
            with zipfile.ZipFile(archive) as source:
                source.extractall(dest)
        else:
            with tarfile.open(archive) as source:
                source.extractall(dest, filter="data")
        root, = dest.iterdir()
        # Use sh explicitly: Python zipfile does not restore executable bits.
        subprocess.run(["sh", str(root / "setup-studio")], cwd=root, check=True)
        original = (root / "agent.toml").read_bytes()
        python = root / "frontier/.venv/bin/python"
        subprocess.run([str(python), str(root / "scripts/start_studio.py"), "--check"], check=True)
        assert (root / "agent.toml").read_bytes() == original, "Config overwritten"
        with (dest / "server.log").open("w+") as log:
            process = subprocess.Popen(
                ["sh", str(root / "switch-studio"), "--no-open", "--port", "0"],
                cwd=root, stdout=log, stderr=subprocess.STDOUT,
            )
            try:
                saved = root / ".switch-agent/studio/server.json"
                deadline = time.monotonic() + 30
                while not saved.exists():
                    if process.poll() is not None or time.monotonic() > deadline:
                        log.seek(0)
                        raise RuntimeError("Server failed to start: " + log.read())
                    time.sleep(0.1)
                port = json.loads(saved.read_text())["port"]
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as response:
                    assert response.status == 200
                    assert b"Sign in to Miner" in response.read()
                key = (root / ".switch-agent/studio/access-key").read_text().strip()
                request = urllib.request.Request(f"http://127.0.0.1:{port}/api/state", headers={"Authorization": "Bearer " + key})
                with urllib.request.urlopen(request, timeout=10) as response:
                    assert "token" in json.load(response)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                    raise
            assert process.returncode == 0, f"Unclean shutdown: {process.returncode}"
        if suite:
            # Clear inherited PYTHONPATH so tests cannot accidentally import the development checkout.
            env = dict(os.environ)
            env.pop("PYTHONPATH", None)
            subprocess.run(
                [str(python), "-m", "unittest", "discover", "-s", "studio/tests", "-v"],
                cwd=root, env=env, check=True,
            )
        print(f"PASS: clean install, HTTP 200, graceful shutdown{' and suite' if suite else ''}: {archive.name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--suite", action="store_true")
    args = parser.parse_args()
    smoke(args.archive.resolve(), args.suite)
