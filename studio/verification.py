"Důkaz příkazu vlastněný řadičem. Modelové reporty nemohou vytvořit kód ukončení.\n\nPříkazy jsou schvalovány přes API vlastníka, spouštěny bez shellu a vázány ke zdrojovému manifestu před i po spuštění. Jde o řízení procesů, nikoli o OS sandbox; nativní pracovník i kontroly stále mají oprávnění aktuálního uživatele.\n"
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
    from .verification_worker import command_outcome
except ImportError:
    from process_tree import ProcessTree
    from verification_worker import command_outcome

IGNORED = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache",
           ".mypy_cache", ".switch-agent", ".apodex", ".DS_Store"}


def source_manifest(root):
    "Otisk zdrojových souborů s pevnými limity; bez procházení symbolických odkazů nebo tichého zkrácení."
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
                raise ValueError(f"Ověření vyžaduje skutečné soubory, ne symlink: {rel}")
            info = path.stat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"Ověření nepřijímá speciální soubor: {rel}")
            if info.st_size > 50_000_000 or len(result) >= 20000:
                raise ValueError("Zdrojové soubory překročily limit ověření (50 MB/soubor, 500 MB, 20 000 souborů).")
            data = read_project_file(root, rel, min(50_000_000, 500_000_000 - total))
            total += len(data)
            result[rel] = hashlib.sha256(data).hexdigest()
    return result


def source_modes(root, manifest):
    "Oprávnění jsou součástí ověřeného stavu zdroje, včetně spustitelnosti."
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
                    raise ValueError("Práva lze ověřit pouze u běžného souboru: " + path)
                result[path] = stat.S_IMODE(mode) & 0o777
            finally:
                os.close(fd)
    return result


def validate_checks(value):
    if isinstance(value, str):
        value = [{"argv": shlex.split(line), "label": line[:200]} for line in value.splitlines() if line.strip()]
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError("Kontroly musí být seznam nejvýše 20 příkazů.")
    result = []
    for index, item in enumerate(value):
        argv = item.get("argv") if isinstance(item, dict) else None
        if (not isinstance(argv, list) or not 1 <= len(argv) <= 100 or
                any(not isinstance(a, str) or not a or len(a) > 4000 or "\0" in a for a in argv)):
            raise ValueError("Příkaz kontroly musí obsahovat neprázdný seznam argumentů argv.")
        timeout = item.get("timeout", 300)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 3600:
            raise ValueError("Čas kontroly musí být 1–3600 sekund.")
        result.append({"id": str(index + 1), "label": str(item.get("label", argv[0]))[:200],
                       "argv": argv, "timeout": timeout})
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
                              error="Studio bylo restartováno; kontrola musí proběhnout znovu.", cleanup=clean)
                self.save(record)

    def save(self, record):
        with self.missions.connect() as db:
            db.execute("INSERT INTO verifications VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                       (record["id"], json.dumps(record, ensure_ascii=False)))

    def get(self, key):
        with self.missions.connect() as db:
            row = db.execute("SELECT data FROM verifications WHERE id=?", (key,)).fetchone()
        if not row:
            raise ValueError("Záznam nezávislé kontroly neexistuje.")
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
                raise ValueError("Chybí příkazy nezávislých kontrol.")
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
            record["source_modes"] = source_modes(record["root"], before)
            self.save(record)
            for spec in record["specs"]:
                if cancel.is_set():
                    raise RuntimeError("Ověření bylo pozastaveno.")
                check = self.command(record, spec, cancel)
                record["checks"].append(check)
                self.save(record)
                if check["exit_code"] != 0 or check.get("error"):
                    raise RuntimeError(check.get("error") or f"Kontrola {spec['label']} skončila kódem {check['exit_code']}.")
            after = source_manifest(record["root"])
            if before != after or source_modes(record["root"], after) != record["source_modes"]:
                raise RuntimeError("Soubory se během kontrol změnily. Ověř znovu výsledný obsah.")
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
        request = {"argv": spec["argv"], "cwd": record["root"], "parent": os.getpid()}
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
                    result["error"] = ("Ověření bylo pozastaveno." if cancel.is_set() else
                                       "Překročen čas kontroly nebo limit logu 2 MB.")
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
                result["error"] = "Nepodařilo se ukončit všechny procesy kontroly."
            result.update(**command_outcome(directory, process.returncode), ended=time.time())
        return result

    def verify(self, m):
        record = self.get(m.get("verification_id"))
        if record["mission"] != m["id"] or record["status"] != "passed":
            raise ValueError("Nezávislé kontroly neprošly.")
        if record["specs"] != m["verification_checks"]:
            raise ValueError("Změnilo se zadání nezávislých kontrol.")
        if source_manifest(m["workspace"]) != record["sources"]:
            raise ValueError("Zdrojové soubory se od nezávislé kontroly změnily.")
        if source_modes(m["workspace"], record["sources"]) != record.get("source_modes"):
            raise ValueError("Práva souborů se změnila nebo nebyla ověřena. Spusť kontroly znovu.")
        return record

    def close(self):
        with self.lock:
            threads = list(self.threads.values())
            for cancel in self.cancels.values():
                cancel.set()
        for thread in threads:
            thread.join(timeout=5)
