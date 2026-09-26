"""Workspace policy and sandbox mounts of the Linux bubblewrap isolation layer."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio import isolation
from studio.isolation import isolated_command
from studio.tests.sandbox_support import requires_sandbox


def linux_bwrap():
    """Build commands as on Linux without running them (pure policy checks)."""
    return patch.multiple('studio.isolation', sys=type('S', (), {'platform': 'linux', 'executable': sys.executable,
                                                                  'prefix': sys.prefix, 'base_prefix': sys.base_prefix}),
                          shutil=type('W', (), {'which': staticmethod(lambda name: '/usr/bin/bwrap')}))


class WorkspacePolicyTests(unittest.TestCase):
    """The default install keeps controller state inside the application tree."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Path(self.temp.name).resolve() / 'app'
        self.data = self.app / '.switch-agent' / 'studio'
        for path in ('studio', 'frontier', '.switch-agent/studio/workspaces/abc', '.switch-agent/studio/runs/abc',
                     '.switch-agent/studio/deployments/key/release', '.switch-agent/studio/objects'):
            (self.app / path).mkdir(parents=True)
        root = patch.object(isolation, 'ROOT', self.app)
        root.start()
        self.addCleanup(root.stop)
        platform = linux_bwrap()
        platform.start()
        self.addCleanup(platform.stop)

    def command(self, workspace, **kwargs):
        return isolated_command(['true'], workspace, **kwargs)

    def test_managed_sandboxes_under_the_application_tree_are_accepted(self):
        for workspace in (self.data / 'workspaces' / 'abc', self.data / 'deployments' / 'key' / 'release'):
            with self.subTest(workspace=workspace):
                cmd = self.command(workspace, managed_root=self.data, protected=[self.data])
                self.assertEqual(cmd[cmd.index('--chdir') + 1], str(workspace))

    def test_application_tree_home_root_and_controller_state_are_rejected(self):
        rejected = [self.app, self.app / 'studio', self.app / 'frontier', self.data, self.data.parent,
                    self.data / 'runs' / 'abc', self.data / 'workspaces', self.data / 'objects', Path('/'), Path.home()]
        for workspace in rejected:
            with self.subTest(workspace=workspace), self.assertRaises(ValueError):
                self.command(workspace, managed_root=self.data, protected=[self.data])

    def test_managed_layout_is_rejected_without_an_explicit_managed_root(self):
        with self.assertRaises(ValueError):
            self.command(self.data / 'workspaces' / 'abc')

    def test_workspace_inside_external_controller_state_must_be_managed(self):
        with tempfile.TemporaryDirectory() as other:
            state = Path(other).resolve() / 'state'
            (state / 'runs' / 'x').mkdir(parents=True)
            (state / 'workspaces' / 'x').mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, 'inside controller state'):
                self.command(state / 'runs' / 'x', managed_root=state, protected=[state])
            self.command(state / 'workspaces' / 'x', managed_root=state, protected=[state])

    def test_controller_state_is_masked_before_the_managed_workspace_is_mounted(self):
        workspace = self.data / 'workspaces' / 'abc'
        run = self.data / 'runs' / 'abc'
        cmd = self.command(workspace, managed_root=self.data, protected=[self.data], writable=[run])
        pairs = list(zip(cmd, cmd[1:]))
        mask = pairs.index(('--tmpfs', str(self.data)))
        self.assertLess(mask, pairs.index(('--bind', str(workspace))))
        self.assertLess(mask, pairs.index(('--bind', str(run))))

    def test_controller_state_cannot_be_mounted_as_a_capability(self):
        workspace = self.data / 'workspaces' / 'abc'
        for capability in (self.data, self.data.parent):
            with self.subTest(capability=capability), self.assertRaisesRegex(ValueError, 'mounted'):
                self.command(workspace, managed_root=self.data, writable=[capability])

    def test_worker_path_keeps_the_runtime_first_and_includes_system_directories(self):
        with tempfile.TemporaryDirectory() as project:
            cmd = self.command(project)
        path = cmd[cmd.index('PATH') + 1].split(':')
        self.assertEqual(path[0], str(Path(sys.executable).parent))
        for entry in ('/usr/bin', '/bin', '/usr/sbin', '/sbin'):
            self.assertIn(entry, path)

    def test_system_mounts_include_alternatives_loader_cache_and_time_zone(self):
        for path in ('/etc/alternatives', '/etc/ld.so.cache', '/etc/localtime', '/sbin'):
            self.assertIn(path, isolation.SYSTEM_MOUNTS)


@requires_sandbox
class SandboxTests(unittest.TestCase):
    def run_isolated(self, code, workspace, **kwargs):
        return subprocess.run(isolated_command([sys.executable, '-c', code], workspace, **kwargs),
                              capture_output=True, text=True, timeout=30)

    def test_production_layout_hides_access_key_and_sibling_runs(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d).resolve() / 'state'
            workspace = data / 'workspaces' / 'mission'
            run, sibling = data / 'runs' / 'current', data / 'runs' / 'other'
            for path in (workspace, run, sibling):
                path.mkdir(parents=True)
            (data / 'access-key').write_text('synthetic-owner-key')
            (sibling / 'decision-x.json').write_text('{}')
            code = ('import json; from pathlib import Path; d=Path(' + repr(str(data)) + ');'
                    'print(json.dumps({"key": (d/"access-key").exists(), "sibling": (d/"runs"/"other").exists(),'
                    '"entries": sorted(p.name for p in d.iterdir())}));'
                    '(d/"runs"/"current"/"result").write_text("ok"); Path("product.txt").write_text("ok")')
            p = self.run_isolated(code, workspace, managed_root=data, protected=[data], writable=[run])
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(json.loads(p.stdout), {'key': False, 'sibling': False, 'entries': ['runs', 'workspaces']})
            self.assertEqual((run / 'result').read_text(), 'ok')
            self.assertEqual((workspace / 'product.txt').read_text(), 'ok')
            self.assertEqual((data / 'access-key').read_text(), 'synthetic-owner-key')

    def test_managed_workspace_under_application_tree_runs(self):
        with tempfile.TemporaryDirectory(dir=isolation.ROOT, prefix='.isolation-test-') as d:
            data = Path(d).resolve() / 'studio'
            workspace = data / 'workspaces' / 'mission'
            workspace.mkdir(parents=True)
            p = self.run_isolated('from pathlib import Path; Path("ok.txt").write_text("ok")', workspace,
                                  managed_root=data, protected=[data])
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual((workspace / 'ok.txt').read_text(), 'ok')

    def test_standard_commands_resolve_through_alternatives(self):
        with tempfile.TemporaryDirectory() as project:
            code = ('import subprocess, sys;'
                    'awk = subprocess.run(["awk", "BEGIN{print 6*7}"], capture_output=True, text=True);'
                    'which = subprocess.run(["which", "python3"], capture_output=True, text=True);'
                    'print(awk.stdout.strip(), which.returncode, which.stdout.strip(), sys.prefix)')
            p = self.run_isolated(code, project)
            self.assertEqual(p.returncode, 0, p.stderr)
            awk, code, python, prefix = p.stdout.split()
            self.assertEqual((awk, code), ('42', '0'))
            self.assertEqual(Path(python).parent, Path(sys.executable).parent)


if __name__ == '__main__':
    unittest.main()
