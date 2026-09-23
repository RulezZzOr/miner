"""Version content, merge conflicts and recovery after an interrupted multi-file write."""
import json
import unittest
from unittest.mock import patch

from studio.tests import test_missions as helpers
from studio.versions import Versions


class VersionTests(unittest.TestCase):
    setUp = helpers.MissionTests.setUp
    launch = helpers.MissionTests.launch
    stop = helpers.MissionTests.stop

    def snapshot(self, label="test"):
        return self.controller.versions.snapshot(self.project, label=label)

    def test_versions_restore_content_not_just_checksums(self):
        (self.project / "a.txt").write_text("one")
        (self.project / ".env").write_text("secret")
        first = self.snapshot()
        self.assertNotIn(".env", first["files"])
        (self.project / "a.txt").write_text("two")
        (self.project / "new.txt").write_text("new")
        versions = self.controller.versions
        preview = versions.preview(self.project, first)
        operation = versions.apply(self.project, first, revision=preview["revision"])
        self.assertEqual((self.project / "a.txt").read_text(), "one")
        self.assertFalse((self.project / "new.txt").exists())
        self.assertEqual((self.project / ".env").read_text(), "secret")
        versions.apply(self.project, versions.get(operation["before"]))
        self.assertEqual((self.project / "a.txt").read_text(), "two")
        self.assertEqual((self.project / "new.txt").read_text(), "new")

    def test_merge_preserves_unrelated_changes_and_rejects_conflicts(self):
        (self.project / "a.txt").write_text("old")
        base = self.snapshot()
        work = self.root / "work"
        versions = self.controller.versions
        versions.materialize(base, work)
        (work / "a.txt").write_text("agent")
        target = versions.snapshot(work, label="result")
        (self.project / "mine.txt").write_text("owner")
        (self.project / "a.txt").write_text("owner edit")
        with self.assertRaisesRegex(ValueError, "Konflikt"):
            versions.apply(self.project, target, base=base)
        (self.project / "a.txt").write_text("old")
        versions.apply(self.project, target, base=base)
        self.assertEqual((self.project / "a.txt").read_text(), "agent")
        self.assertEqual((self.project / "mine.txt").read_text(), "owner")

    def test_failed_write_rolls_back_and_restart_recovers_uncommitted_operation(self):
        for name in ("a.txt", "b.txt"):
            (self.project / name).write_text("old")
            (self.project / name).chmod(0o640)
        base = self.snapshot()
        for name in ("a.txt", "b.txt"):
            (self.project / name).write_text("new")
            (self.project / name).chmod(0o660)
        target = self.snapshot()
        versions = self.controller.versions
        versions.apply(self.project, base)
        original = versions.write_file
        calls = []
        def faulty(*args, **kwargs):
            calls.append(args)
            if len(calls) == 2:
                raise OSError("simulated disk error")
            return original(*args, **kwargs)
        with patch.object(versions, "write_file", side_effect=faulty):
            with self.assertRaisesRegex(OSError, "simulated"):
                versions.apply(self.project, target)
        self.assertEqual((self.project / "a.txt").read_text(), "old")
        self.assertEqual((self.project / "b.txt").read_text(), "old")
        versions.save("file_operations", {"id": "crash", "root": str(self.project),
            "before": base["id"], "target": target["id"], "changed": ["a.txt", "b.txt"], "status": "applying"})
        versions.write_file(self.project, "a.txt", target["files"]["a.txt"], target["modes"]["a.txt"])
        restarted = Versions(self.controller)
        self.assertEqual(restarted.recovery_errors, [])
        self.assertEqual((self.project / "a.txt").read_text(), "old")
        self.assertEqual((self.project / "a.txt").stat().st_mode & 0o777, 0o640)
        with self.controller.connect() as db:
            record = json.loads(db.execute("SELECT data FROM file_operations WHERE id='crash'").fetchone()[0])
        self.assertEqual(record["status"], "rolled_back")

    def test_stale_restore_preview_and_corrupted_object_do_not_overwrite(self):
        (self.project / "a.txt").write_text("one")
        first = self.snapshot()
        versions = self.controller.versions
        preview = versions.preview(self.project, first)
        (self.project / "a.txt").write_text("two")
        with self.assertRaisesRegex(ValueError, "náhledu"):
            versions.apply(self.project, first, revision=preview["revision"])
        versions.object_path(first["files"]["a.txt"]).write_text("corrupted")
        with self.assertRaisesRegex(ValueError, "poškozená"):
            versions.apply(self.project, first)
        self.assertEqual((self.project / "a.txt").read_text(), "two")

    def test_permission_only_changes_restore_and_conflict(self):
        path = self.project / "run.sh"
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o644)
        base = self.snapshot()
        path.chmod(0o755)
        target = self.snapshot()
        versions = self.controller.versions
        self.assertEqual(versions.preview(self.project, base)["changed"], ["run.sh"])
        versions.apply(self.project, base)
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)
        preview = versions.preview(self.project, target)
        path.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "náhledu"):
            versions.apply(self.project, target, revision=preview["revision"])
        with self.assertRaisesRegex(ValueError, "Konflikt"):
            versions.apply(self.project, target, base=base)
        path.chmod(0o644)
        versions.apply(self.project, target, base=base)
        self.assertEqual(path.stat().st_mode & 0o777, 0o755)

    def test_file_directory_conversion_is_rejected_before_any_write(self):
        path = self.project / "tree"
        path.write_text("file")
        first = self.snapshot()
        path.unlink()
        path.mkdir()
        (path / "child").write_text("child")
        second = self.snapshot()
        with self.assertRaisesRegex(ValueError, "Záměna souboru"):
            self.controller.versions.apply(self.project, first)
        self.assertEqual((path / "child").read_text(), "child")
        (path / "child").unlink()
        path.rmdir()
        path.write_text("file")
        with self.assertRaisesRegex(ValueError, "Záměna souboru"):
            self.controller.versions.apply(self.project, second)
        self.assertEqual(path.read_text(), "file")
