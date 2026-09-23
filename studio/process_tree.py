"Trvalé vlastnictví procesu nativním způsobem, zachovává identity při přepojování.\n\nJde o řízení životního cyklu, nikoli o uzavření úmyslně daemonizovaného kódu.\npsutil před signálem zkontroluje PID + čas vytvoření, čímž předejde zabíjení znovupoužitých PID.\n"

from __future__ import annotations

import signal
import threading
import time

import psutil


class ProcessTree:
    def __init__(self, pid, grace=5):
        self.lock = threading.RLock()
        self.cleanup_lock = threading.Lock()
        self.members = set()
        self.interrupted = set()
        self.deadline = None
        self.grace = grace
        try:
            self.members.add(psutil.Process(pid))
        except (psutil.NoSuchProcess, ValueError):
            pass
        self.refresh()

    @staticmethod
    def alive(process):
        try:
            return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False

    def refresh(self):
        with self.lock:
            for parent in list(self.members):
                try:
                    self.members.update(parent.children(recursive=True))
                except psutil.NoSuchProcess:
                    pass
            living = {p for p in self.members if self.alive(p)}
            if self.deadline is not None:
                for process in living - self.interrupted:
                    self.send(process, signal.SIGINT)
                    self.interrupted.add(process)
            return living

    def identities(self):
        result = []
        for process in self.refresh():
            try:
                result.append({"pid": process.pid, "created": process.create_time()})
            except psutil.NoSuchProcess:
                pass
        return result

    @classmethod
    def recover(cls, identities):
        tree = cls(-1, grace=0.5)
        for item in identities:
            try:
                process = psutil.Process(item["pid"])
                if process.create_time() == item["created"]:
                    tree.members.add(process)
            except (psutil.NoSuchProcess, KeyError):
                pass
        return tree.finish()

    @staticmethod
    def send(process, sig):
        try:
            process.send_signal(sig)
        except psutil.NoSuchProcess:
            pass

    def stop(self):
        with self.lock:
            # Snapshot descendants BEFORE any parent gets a chance to exit.
            self.refresh()
            if self.deadline is None:
                self.deadline = time.monotonic() + self.grace
            self.refresh()

    def finish(self):
        # Watcher, Stop and server shutdown may all join the same cleanup.
        with self.cleanup_lock:
            self.stop()
            while self.refresh() and time.monotonic() < self.deadline:
                time.sleep(0.05)
            for process in self.refresh():
                self.send(process, signal.SIGKILL)
            deadline = time.monotonic() + 3
            while self.refresh() and time.monotonic() < deadline:
                # A last descendant may have appeared during escalation.
                for process in self.refresh():
                    self.send(process, signal.SIGKILL)
                time.sleep(0.05)
            return not self.refresh()
