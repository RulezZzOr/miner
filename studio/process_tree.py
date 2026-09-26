"""Best-effort native process ownership, retaining identities across reparenting.

This is lifecycle management, not containment of deliberately daemonising code.
psutil checks PID + creation time before signalling, avoiding reused-PID kills.
Recovery compares the boot-relative start time only within the recorded boot; a
record from another boot, or one without a boot identity, needs the exact creation time.
During a stop, only the payload receives the graceful signal: the bubblewrap monitor
has no handler and its --die-with-parent teardown would SIGKILL the sandbox at once.
Everything that is still alive after the grace period, including processes left in
the root's process group, is killed.
"""

from __future__ import annotations

import os
import signal
import threading
import time
from pathlib import Path

import psutil

SCAN_INTERVAL = 0.25


def started(process):
    """Boot-relative start time; unlike create_time() it survives wall-clock steps."""
    try:
        return round(float(process._proc.create_time(monotonic=True)), 3)
    except (AttributeError, TypeError, ValueError, OSError, psutil.Error):
        return None


def boot_id():
    """Identity of the running boot ('' when unknown); boot-relative times repeat across boots."""
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def ppid_map():
    reader = getattr(psutil, "_ppid_map", None)
    if reader is not None:
        try:
            return reader()
        except (OSError, psutil.Error):
            pass
    return {p.pid: p.info["ppid"] for p in psutil.process_iter(["ppid"]) if p.info["ppid"] is not None}


class ProcessTree:
    def __init__(self, pid, grace=5, interrupt=signal.SIGINT):
        self.lock = threading.RLock()
        self.cleanup_lock = threading.Lock()
        self.members = set()
        self.interrupted = set()
        self.deadline = None
        self.grace = grace
        self.interrupt = interrupt
        self.group = None
        self.scanned = 0.0
        try:
            root = psutil.Process(pid)
            self.members.add(root)
            # Roots start in their own session; the group outlives an unseen child's parent.
            if os.getpgid(pid) == pid:
                self.group = (pid, root.create_time())
        except (psutil.Error, ValueError, OSError):
            pass
        self.refresh(force=True)

    @staticmethod
    def alive(process):
        try:
            return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
        except psutil.Error:
            return False

    @staticmethod
    def sandbox_monitor(process):
        try:
            return process.name() == "bwrap"
        except psutil.Error:
            return False

    def discover(self, living):
        """Add living descendants using one parent map for the whole tree."""
        children = {}
        for pid, parent in ppid_map().items():
            children.setdefault(parent, []).append(pid)
        queue = list(living)
        while queue:
            parent = queue.pop()
            for pid in children.get(parent.pid, ()):
                try:
                    child = psutil.Process(pid)
                    # A child older than its parent is a reused PID, not a descendant.
                    if child in living or child.create_time() < parent.create_time() or not self.alive(child):
                        continue
                except psutil.Error:
                    continue
                living.add(child)
                queue.append(child)

    def group_members(self):
        """Processes still in the root's group; empty when the group ID was reused."""
        if not self.group:
            return set()
        pgid, created = self.group
        try:
            if psutil.Process(pgid).create_time() != created:
                return set()
        except psutil.Error:
            pass  # A dead leader's group ID cannot be reused while members remain.
        found = set()
        for process in psutil.process_iter():
            try:
                if os.getpgid(process.pid) == pgid and self.alive(process):
                    found.add(process)
            except (OSError, psutil.Error):
                pass
        return found

    def refresh(self, force=False):
        with self.lock:
            living = {p for p in self.members if self.alive(p)}
            now = time.monotonic()
            if living and (force or now - self.scanned >= SCAN_INTERVAL):
                self.scanned = now
                self.discover(living)
            # Dead members never help discovery: their children were reparented.
            self.members = set(living)
            self.interrupted &= living
            if self.deadline is not None:
                for process in living - self.interrupted:
                    if not self.sandbox_monitor(process):
                        self.send(process, self.interrupt)
                    self.interrupted.add(process)
            return living

    def identities(self):
        result = []
        boot = boot_id()
        for process in self.refresh():
            try:
                result.append({"pid": process.pid, "created": process.create_time(), "started": started(process),
                               "boot": boot})
            except psutil.Error:
                pass
        return result

    @classmethod
    def recover(cls, identities):
        tree = cls(-1, grace=0.5)
        boot = boot_id()
        for item in identities:
            try:
                process = psutil.Process(item["pid"])
                same_boot = bool(boot) and item.get("boot") == boot
                current = started(process) if same_boot and item.get("started") is not None else None
                if current is not None:
                    matches = abs(current - item["started"]) < 0.05
                else:
                    matches = process.create_time() == item["created"]
                if matches:
                    tree.members.add(process)
            except (psutil.Error, KeyError, TypeError, ValueError):
                pass
        return tree.finish()

    @staticmethod
    def send(process, sig):
        try:
            process.send_signal(sig)
        except psutil.Error:
            pass

    def stop(self):
        with self.lock:
            # Snapshot descendants BEFORE any parent gets a chance to exit.
            self.refresh(force=True)
            if self.deadline is None:
                self.deadline = time.monotonic() + self.grace
            self.refresh(force=True)

    def finish(self):
        # Watcher, Stop and server shutdown may all join the same cleanup.
        with self.cleanup_lock:
            self.stop()
            while self.refresh() and time.monotonic() < self.deadline:
                time.sleep(0.05)
            deadline = time.monotonic() + 3
            while True:
                with self.lock:
                    # A child spawned after the last scan may have lost its parent already.
                    self.members |= self.group_members()
                    living = self.refresh(force=True)
                if not living or time.monotonic() >= deadline:
                    return not living
                for process in living:
                    self.send(process, signal.SIGKILL)
                time.sleep(0.05)
