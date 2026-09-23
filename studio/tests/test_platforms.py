"""Distribution boundaries: preserve configuration and exclude private runtime state."""
from __future__ import annotations

import hashlib
import json
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.build_release import FILES, build, source_files
from scripts.start_studio import configure


class PlatformTests(unittest.TestCase):
    def test_setup_keeps_existing_config_including_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "agent.example.toml").write_bytes(b"example")
            self.assertTrue(configure(root))
            (root / "agent.toml").write_bytes(b"personal config")
            self.assertFalse(configure(root))
            self.assertEqual((root / "agent.toml").read_bytes(), b"personal config")
            (root / "agent.toml").unlink()
            (root / "agent.toml").symlink_to(root / "missing-secret")
            self.assertFalse(configure(root))
            self.assertFalse((root / "missing-secret").exists())

    def test_archives_exclude_private_state_and_verify_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in (*FILES, "Switch Studio.command", "Setup Studio.command", "Switch Studio Windows.cmd",
                         "studio/VERSION", "studio/server.py", "studio/static/app.js"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("source\n")
            (root / "studio/VERSION").write_text("0.1.0-test")
            private = ("agent.toml", ".env", ".env.production", ".switch-agent/token.json",
                       "frontier/.venv/private.py", "studio/__pycache__/private.py",
                       "studio/.env.json", "frontier/benchmarks/datasets/private.json")
            for name in private:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("PRIVATE_SENTINEL")
            archives = build(root, root / "dist")
            self.assertEqual(len(archives), 3)
            for archive in archives:
                if archive.suffix == ".zip":
                    with zipfile.ZipFile(archive) as source:
                        payload = {name.split("/", 1)[1]: source.read(name) for name in source.namelist()}
                        launch = source.getinfo("switch-studio-0.1.0-test/switch-studio")
                        self.assertTrue((launch.external_attr >> 16) & 0o111)
                else:
                    with tarfile.open(archive) as source:
                        payload = {p.name.split("/", 1)[1]: source.extractfile(p).read() for p in source.getmembers()}
                for name in private:
                    self.assertNotIn(name, payload)
                self.assertFalse(any(b"PRIVATE_SENTINEL" in data for data in payload.values()))
                manifest = json.loads(payload.pop("release-manifest.json"))
                self.assertEqual(manifest["files"], {
                    name: hashlib.sha256(data).hexdigest() for name, data in payload.items()
                })
            # Source-tree symlinks must never smuggle external data into a release.
            (root / "studio/leak.py").symlink_to(root / "agent.toml")
            with self.assertRaisesRegex(ValueError, "symlink"):
                source_files(root)
