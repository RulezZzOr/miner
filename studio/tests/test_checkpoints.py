"""Failure injection for the durable worker/session boundary."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from frontier_agent.core.loop_types import LoopConfig
from frontier_agent.core.runtime.checkpoint import CheckpointError, write_checkpoint
from frontier_agent.core.runtime.loop.agent_loop import _handle_turn_end


class CheckpointTests(unittest.TestCase):
    def test_session_exposes_persistence_failure_to_renderer(self):
        from apodex.session import TerminalSession
        session = MagicMock()
        session.tui_state = {}
        with patch("apodex.session._session_state_path", return_value="/unused/session.json"), \
             patch("apodex.session.write_checkpoint", side_effect=CheckpointError("disk full")):
            with self.assertRaisesRegex(CheckpointError, "disk full"):
                TerminalSession._persist(session)
        session.r.error.assert_called_once()
        self.assertIn("work stopped", session.r.error.call_args.args[0])

    def test_disk_failure_preserves_previous_state_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            write_checkpoint(path, {"turn": 1})
            with patch("frontier_agent.core.runtime.checkpoint.os.replace", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(CheckpointError, "disk full"):
                    write_checkpoint(path, {"turn": 2})
            self.assertEqual(json.loads(path.read_text()), {"turn": 1})
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_failed_checkpoint_propagates_before_next_turn_or_pause(self):
        async def fail(*args):
            raise CheckpointError("disk full")
        async def pause():
            self.fail("A failed checkpoint must stop before subsequent decisions")
        with self.assertRaises(CheckpointError):
            asyncio.run(_handle_turn_end(LoopConfig(), [], [], {}, 1, None, fail, pause))
