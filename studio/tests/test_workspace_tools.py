"""An isolated project remains discoverable below Studio's ignored state folder."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins.tools.glob_search import _local_glob
from plugins.tools.ignore_rules import discover_repo_root
from apodex.agent_tools import localize_path_args


class WorkspaceToolsTests(unittest.TestCase):
    def test_reader_preserves_absolute_path_when_runtime_workspace_differs(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            output = project / ".apodex/runs/pilot/outputs/result.txt"
            with patch.dict(os.environ, {"FRONTIER_AGENT_WORKSPACE_DIR": str(project / "runtime")}):
                self.assertIsNone(localize_path_args("read_file", {"path": str(output)}, str(project)))
            with patch.dict(os.environ, {"FRONTIER_AGENT_WORKSPACE_DIR": str(project)}):
                self.assertEqual(localize_path_args("read_file", {"path": str(output)}, str(project)),
                                 {"path": ".apodex/runs/pilot/outputs/result.txt"})

    def test_host_ignore_file_does_not_hide_authorized_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            host = Path(directory).resolve()
            (host / ".gitignore").write_text(".switch-agent/\n")
            workspace = host / ".switch-agent" / "workspaces" / "pilot"
            workspace.mkdir(parents=True)
            (workspace / "README.md").write_text("Visible")
            (workspace / "node_modules").mkdir()
            (workspace / "node_modules" / "ignored.md").write_text("Hidden dependency")
            with patch.dict(os.environ, {"CODING_WORKSPACE_ROOT": str(workspace)}):
                self.assertEqual(discover_repo_root(workspace), workspace)
                result = _local_glob("**/*.md", str(workspace), 100)
                self.assertIn("README.md", result)
                self.assertNotIn("ignored.md", result)
