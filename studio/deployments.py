"""Local release processes, health probes, incident evidence and verified replacement.

This backend runs on this machine, binds to loopback by configuration, and is not
public hosting. A candidate must become healthy before the previous process stops.
Each release has its own URL. Remote deployment is deliberately a separate adapter.
"""
from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import psutil

try:
    from .process_tree import ProcessTree
    from .verification_worker import command_outcome
except ImportError:
    from process_tree import ProcessTree
    from verification_worker import command_outcome


def settings(body):
    argv = body.get("argv")
    if isinstance(argv, str):
        argv = shlex.split(argv)
    if (not isinstance(argv, list) or not 1 <= len(argv) <= 100 or
            any(not isinstance(a, str) or not a or len(a) > 4000 or "\0" in a for a in argv)):
        raise ValueError("Zadej příkaz služby jako argumenty nebo jeden příkazový řádek.")
    if not any("{port}" in a for a in argv) or not any("{host}" in a for a in argv):
        raise ValueError("Příkaz služby musí použít {host} a {port}; Studio dosadí 127.0.0.1 a volný port.")
    path = body.get("health_path", "/")
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//") or any(c in path for c in "\r\n\\"):
        raise ValueError("Health cesta musí být místní cesta začínající /.")
    return {"argv": argv, "health_path": path[:1000], "expected_status": 200,
            "expected_text": str(body.get("expected_text", ""))[:1000],
            "auto_repair": body.get("auto_repair") is True,
            "auto_release": body.get("auto_release") is True,
            "restart_on_start": body.get("restart_on_start") is True}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Deployments:
    def __init__(self, studio):
        self.studio = studio
        self.missions = studio.missions
        self.lock = threading.RLock()
        self.processes, self.logs, self.readers = {}, {}, {}
        self.pending_restart = {}
        with self.missions.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS deployments (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        records = self.list()
        for record in records:
            if record["status"] in {"starting", "healthy", "unhealthy", "stopping"}:
                if not ProcessTree.recover(record.get("processes", [])):
                    raise RuntimeError("Předchozí proces nasazení se nepodařilo ukončit.")
                record.update(status="interrupted", processes=[], error="Studio bylo restartováno.")
                self.save(record)
            if record.get("desired") == "running":
                old = self.pending_restart.get(record["product"])
                rank = (bool(record.get("healthy_at")), record["created"])
                if old is None or rank > (bool(old.get("healthy_at")), old["created"]):
                    self.pending_restart[record["product"]] = record

    def save(self, record):
        with self.missions.connect() as db:
            db.execute("INSERT INTO deployments VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                       (record["id"], json.dumps(record, ensure_ascii=False)))

    def get(self, key):
        with self.missions.connect() as db:
            row = db.execute("SELECT data FROM deployments WHERE id=?", (key,)).fetchone()
        if not row:
            raise ValueError("Nasazení neexistuje.")
        return json.loads(row[0])

    def list(self, product=None):
        with self.missions.connect() as db:
            records = [json.loads(r[0]) for r in db.execute("SELECT data FROM deployments")]
        return sorted([r for r in records if product is None or r["product"] == product],
                      key=lambda r: r["created"], reverse=True)

    def start(self, product, release, *, recovery_count=0):
        spec = product.get("deployment_settings")
        if not spec:
            raise ValueError("Nejdřív nastav místní službu a její kontrolu dostupnosti.")
        if not release.get("version_id") or not release.get("verification_id"):
            raise ValueError("Nasazení vyžaduje uložený obsah verze a úspěšné nezávislé kontroly.")
        version = self.missions.versions.get(release["version_id"])
        evidence = self.missions.verifications.get(release["verification_id"])
        if (evidence["status"] != "passed" or evidence.get("sources") != version["files"]
                or evidence.get("source_modes") != version["modes"]):
            raise ValueError("Verze neodpovídá skutečně ověřeným zdrojovým souborům.")
        with self.lock:
            if any(r["status"] == "starting" for r in self.list(product["id"])):
                raise ValueError("Už se ověřuje nové nasazení tohoto produktu.")
            if len(self.processes) >= 10:
                raise ValueError("Místní backend má limit 10 současných procesů služeb.")
            key = uuid.uuid4().hex[:16]
            directory = self.studio.data / "deployments" / key
            root = directory / "release"
            self.missions.versions.materialize(version, root)
            data_dir = self.studio.data / "deployment-data" / product["id"]
            data_dir.mkdir(parents=True, exist_ok=True)
            with socket.socket() as allocation:
                allocation.bind(("127.0.0.1", 0))
                port = allocation.getsockname()[1]
            replacements = {"{host}": "127.0.0.1", "{port}": str(port), "{data_dir}": str(data_dir)}
            argv = []
            for argument in spec["argv"]:
                for token, value in replacements.items():
                    argument = argument.replace(token, value)
                argv.append(argument)
            record = {"id": key, "product": product["id"], "number": release["number"],
                      "version": version["id"], "verification": evidence["id"], "spec": spec,
                      "status": "starting", "desired": "running", "created": time.time(),
                      "url": f"http://127.0.0.1:{port}", "processes": [], "health": [], "failures": 0,
                      "consecutive_passes": 0, "next_check": 0, "log": "", "recovery_count": recovery_count}
            self.save(record)
            request = {"argv": argv, "cwd": str(root), "parent": os.getpid(),
                       "environment": {"SWITCH_DATA_DIR": str(data_dir), "SWITCH_RELEASE_ID": version["id"]}}
            (directory / "request.json").write_text(json.dumps(request), encoding="utf-8")
            try:
                process = subprocess.Popen([sys.executable, "-u", str(Path(__file__).with_name("verification_worker.py")), str(directory)],
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
                tree = ProcessTree(process.pid, grace=0.5)
                self.processes[key] = (process, tree)
                record["processes"] = tree.identities()
                self.save(record)
                self.logs[key] = b""
                reader = threading.Thread(target=self.read_log, args=(key, process), daemon=True)
                self.readers[key] = reader
                reader.start()
                (directory / "activated").touch()
            except Exception as exc:
                if key in self.processes:
                    self.stop(key, reason="Start selhal.")
                record.update(status="failed", desired="stopped", error=str(exc), processes=[], incident=uuid.uuid4().hex[:16])
                self.save(record)
                raise
            return record

    def read_log(self, key, process):
        try:
            while chunk := os.read(process.stdout.fileno(), 65536):
                with self.lock:
                    self.logs[key] = (self.logs.get(key, b"") + chunk)[-32768:]
        except (OSError, ValueError):
            pass

    def stop(self, key, *, reason="Zastaveno vlastníkem.", keep_desired=False):
        with self.lock:
            record = self.get(key)
            pair = self.processes.pop(key, None)
            record.update(status="stopping", desired="running" if keep_desired else "stopped", error=reason)
            self.save(record)
        clean = True
        if pair:
            process, tree = pair
            clean = tree.finish()
            process.wait(timeout=4)
            reader = self.readers.pop(key, None)
            if reader:
                reader.join(timeout=1)
            process.stdout.close()
        with self.lock:
            record.update(status="interrupted" if keep_desired else "stopped",
                          processes=tree.identities() if pair and not clean else [], ended=time.time(),
                          log=self.logs.pop(key, record.get("log", "").encode()).decode("utf-8", errors="replace"))
            if not clean:
                record.update(status="unhealthy", error="Nelze ukončit všechny procesy služby.")
                self.processes[key] = pair
            self.save(record)
        return record

    def probe(self, record):
        started = time.monotonic()
        check = {"at": time.time(), "passed": False}
        try:
            # The free-port allocation and process bind cannot be atomic for
            # arbitrary commands. Never accept another process's HTTP answer.
            pair = self.processes.get(record["id"])
            port = int(record["url"].rsplit(":", 1)[1])
            owned = pair and any(connection.status == psutil.CONN_LISTEN and connection.laddr.port == port
                                for process in pair[1].refresh()
                                for connection in process.net_connections(kind="inet"))
            if not owned:
                raise RuntimeError("Na portu zatím neposlouchá vlastní proces nasazení.")
            opener = urllib.request.build_opener(NoRedirect)
            with opener.open(record["url"] + record["spec"]["health_path"], timeout=1) as response:
                body = response.read(65536).decode("utf-8", errors="replace")
                check.update(status=response.status, passed=response.status == record["spec"]["expected_status"]
                             and record["spec"]["expected_text"] in body)
        except Exception as exc:
            check["error"] = str(exc)[:500]
        check["seconds"] = time.monotonic() - started
        return check

    def tick(self):
        pending, self.pending_restart = self.pending_restart, {}
        for product_id, previous in pending.items():
            previous = self.get(previous["id"])
            if previous.get("desired") != "running":
                continue
            count_before = previous.get("recovery_count", 0)
            try:
                product = self.studio.products.get(product_id)
                if (product.get("deployment_settings") or {}).get("restart_on_start") and previous.get("recovery_count", 0) < 3:
                    attempt = previous.get("recovery_count", 0) + 1
                    previous["recovery_count"] = attempt
                    self.save(previous)
                    release = next(r for r in product["releases"] if r["number"] == previous["number"])
                    self.start(product, release, recovery_count=attempt)
                    for old in self.list(product_id):
                        if old["created"] <= previous["created"] or old["status"] == "interrupted":
                            old["desired"] = "stopped"
                            self.save(old)
            except Exception as exc:
                previous["recovery_count"] = max(previous.get("recovery_count", 0), count_before + 1)
                previous.update(error="Obnova služby selhala: " + str(exc)[:1000])
                self.save(previous)
                if previous.get("recovery_count", 0) < 3:
                    self.pending_restart[product_id] = previous
        for record in self.list():
            key = record["id"]
            with self.lock:
                pair = self.processes.get(key)
            if pair and record["status"] in {"starting", "healthy"} and time.time() >= record["next_check"]:
                process, tree = pair
                check = self.probe(record) if process.poll() is None else {
                    "at": time.time(), "passed": False,
                    **command_outcome(self.studio.data / "deployments" / key, process.returncode),
                    "error": "Proces služby skončil."}
                if check.get("signal"):
                    check["error"] += " Signál: " + check["signal"] + "."
                with self.lock:
                    if key not in self.processes:
                        continue
                    record["health"] = (record["health"] + [check])[-50:]
                    record["processes"] = tree.identities()
                    record["log"] = self.logs.get(key, b"").decode("utf-8", errors="replace")
                    record["next_check"] = time.time() + 2
                    record["failures"] = 0 if check["passed"] else record["failures"] + 1
                    record["consecutive_passes"] = record["consecutive_passes"] + 1 if check["passed"] else 0
                    activate = record["status"] == "starting" and record["consecutive_passes"] >= 2
                    failed = (process.poll() is not None or
                              record["status"] == "starting" and time.time() - record["created"] >= 30 or
                              record["status"] == "healthy" and record["failures"] >= 3)
                    if activate:
                        record.update(status="healthy", healthy_at=time.time())
                    elif failed:
                        record.update(status="unhealthy", incident=record.get("incident") or uuid.uuid4().hex[:16],
                                      error=check.get("error", "Kontrola dostupnosti neprošla."))
                    if record["status"] == "healthy" and check["passed"] and time.time() - record.get("healthy_at", time.time()) >= 60:
                        record["recovery_count"] = 0  # a minute of stable service ends the recovery series
                    self.save(record)
                if activate:
                    for previous in self.list(record["product"]):
                        if previous["id"] != key and previous["id"] in self.processes:
                            self.stop(previous["id"], reason="Nahrazeno ověřenou novou verzí.")
                elif failed:
                    stopped = self.stop(key, reason=record["error"])
                    stopped.update(status="unhealthy", incident=record["incident"])
                    self.save(stopped)
                    record = stopped
            if record.get("incident") and not record.get("incident_item"):
                item = self.studio.products.deployment_incident(record)
                if item:
                    record["incident_item"] = item
                    self.save(record)

    def close(self):
        for key in list(self.processes):
            self.stop(key, reason="Studio se ukončuje.", keep_desired=True)
