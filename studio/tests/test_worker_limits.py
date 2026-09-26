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

from studio import runner
from studio.runner import (ENGLISH, FINALIZING, LAST_TURN, NO_REPORT, PHASE_TOOLS, REPORT_FORMAT, REPORT_ONLY,
                           REPORT_REJECTED, SAVE_NOW, TOOL_TIME, AttemptBudget, clamp_tool_timeouts, classify_failure,
                           error_failure_kind, exception_failure_kind, limit_mission_tools, limit_stream_timeouts,
                           settle_outcome, stop_failure_kind)


class WorkerLimitTests(unittest.TestCase):
    def test_profile_ceiling_and_legacy_requests(self):
        settings = {"agent": {"tool_timeout_s": 900}}
        self.assertIsNone(limit_mission_tools(settings, None))
        self.assertEqual(settings["agent"]["tool_timeout_s"], 900)
        self.assertEqual(limit_mission_tools(settings, 600), 200)
        settings["agent"]["tool_timeout_s"] = 12
        self.assertEqual(limit_mission_tools(settings, 600), 12)

    def test_model_timeouts_and_research_deadline_fit_inside_the_attempt(self):
        # Values the Switch profile ships with: a single call could outlive a 300 s plan.
        agent = {"tool_timeout_s": 1800, "llm_timeout_s": 600, "reasoning_only_timeout_s": 600,
                 "logical_call_timeout_s": 900, "reporter_timeout_s": 600, "reporter_phase_timeout_s": 1800,
                 "research_wall_time_s": "${RESEARCH_WALL_TIME:-9000}", "wall_deadline_reserve_s": "${REPORT_WALL_TIME:-1800}"}
        settings = {"agent": dict(agent)}
        limit_mission_tools(settings, 300, elapsed=10)
        limited = settings["agent"]
        self.assertEqual(limited["tool_timeout_s"], 100)
        self.assertEqual(limited["research_wall_time_s"], 270)
        self.assertEqual(limited["wall_deadline_reserve_s"], 0)
        self.assertFalse(limited["salvage_infra_errors"])
        for key in ("llm_timeout_s", "reasoning_only_timeout_s", "logical_call_timeout_s",
                    "reporter_timeout_s", "reporter_phase_timeout_s"):
            self.assertLessEqual(limited[key], 270, key)
        self.assertNotIn("first_chunk_s", limited)  # disabled features stay disabled
        settings = {"agent": dict(agent)}
        limit_mission_tools(settings, 3600)
        self.assertEqual(settings["agent"]["llm_timeout_s"], 600)  # never raised
        environ = {"FRONTIER_AGENT_LLM_FIRST_CHUNK_S": "600"}
        limit_stream_timeouts(environ, 270)
        self.assertEqual(environ, {"FRONTIER_AGENT_LLM_FIRST_CHUNK_S": "270"})
        environ = {}
        limit_stream_timeouts(environ, 120)
        self.assertEqual(environ, {"FRONTIER_AGENT_LLM_STREAM_STALL_S": "120"})

    def test_provider_outages_are_classified_as_transient(self):
        loading = ("Provider/model: local/qwen\nReason: exhausted\nProvider response: Error code: 503 - "
                   "{'error': {'message': 'Loading model', 'type': 'unavailable_error'}}")
        for text in (loading, "Connection error.", "[Errno 111] Connection refused", "Request timed out.",
                     "Error code: 502 - Bad Gateway"):
            self.assertEqual(classify_failure(text), "provider_unavailable", text)
        self.assertEqual(classify_failure("Reason: wall_deadline\nProvider response: wall_deadline reached (3s left)"), "time_limit")
        # A slow model cut off at the deadline is a time limit; a down endpoint is not.
        self.assertEqual(classify_failure("Reason: wall_deadline\nProvider response: Request timed out."), "time_limit")
        self.assertEqual(classify_failure("Reason: wall_deadline\nProvider response: " + loading), "provider_unavailable")
        self.assertEqual(classify_failure("Error code: 401 - invalid api key", configuration_error=True), "setup")
        self.assertEqual(classify_failure("agent loop failed: KeyError 'x'"), "crash")
        self.assertEqual([stop_failure_kind(s) for s in ("max_turns", "wall_deadline", "progress_guard: x", "no_tool")],
                         ["max_turns", "time_limit", "progress_guard", ""])

    def test_mission_run_is_completed_only_with_a_saved_report(self):
        chat = {"status": "completed", "reason": "no_tool", "failure_kind": ""}
        result = settle_outcome(chat, mission=True, saved=False, code=0)
        self.assertEqual((result["status"], result["failure_kind"]), ("incomplete", "no_report"))
        self.assertIn("save_mission_report", result["reason"])
        self.assertEqual(settle_outcome(chat, mission=False, saved=False, code=0)["status"], "completed")
        saved = settle_outcome({"status": "incomplete", "reason": "", "failure_kind": ""}, mission=True, saved=True, code=0)
        self.assertEqual((saved["status"], saved["failure_kind"]), ("completed", ""))
        turns = settle_outcome({"status": "incomplete", "reason": "max_turns", "failure_kind": "max_turns"},
                               mission=True, saved=False, code=0)
        self.assertEqual((turns["status"], turns["reason"], turns["failure_kind"]), ("incomplete", "max_turns", "max_turns"))
        deadline = settle_outcome({"status": "incomplete", "reason": "wall_deadline", "failure_kind": "time_limit"},
                                  mission=True, saved=False, code=0)
        self.assertIn("time limit", deadline["reason"])
        provider = settle_outcome({"status": "failed", "reason": "Error code: 503", "failure_kind": "provider_unavailable"},
                                  mission=True, saved=False, code=0)
        self.assertEqual((provider["status"], provider["failure_kind"]), ("failed", "provider_unavailable"))
        guard = settle_outcome(chat, mission=True, saved=False, code=0, guard_reason="progress_guard: repeated")
        self.assertEqual((guard["status"], guard["failure_kind"]), ("incomplete", "progress_guard"))
        self.assertEqual(settle_outcome(chat, mission=True, saved=True, code=130)["status"], "cancelled")
        crash = settle_outcome({"status": "incomplete", "reason": "x", "failure_kind": ""}, mission=True, saved=False, code=1)
        self.assertEqual((crash["status"], crash["failure_kind"]), ("failed", "crash"))
        preflight = settle_outcome({"status": "incomplete", "reason": "x", "failure_kind": ""}, mission=False, saved=False, code=2)
        self.assertEqual(preflight["failure_kind"], "setup")

    def test_budget_warns_once_per_trigger_and_limits_the_last_turn_to_the_report(self):
        now = [1000.0]
        budget = AttemptBudget({"created": 1000, "max_turns": 8, "mission": {"attempt_seconds": 300}}, clock=lambda: now[0])
        budget.begin_call(1, 8)
        self.assertIsNone(budget.warning())
        self.assertFalse(budget.report_only())
        budget.begin_call(7, 8)
        message = budget.warning()
        self.assertIn("2 turns", message)
        self.assertIn("save_mission_report", message)
        self.assertIsNone(budget.warning())
        now[0] = 1000 + 300 - 20 - 44  # 15% of the attempt is left before the worker's deadline
        self.assertIn("seconds", budget.warning())
        self.assertIsNone(budget.warning())
        self.assertFalse(budget.report_only())
        budget.begin_call(8, 8)
        self.assertTrue(budget.report_only())
        idle = AttemptBudget({"created": 1000, "max_turns": 40, "mission": {"attempt_seconds": 600}}, clock=lambda: now[0])
        now[0] = 1000 + 600 - 30 - 20
        idle.begin_call(3, 40)
        self.assertTrue(idle.report_only())
        idle.waited = 120  # a human approval wait extends the attempt
        self.assertFalse(idle.report_only())
        self.assertIsNone(AttemptBudget({"max_turns": 5}).remaining())

    def test_explicit_failure_kind_wins_and_only_model_failures_are_outages(self):
        # The frontier task runner marks a non-LLM workflow exception as a crash.
        for text in ("stateful-react-agent workflow failed: [Errno 111] Connection refused\nFailure kind: crash",
                     "stateful-react-agent workflow failed: Command 'pytest' timed out after 60 seconds\nFailure kind: crash",
                     "workflow failed: tool said\nFailure kind: provider_unavailable\nFailure kind: crash"):
            self.assertEqual(error_failure_kind(text), "crash", text)
        self.assertEqual(error_failure_kind("workflow failed: Error code: 503\nFailure kind: provider_unavailable"),
                         "provider_unavailable")
        # Without the line, a tool or loop error is a crash whatever it mentions.
        for text in ("agent loop failed: [Errno 111] Connection refused", "agent loop failed: request timed out",
                     "Session checkpoint failed; work stopped: server overloaded"):
            self.assertEqual(error_failure_kind(text), "crash", text)
        self.assertEqual(error_failure_kind("\u2717 LLM call failed (exhausted):\n  Connection error."), "provider_unavailable")
        self.assertEqual(error_failure_kind("\u2717 LLM call failed (wall_deadline):\n  Request timed out."), "time_limit")
        # In a model-call failure the provider response follows the frontier's own line.
        failure = ("Provider/model: local/qwen\nReason: exhausted\nFailure kind: setup\n"
                   "Provider response: Error code: 401\nFailure kind: provider_unavailable")
        self.assertEqual(classify_failure(failure), "setup")
        self.assertEqual(classify_failure("Reason: wall_deadline\nFailure kind: time_limit\nProvider response: Loading model"),
                         "provider_unavailable")
        self.assertEqual(classify_failure("Failure kind: bogus\nProvider response: Request timed out."), "provider_unavailable")
        self.assertEqual(exception_failure_kind(ConnectionRefusedError(111, "Connection refused")), "crash")
        provider_error = type("APIConnectionError", (Exception,), {"__module__": "openai._exceptions"})
        self.assertEqual(exception_failure_kind(provider_error("Connection error.")), "provider_unavailable")

    def test_tool_calls_are_cut_to_the_time_left_before_the_report_reserve(self):
        from frontier_agent.core.runtime.loop import tool_exec
        now = [1000.0]
        budget = AttemptBudget({"created": 1000, "max_turns": 40, "mission": {"attempt_seconds": 1800}},
                               clock=lambda: now[0])
        settings = {"agent": {"tool_timeout_s": 1800}}
        self.assertEqual(limit_mission_tools(settings, 1800), 600)
        original = clamp_tool_timeouts(budget)
        self.addCleanup(setattr, tool_exec, "_effective_tool_timeout", original)
        now[0] = 1000 + 100
        self.assertEqual(tool_exec._effective_tool_timeout("read_file", {}, 600), 600)
        now[0] = 1000 + 1500  # 210 s to the worker deadline, 90 s of them kept for the report
        self.assertEqual(budget.tool_seconds(), 120)
        self.assertFalse(budget.report_only())
        self.assertEqual(tool_exec._effective_tool_timeout("read_file", {}, 600), 120)
        bash_wait = tool_exec._effective_tool_timeout("bash", {}, 600)
        self.assertEqual(bash_wait, 120)
        self.assertLessEqual(tool_exec._tool_budget("bash", bash_wait), 120)
        now[0] = 1000 + 1620  # a 600 s call started here used to outlive the controller's kill
        self.assertEqual(budget.min_tool_seconds(), runner.MIN_TOOL_SECONDS)
        self.assertLess(budget.tool_seconds(), budget.min_tool_seconds())  # refused; only the report is written
        self.assertEqual(tool_exec._effective_tool_timeout("create_file", {}, 600), runner.MIN_TOOL_SECONDS)
        # A one-minute attempt keeps a usable tool window instead of refusing every call.
        short = AttemptBudget({"created": 1000, "max_turns": 3, "mission": {"attempt_seconds": 60}}, clock=lambda: 1003.0)
        self.assertEqual((short.min_tool_seconds(), round(short.tool_seconds())), (6, 34))

        class Slow:
            async def ainvoke(self, args):
                await asyncio.sleep(10)
                return "finished"

        now[0] = 1000 + 1710 - 90 - 1  # one second left for tools
        started = time.monotonic()
        with patch.object(runner, "MIN_TOOL_SECONDS", 1):
            result = asyncio.run(tool_exec.execute_tools([{"name": "slow", "args": {}, "id": "c1"}],
                                                         {"slow": Slow()}, 600, 1, 0))[0]
        self.assertTrue(result.is_error)
        self.assertIn("timed out after 1s", result.result)
        self.assertLess(time.monotonic() - started, 5)

    def test_injected_budget_prompts_require_english_output(self):
        for prompt in (SAVE_NOW, NO_REPORT, LAST_TURN, FINALIZING, REPORT_ONLY, TOOL_TIME, REPORT_REJECTED,
                       REPORT_FORMAT, PHASE_TOOLS):
            self.assertIn(ENGLISH, prompt)
        self.assertIn(ENGLISH, TOOL_TIME.format(left=12))
        self.assertIn(ENGLISH, REPORT_REJECTED.format(error="tasks must not be empty"))

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
