"""Controller-owned command evidence. Model reports cannot manufacture an exit code.

Commands are approved through the owner API, run without a shell, and bind to a
source manifest before and after execution. Linux bubblewrap confines commands to
the selected workspace and explicit runtime mounts; network access remains enabled.
"""
from __future__ import annotations

import hashlib
import json
import os
import selectors
import shlex
import stat
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

try:
    from .process_tree import ProcessTree
    from .test_evidence import check_kind, test_count, git_revision
    from .verification_worker import command_outcome
except ImportError:
    from process_tree import ProcessTree
    from test_evidence import check_kind, test_count, git_revision
    from verification_worker import command_outcome

IGNORED = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache",
           ".mypy_cache", ".switch-agent", ".apodex", ".DS_Store"}


def source_manifest(root):
    """Bounded source fingerprint; no symlink traversal or silent truncation."""
    try:
        from .server import read_project_file
    except ImportError:
        from server import read_project_file
    root = Path(root)
    result, total = {}, 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED and not d.casefold().startswith(".env"))
        for name in sorted(files + [d for d in dirs if (Path(directory) / d).is_symlink()]):
            path = Path(directory) / name
            rel = path.relative_to(root).as_posix()
            if name in IGNORED or name.casefold().startswith(".env") or name.startswith(".studio-") or rel.startswith("company/projects/"):
                continue
            if path.is_symlink():
                raise ValueError(f"Verification requires actual files, not a symlink: {rel}")
            info = path.stat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"Verification does not accept a special file: {rel}")
            if info.st_size > 50_000_000 or len(result) >= 20000:
                raise ValueError("Source files exceeded verification limits (50 MB/file, 500 MB, 20,000 files).")
            data = read_project_file(root, rel, min(50_000_000, 500_000_000 - total))
            total += len(data)
            result[rel] = hashlib.sha256(data).hexdigest()
    return result


def source_modes(root, manifest):
    """Permission bits are part of the verified source state, including executability."""
    try:
        from .server import file_parent
    except ImportError:
        from server import file_parent
    result = {}
    for path in manifest:
        with file_parent(Path(root), path) as (parent, name):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                mode = os.fstat(fd).st_mode
                if not stat.S_ISREG(mode):
                    raise ValueError("Permissions can be verified only for a regular file: " + path)
                result[path] = stat.S_IMODE(mode) & 0o777
            finally:
                os.close(fd)
    return result


def validate_checks(value):
    if isinstance(value, str):
        value = [{"argv": shlex.split(line), "label": line[:200]} for line in value.splitlines() if line.strip()]
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError("Checks must be a list of at most 20 commands.")
    result = []
    for index, item in enumerate(value):
        argv = item.get("argv") if isinstance(item, dict) else None
        if (not isinstance(argv, list) or not 1 <= len(argv) <= 100 or
                any(not isinstance(a, str) or not a or len(a) > 4000 or "\0" in a for a in argv)):
            raise ValueError("Check command must contain a non-empty list of arguments argv.")
        timeout = item.get("timeout", 300)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 3600:
            raise ValueError("Check timeout must be 1–3600 seconds.")
        kind = item.get("kind", check_kind(argv))
        minimum = item.get("minimum_tests", 1)
        if kind not in {"command", "test"} or isinstance(minimum, bool) or not isinstance(minimum, int) or not 1 <= minimum <= 1000000:
            raise ValueError("Check kind must be command or test, with a positive minimum_tests count.")
        result.append({"id": str(index + 1), "label": str(item.get("label", argv[0]))[:200],
                       "argv": argv, "timeout": timeout, "kind": kind, "minimum_tests": minimum})
    return result


class Verifications:
    def __init__(self, missions):
        self.missions = missions
        self.studio = missions.studio
        self.lock = threading.RLock()
        self.threads, self.cancels = {}, {}
        with missions.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS verifications (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            records = [json.loads(r[0]) for r in db.execute("SELECT data FROM verifications")]
        for record in records:
            if record["status"] == "running":
                clean = ProcessTree.recover(record.get("processes", []))
                record.update(status="interrupted", ended=time.time(),
                              error="Studio was restarted; check must run again.", cleanup=clean)
                self.save(record)

    def save(self, record):
        with self.missions.connect() as db:
            db.execute("INSERT INTO verifications VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                       (record["id"], json.dumps(record, ensure_ascii=False)))

    def get(self, key):
        with self.missions.connect() as db:
            row = db.execute("SELECT data FROM verifications WHERE id=?", (key,)).fetchone()
        if not row:
            raise ValueError("Independent check record does not exist.")
        return json.loads(row[0])

    def active(self):
        with self.lock:
            return any(t.is_alive() for t in self.threads.values())

    def start(self, m):
        with self.lock:
            key = uuid.uuid4().hex[:16]
            record = {"id": key, "mission": m["id"], "status": "running", "started": time.time(),
                      "root": m["workspace"], "specs": validate_checks(m["verification_checks"]),
                      "checks": [], "processes": []}
            if not record["specs"]:
                raise ValueError("Missing independent verification commands.")
            self.save(record)
            cancel = threading.Event()
            self.cancels[key] = cancel
            thread = threading.Thread(target=self.run, args=(record, cancel), daemon=True, name="studio-checks")
            self.threads[key] = thread
            thread.start()
            return key

    def stop(self, key):
        with self.lock:
            if key in self.cancels:
                self.cancels[key].set()

    def run(self, record, cancel):
        try:
            before = source_manifest(record["root"])
            record["sources"] = before
            record["git_revision"] = git_revision(record["root"])
            record["source_modes"] = source_modes(record["root"], before)
            self.save(record)
            for spec in record["specs"]:
                if cancel.is_set():
                    raise RuntimeError("Verification was paused.")
                check = self.command(record, spec, cancel)
                if spec.get("kind") == "test":
                    check["test_count"] = test_count(check.get("log", ""))
                    if check["test_count"] is None or check["test_count"] < spec.get("minimum_tests", 1):
                        check["error"] = "Test evidence is missing or below the required test count. Provide a supported test-runner summary."
                record["checks"].append(check)
                self.save(record)
                if check["exit_code"] != 0 or check.get("error"):
                    raise RuntimeError(check.get("error") or f"Check {spec['label']} exited with code {check['exit_code']}.")
            after = source_manifest(record["root"])
            if before != after or source_modes(record["root"], after) != record["source_modes"]:
                raise RuntimeError("Files changed during verification. Re-verify the final content.")
            if git_revision(record["root"]) != record["git_revision"]:
                raise RuntimeError("Git revision changed during verification.")
            record["status"] = "passed"
        except Exception as exc:
            record.update(status="cancelled" if cancel.is_set() else "failed", error=str(exc)[:2000])
        finally:
            record.update(ended=time.time(), processes=[])
            self.save(record)

    def command(self, record, spec, cancel):
        # Stage the process before activation, so restart recovery knows its identity.
        directory = self.studio.data / "verifications" / record["id"] / spec["id"]
        directory.mkdir(parents=True)
        request = {"argv": spec["argv"], "cwd": record["root"], "parent": os.getpid(), "controller_data": str(self.studio.data.resolve())}
        (directory / "request.json").write_text(json.dumps(request), encoding="utf-8")
        result = {**spec, "started": time.time(), "exit_code": None, "log": "", "bytes": 0}
        process = subprocess.Popen([sys.executable, "-u", str(Path(__file__).with_name("verification_worker.py")),
                                    str(directory)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        tree = ProcessTree(process.pid, grace=0.5)
        selector = selectors.DefaultSelector()
        try:
            record["processes"] = tree.identities()
            self.save(record)
            (directory / "activated").touch()
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + spec["timeout"]
            chunks = []
            while True:
                tree.refresh()
                if cancel.is_set() or time.monotonic() > deadline or result["bytes"] > 2_000_000:
                    result["error"] = ("Verification was paused." if cancel.is_set() else
                                       "Verification time limit or 2 MB log size limit exceeded.")
                    break
                for event, _ in selector.select(0.1):
                    chunk = os.read(event.fd, 65536)
                    if chunk:
                        result["bytes"] += len(chunk)
                        if result["bytes"] <= 2_100_000:
                            chunks.append(chunk)
                    else:
                        selector.unregister(event.fileobj)
                if process.poll() is not None and not selector.get_map():
                    break
            result["log"] = b"".join(chunks).decode("utf-8", errors="replace")
        finally:
            selector.close()
            clean = tree.finish()
            process.wait(timeout=4)
            process.stdout.close()
            if not clean:
                result["error"] = "Failed to terminate all verification processes."
            result.update(**command_outcome(directory, process.returncode), ended=time.time())
        return result

    def verify(self, m):
        record = self.get(m.get("verification_id"))
        if record["mission"] != m["id"] or record["status"] != "passed":
            raise ValueError("Independent verifications failed.")
        if validate_checks(record["specs"]) != validate_checks(m["verification_checks"]):
            raise ValueError("The task brief for independent verifications has changed.")
        expected = dict(record["sources"])
        if m.get("status") == "accepted" and not m.get("work_project"):
            expected.update(m.get("notes_sync", {}).get("hashes", {}))
        if source_manifest(m["workspace"]) != expected:
            raise ValueError("Source files have changed since the independent verification.")
        modes = dict(record.get("source_modes", {}))
        if m.get("status") == "accepted" and not m.get("work_project"):
            modes.update(m.get("notes_sync", {}).get("modes", {}))
        if source_modes(m["workspace"], expected) != modes:
            raise ValueError("File permissions have changed or were not verified. Run verifications again.")
        if "git_revision" in record and git_revision(m["workspace"]) != record["git_revision"]:
            raise ValueError("Git revision changed since verification. Run verifications again.")
        return record

    def close(self):
        with self.lock:
            threads = list(self.threads.values())
            for cancel in self.cancels.values():
                cancel.set()
        for thread in threads:
            thread.join(timeout=5)
