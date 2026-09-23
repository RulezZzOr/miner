"""A stuck native shell leaves the bounded worker time to report its failure."""
import asyncio
import importlib
import os
import shlex
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from frontier_agent.core.execution_context import reset_current_tool_budget, set_current_tool_budget
from plugins.tools._sandbox import CurrentSandbox

from studio.runner import limit_mission_tools


class WorkerLimitTests(unittest.TestCase):
    def test_profile_ceiling_and_legacy_requests(self):
        settings = {"agent": {"tool_timeout_s": 900}}
        self.assertIsNone(limit_mission_tools(settings, None))
        self.assertEqual(settings["agent"]["tool_timeout_s"], 900)
        self.assertEqual(limit_mission_tools(settings, 600), 200)
        settings["agent"]["tool_timeout_s"] = 12
        self.assertEqual(limit_mission_tools(settings, 600), 12)

    def test_real_background_child_times_out_and_next_command_still_works(self):
        bash = importlib.import_module("plugins.tools.bash")
        with tempfile.TemporaryDirectory() as directory:
            sandbox = CurrentSandbox(directory)
            settings = {"agent": {"tool_timeout_s": 900}}
            budget = limit_mission_tools(settings, 3)

            async def exercise():
                token = set_current_tool_budget(budget)
                try:
                    started = time.monotonic()
                    out = await bash.bash.ainvoke({"command": shlex.quote(sys.executable)
                        + " -c 'import time; time.sleep(30)' &"})
                    self.assertIn("timed out after 1 seconds", out)
                    self.assertIn("Save the observed failure", out)
                    self.assertNotIn("launch it with nohup", out)
                    self.assertLess(time.monotonic() - started, 10)
                    out = await bash.bash.ainvoke({"command": "echo recovered"})
                    self.assertIn("recovered", out)
                finally:
                    reset_current_tool_budget(token)

            with patch.dict(os.environ, {"SWITCH_STUDIO_PHASE_INSTRUCTIONS": "mission", "BASH_TIMEOUT": "900"}), \
                 patch.object(bash, "aget_sandbox", AsyncMock(return_value=sandbox)), \
                 patch.object(bash, "ensure_guard_file", AsyncMock()):
                asyncio.run(exercise())
