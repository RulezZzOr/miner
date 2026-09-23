"""Content-addressed source versions and conflict-checked, recoverable file updates."""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

try:
    from .verification import source_manifest, source_modes
except ImportError:
    from verification import source_manifest, source_modes


def manifest_revision(manifest):
    return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


def file_state(version, path):
    digest = version["files"].get(path)
    return (digest, version.get("modes", {}).get(path, 0o644)) if digest is not None else None


class Versions:
    def __init__(self, missions):
        self.missions = missions
        self.studio = missions.studio
        self.objects = self.studio.data / "objects"
        self.objects.mkdir(exist_ok=True)
        with missions.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS versions (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS file_operations (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            pending = [json.loads(r[0]) for r in db.execute("SELECT data FROM file_operations")]
        self.recovery_errors = []
        for root in {p["root"] for p in pending if p["status"] in {"applying", "conflict"}}:
            try:
                self.recover(Path(root))
            except Exception as exc:
                self.recovery_errors.append(str(exc))

    def save(self, table, item):
        if table not in {"versions", "file_operations"}:
            raise ValueError("Unknown version table")
        with self.missions.connect() as db:
            db.execute(f"INSERT INTO {table} VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                       (item["id"], json.dumps(item, ensure_ascii=False)))

    def get(self, key):
        with self.missions.connect() as db:
            row = db.execute("SELECT data FROM versions WHERE id=?", (key,)).fetchone()
        if not row:
            raise ValueError("File version does not exist.")
        return json.loads(row[0])

    def object_path(self, digest):
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Invalid content identifier.")
        return self.objects / digest

    def read_object(self, digest):
        data = self.object_path(digest).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Saved version is corrupted; recovery has been stopped.")
        return data

    def snapshot(self, root, *, label, expected=None, expected_modes=None):
        try:
            from .server import read_project_file
        except ImportError:
            from server import read_project_file
        root = Path(root)
        manifest = source_manifest(root)
        if expected is not None and manifest != expected:
            raise ValueError("Files changed before version creation.")
        modes = source_modes(root, manifest)
        if expected_modes is not None and modes != expected_modes:
            raise ValueError("File permissions changed before version creation.")
        for relative, digest in manifest.items():
            data = read_project_file(root, relative, 50_000_000)
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError(f"File changed while saving the version: {relative}")
            target = self.object_path(digest)
            if not target.exists():
                temporary = target.with_name(target.name + "." + uuid.uuid4().hex)
                try:
                    with temporary.open("xb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
            else:
                self.read_object(digest)
        if source_manifest(root) != manifest or source_modes(root, manifest) != modes:
            raise ValueError("Files changed during version saving.")
        fd = os.open(self.objects, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        version = {"id": uuid.uuid4().hex[:16], "created": time.time(), "root": str(root),
                   "label": label, "files": manifest, "modes": modes}
        self.save("versions", version)
        return version

    def write_file(self, root, relative, digest, mode=0o644):
        try:
            from .server import file_parent
        except ImportError:
            from server import file_parent
        data = self.read_object(digest) if digest is not None else None
        with file_parent(Path(root), relative, create=data is not None) as (parent, name):
            if data is None:
                os.unlink(name, dir_fd=parent)
            else:
                temporary = ".studio-" + uuid.uuid4().hex
                try:
                    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 mode, dir_fd=parent)
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fchmod(stream.fileno(), mode)
                        os.fsync(stream.fileno())
                    os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
                finally:
                    try:
                        os.unlink(temporary, dir_fd=parent)
                    except FileNotFoundError:
                        pass
            os.fsync(parent)

    def materialize(self, version, root):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        if any(root.iterdir()):
            raise ValueError("Working folder for isolated execution is no longer empty.")
        for relative, digest in version["files"].items():
            self.write_file(root, relative, digest, version["modes"].get(relative, 0o644))

    def preview(self, root, target, base=None):
        current = source_manifest(root)
        modes = source_modes(root, current)
        state = {"files": current, "modes": modes}
        desired = target["files"]
        previous = base["files"] if base is not None else current
        base = base if base is not None else state
        changed = sorted(p for p in previous.keys() | desired.keys() if file_state(base, p) != file_state(target, p))
        conflicts = [p for p in changed if file_state(state, p) not in {file_state(base, p), file_state(target, p)}]
        # Refuse shape changes before writing anything, including untracked or
        # excluded directories. This avoids an unrecoverable partial conversion.
        shape_conflicts = []
        for p in changed:
            if p not in desired:
                continue
            destination = Path(root) / p
            if destination.is_dir() or any(parent.is_file() for parent in destination.parents if parent != Path(root)):
                shape_conflicts.append(p)
        return {"revision": manifest_revision(state), "changed": changed, "conflicts": conflicts,
                "shape_conflicts": shape_conflicts, "target": target["id"], "current": current, "modes": modes}

    def apply(self, root, target, *, base=None, revision=None, commit=None):
        try:
            from .server import Problem, read_project_file
        except ImportError:
            from server import Problem, read_project_file
        self.recover(root)
        preview = self.preview(root, target, base)
        if preview["conflicts"]:
            raise ValueError("Conflict with newer changes: " + ", ".join(preview["conflicts"][:20]))
        if preview["shape_conflicts"]:
            raise ValueError("File/folder swap requires manual move before acceptance: " + ", ".join(preview["shape_conflicts"][:20]))
        if revision is not None and revision != preview["revision"]:
            raise ValueError("Files have changed since the recovery preview. Load a new preview.")
        # Keep the complete pre-operation version, including unrelated owner edits.
        before = self.snapshot(root, label="Before acceptance / recovery", expected=preview["current"], expected_modes=preview["modes"])
        for path in preview["changed"]:
            if path in target["files"]:
                self.read_object(target["files"][path])
        operation = {"id": uuid.uuid4().hex[:16], "root": str(root), "before": before["id"],
                     "target": target["id"], "changed": preview["changed"], "status": "applying",
                     "created": time.time()}
        self.save("file_operations", operation)
        try:
            for path in preview["changed"]:
                try:
                    current = (hashlib.sha256(read_project_file(root, path, 50_000_000)).hexdigest(),
                               source_modes(root, [path])[path])
                except Problem as exc:
                    if exc.status != 404:
                        raise
                    current = None
                if current not in {file_state(before, path), file_state(target, path)}:
                    raise ValueError(f"File changed during acceptance: {path}")
                desired = target["files"].get(path)
                if current != file_state(target, path):
                    self.write_file(root, path, desired, target["modes"].get(path, 0o644))
            operation.update(status="complete", ended=time.time())
            # Persist linked owner metadata in the same commit as completion.
            # A crash before that commit leaves an applying journal, which
            # restores the previous files and keeps the previous owner state.
            with self.missions.connect() as db:
                db.execute("UPDATE file_operations SET data=? WHERE id=?",
                           (json.dumps(operation, ensure_ascii=False), operation["id"]))
                if commit:
                    commit(db, operation)
        except Exception:
            self.recover(root)
            raise
        return operation

    def recover(self, root):
        with self.missions.connect() as db:
            operations = [json.loads(r[0]) for r in db.execute("SELECT data FROM file_operations")]
        for operation in operations:
            if operation["root"] != str(root) or operation["status"] not in {"applying", "conflict"}:
                continue
            before = self.get(operation["before"])
            target = self.get(operation["target"])
            current = source_manifest(root)
            state = {"files": current, "modes": source_modes(root, current)}
            conflicts = [p for p in operation["changed"]
                         if file_state(state, p) not in {file_state(before, p), file_state(target, p)}]
            if conflicts:
                operation.update(status="conflict", conflicts=conflicts)
                self.save("file_operations", operation)
                raise ValueError("Interrupted recovery contains newer changes; automatic overwrite rejected: " + ", ".join(conflicts[:20]))
            for path in reversed(operation["changed"]):
                desired = before["files"].get(path)
                if file_state(state, path) != file_state(before, path):
                    self.write_file(root, path, desired, before["modes"].get(path, 0o644))
            operation.update(status="rolled_back", ended=time.time())
            self.save("file_operations", operation)
