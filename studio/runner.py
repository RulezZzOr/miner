"""One isolated Frontier process per GUI task; durable events and approval IPC.

result.json follows the run failure contract: status (completed, incomplete, failed,
cancelled), reason and failure_kind. A mission run is completed only when its report
was saved.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import sys
import threading
import time
import uuid
from pathlib import Path

from apodex import cli, session
from apodex.observers import Approver, Decision
from apodex.render import Renderer
from apodex.switch_cli import configure
from dotenv import load_dotenv

try:
    from .progress_guard import ProgressGuard
    from .mission_report import MissionReport
    from .ssh_inventory import SSHInventory
except ImportError:
    from progress_guard import ProgressGuard
    from mission_report import MissionReport
    from ssh_inventory import SSHInventory

ENGLISH = ("Write all reports, questions, summaries, notes and generated documentation in English, "
           "regardless of the language of the input.")
FAILURE_KINDS = ("provider_unavailable", "time_limit", "max_turns", "progress_guard", "no_report", "crash", "setup", "")
# Provider outages are transient: the controller waits for the endpoint instead of
# spending an attempt. An unreachable or overloaded endpoint wins over the deadline
# that cut its retries short; a plain timeout at the deadline is a time limit.
PROVIDER_DOWN = re.compile(
    r"Error code: 5(?:02|03|04|29)\b|\b50[234]\b[^\n]{0,40}(?:Service Unavailable|Bad Gateway|Gateway Time)|"
    r"Service Unavailable|Bad Gateway|Gateway Time-?out|Loading model|model is (?:still )?loading|"
    r"Connection (?:refused|error|reset|aborted)|APIConnectionError|ConnectError|RemoteProtocolError|"
    r"Server disconnected|Remote end closed|No route to host|Network is unreachable|"
    r"Name or service not known|Temporary failure in name resolution|nodename nor servname|overloaded", re.I)
TIME_LIMIT = re.compile(r"\bwall_deadline\b|\bbudget_exhausted\b|time limit", re.I)
PROVIDER_SLOW = re.compile(r"APITimeoutError|ReadTimeout|ConnectTimeout|Request timed out|timed out", re.I)
# The frontier task runner states the kind it determined on a line of its own.
FAILURE_LINE = re.compile(r"^Failure kind: *([a-z_]+) *$", re.M)
# Prefixes the frontier gives model-call failures; any other error is not a provider outage.
LLM_ERROR = re.compile(r"^\W*LLM (?:call failed|configuration error)\b")
PROVIDER_MODULES = {"openai", "anthropic", "httpx", "httpcore"}
# A tool call that cannot get this long (at most 10% of a short attempt) before the report reserve is refused.
MIN_TOOL_SECONDS = 30
SAVE_NOW = ("Budget nearly exhausted: {left} left in this attempt. Call save_mission_report now with the verified "
            "results. If the work is not finished, save status blocked with concrete questions that name the "
            "remaining work or the missing input. A chat answer is not a saved report, and the controller stops "
            "the run at its limit. " + ENGLISH)
LAST_TURN = ("The next turn is the final turn of this attempt, and save_mission_report stays available in it. "
             "Only save_mission_report is accepted in that turn; other tools are refused. Ignore any instruction "
             "to answer in plain text or to copy deliverables to /outputs: in this Studio mission only a saved "
             "report counts. " + ENGLISH)
FINALIZING = ("In this Studio mission, finishing means calling save_mission_report with named fields. Do not "
              "copy deliverables to /outputs and do not end with a plain-text answer. " + ENGLISH)
NO_REPORT = ("No report is saved yet, and a chat answer does not finish this phase. Call save_mission_report now "
             "with named fields; if the work is incomplete, use status blocked with concrete questions. " + ENGLISH)
REPORT_ONLY = ("Refused: the attempt budget is exhausted, so only save_mission_report is accepted now. Save the "
               "verified results, or status blocked with the concrete remaining work. " + ENGLISH)
TOOL_TIME = ("Refused: about {left} seconds remain for tools before the time this attempt keeps for saving its "
             "report, which is too little for another tool call. Call save_mission_report now with the verified "
             "results, or status blocked with the concrete remaining work. " + ENGLISH)
REPORT_REJECTED = "Report NOT saved: {error}. Correct the fields and call save_mission_report again. " + ENGLISH
REPORT_FORMAT = ("Report NOT saved: {error}. Fix the format and call create_file again; preferably pass data "
                 "directly as a JSON object. Do not mark the work as complete. " + ENGLISH)
PHASE_TOOLS = ("This phase allows reading tools and submission via save_mission_report. "
               "Do not modify product files or select the report path yourself. " + ENGLISH)
NO_REPORT_REASON = "No mission report was saved: the worker finished without calling save_mission_report."


def report_margin(attempt_seconds):
    """Time kept free after the worker's own deadline for shutdown and result recording."""
    return max(20.0, 0.05 * float(attempt_seconds))


def limit_mission_tools(settings, attempt_seconds, elapsed=0.0):
    """Fit every tool and model call inside the attempt so the controller's kill is a last resort.

    The research deadline ends the agent loop cleanly before the controller's limit, and
    the loop clamps each model call and retry backoff to it. Static model timeouts are
    also capped, and the post-deadline salvage call is disabled: it produces a chat
    answer, never a saved mission report.
    """
    if not attempt_seconds:
        return None  # Legacy requests retain their existing profile budget.
    attempt = float(attempt_seconds)
    cap = max(1, int(attempt / 3))
    agent = settings["agent"]
    current = float(agent.get("tool_timeout_s", cap))
    agent["tool_timeout_s"] = min(current, cap) if current > 0 else cap
    usable = max(30, int(attempt - max(0.0, float(elapsed)) - report_margin(attempt)))
    agent["research_wall_time_s"] = usable
    agent["wall_deadline_reserve_s"] = 0
    agent["salvage_infra_errors"] = False
    for key in ("llm_timeout_s", "reasoning_only_timeout_s", "logical_call_timeout_s",
                "reporter_timeout_s", "reporter_phase_timeout_s", "finalization_timeout_s", "first_chunk_s"):
        try:
            value = float(agent.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > usable:
            agent[key] = usable
    return agent["tool_timeout_s"]


def limit_stream_timeouts(environ, seconds):
    """Cap first-chunk and stall waits of streamed model calls to the usable attempt time."""
    for name, default in (("FRONTIER_AGENT_LLM_FIRST_CHUNK_S", 0.0), ("FRONTIER_AGENT_LLM_STREAM_STALL_S", 180.0)):
        try:
            value = float(environ.get(name) or default)
        except ValueError:
            value = default
        if value > seconds:
            environ[name] = str(int(seconds))


def clamp_tool_timeouts(budget):
    """Cut every tool call to the time left before the report reserve.

    The profile cap (a third of the attempt) alone lets a call started late outlive
    the controller's limit. The loop's outer wait for each call is lowered to what is
    left, so a budget-aware tool such as bash also gets its own deadline inside it.
    """
    from frontier_agent.core.runtime.loop import tool_exec
    original = tool_exec._effective_tool_timeout

    def effective_tool_timeout(name, args, default_timeout):
        timeout = original(name, args, default_timeout)
        left = budget.tool_seconds()
        return timeout if left is None else max(1, min(int(timeout), max(int(left), int(budget.min_tool_seconds()))))

    tool_exec._effective_tool_timeout = effective_tool_timeout
    return original


def explicit_failure_kind(text, *, last=False):
    """The kind named on a 'Failure kind: <kind>' line, or ''."""
    kinds = [k for k in FAILURE_LINE.findall(str(text or "")) if k and k in FAILURE_KINDS]
    return (kinds[-1] if last else kinds[0]) if kinds else ""


def classify_failure(text, *, configuration_error=False):
    """Map a model-call failure message to a failure_kind of the run contract.

    An explicit 'Failure kind:' line wins; the first one counts, because the provider's
    own response follows it. A down endpoint is still a provider outage when the
    deadline cut its retries short.
    """
    text = str(text or "")
    explicit = explicit_failure_kind(text)
    if explicit and not (explicit == "time_limit" and PROVIDER_DOWN.search(text)):
        return explicit
    if PROVIDER_DOWN.search(text):
        return "provider_unavailable"
    if TIME_LIMIT.search(text):
        return "time_limit"
    if PROVIDER_SLOW.search(text):
        return "provider_unavailable"
    return "setup" if configuration_error else "crash"


def error_failure_kind(text):
    """Map a renderer error to a failure_kind; only model-call failures can be provider outages.

    The frontier appends 'Failure kind:' to workflow failures, after the exception text,
    so the last such line counts. Without it, a tool or loop error that mentions
    'Connection refused' or 'timed out' is a crash, not a provider outage.
    """
    text = str(text or "")
    explicit = explicit_failure_kind(text, last=True)
    if explicit:
        return explicit
    if LLM_ERROR.match(text):
        return classify_failure(text)
    return "time_limit" if TIME_LIMIT.search(text) else "crash"


def exception_failure_kind(exc):
    """Classify an exception that escaped the CLI by its type, as the frontier does."""
    provider = type(exc).__name__ == "LLMError" or type(exc).__module__.split(".")[0] in PROVIDER_MODULES
    return classify_failure(f"{type(exc).__name__}: {exc}") if provider else error_failure_kind(exc)


def stop_failure_kind(stopped_by):
    stopped_by = str(stopped_by or "")
    if stopped_by in {"max_turns", "max_attempts"}:
        return "max_turns"
    if stopped_by in {"wall_deadline", "budget_exhausted", "time_limit"}:
        return "time_limit"
    if stopped_by.startswith("progress_guard"):
        return "progress_guard"
    return ""


def settle_outcome(outcome, *, mission, saved, code, guard_reason=""):
    """Apply the run contract: only a saved report completes a mission run."""
    result = dict(outcome)
    result.setdefault("failure_kind", "")
    if code == 130 or result.get("status") == "cancelled":
        result.update(status="cancelled", failure_kind="")
    elif mission and saved:
        result.update(status="completed", reason="mission_report_saved", failure_kind="")
    elif guard_reason:
        result.update(status="incomplete", reason=guard_reason, failure_kind="progress_guard")
    elif code:
        result.update(status="failed", failure_kind=result["failure_kind"] or ("setup" if code == 2 else "crash"))
    elif mission and result.get("status") in {"completed", "incomplete"}:
        kind = result["failure_kind"] or stop_failure_kind(result.get("reason")) or "no_report"
        reason = result.get("reason") or ""
        if kind == "no_report":
            reason = NO_REPORT_REASON + (f" Stop reason: {reason}." if reason else "")
        elif kind == "time_limit":
            match = TIME_LIMIT.search(reason)
            detail = match.group(0) if match else (reason.strip().splitlines() or ["wall_deadline"])[0][:200]
            reason = f"The run reached its time limit before saving a report ({detail})."
        result.update(status="incomplete", reason=reason, failure_kind=kind)
    elif result.get("status") == "completed":
        result["failure_kind"] = ""
    if result["failure_kind"] not in FAILURE_KINDS:
        result["failure_kind"] = "crash"
    return result


class AttemptBudget:
    """Turn and time budget the controller enforces, with warnings before it runs out.

    Times are measured to the worker's own deadline (the controller limit minus the
    shutdown margin). Human approval waits extend the budget, as in the controller.
    """

    def __init__(self, request, clock=time.time):
        mission = request.get("mission") or {}
        self.seconds = float(mission.get("attempt_seconds") or 0)
        self.clock = clock
        self.started = float(request.get("created") or clock())
        self.margin = report_margin(self.seconds) if self.seconds else 0.0
        self.waited = 0.0
        self.turn = 0
        self.max_turns = int(request.get("max_turns") or 0)
        self.slowest_call = 0.0
        self.call_started = None
        self.warned = set()

    def remaining(self):
        if not self.seconds:
            return None
        return self.started + self.waited + self.seconds - self.margin - self.clock()

    def begin_call(self, turn, max_turns):
        self.turn, self.max_turns = turn, max_turns or self.max_turns
        self.call_started = time.monotonic()

    def end_call(self):
        if self.call_started is not None:
            self.slowest_call = max(self.slowest_call, time.monotonic() - self.call_started)
            self.call_started = None

    def reserve(self):
        """Time kept for the model call that saves the report."""
        return max(0.05 * self.seconds, 1.5 * self.slowest_call)

    def tool_seconds(self):
        """Longest a tool call may run and still leave the report reserve; None without a time budget."""
        remaining = self.remaining()
        return None if remaining is None else remaining - self.reserve()

    def min_tool_seconds(self):
        """Shortest useful tool window; a one-minute attempt still gets tool calls."""
        return min(MIN_TOOL_SECONDS, 0.1 * self.seconds)

    def report_only(self):
        """The final turn, or too little time for anything but the report."""
        remaining = self.remaining()
        return bool(self.max_turns and self.turn >= self.max_turns) or (
            remaining is not None and remaining <= self.reserve())

    def warning(self):
        """One-time 'save now' notices once <=2 turns or <=15% of the time remain."""
        parts = []
        turns_left = self.max_turns - self.turn + 1 if self.max_turns else None
        if turns_left is not None and turns_left <= 2 and "turns" not in self.warned:
            self.warned.add("turns")
            parts.append(f"{turns_left} turn{'s' if turns_left != 1 else ''} (including this one)")
        remaining = self.remaining()
        if (remaining is not None and "time" not in self.warned and
                remaining <= max(0.15 * self.seconds, 2.5 * self.slowest_call)):
            self.warned.add("time")
            parts.append(f"about {max(0, int(remaining))} seconds")
        return SAVE_NOW.format(left=" and ".join(parts)) if parts else None


class StderrTail:
    """Pass stderr through while keeping its tail for a preflight failure reason."""

    def __init__(self, stream):
        self.stream = stream
        self.tail = ""

    def write(self, text):
        self.tail = (self.tail + str(text))[-2000:]
        return self.stream.write(text)

    def __getattr__(self, name):
        return getattr(self.stream, name)


def extend_wall_deadline(seconds):
    """Human approval time does not count against the loop's own deadline."""
    try:
        from frontier_agent.core.execution_context import get_current_execution_scope
        from frontier_agent.core.loop_types import WALL_DEADLINE_MONOTONIC_KEY
        scope = get_current_execution_scope()
        deadline = scope.metadata.get(WALL_DEADLINE_MONOTONIC_KEY) if scope is not None else None
        if isinstance(deadline, (int, float)):
            scope.metadata[WALL_DEADLINE_MONOTONIC_KEY] = deadline + seconds
    except Exception:
        pass


def normalize_report_args(args):
    """Validate controller JSON before writing and normalize encoded data."""
    if args.get("ops") or args.get("rows") is not None:
        raise ValueError("For the report, use data as a JSON object or content as valid JSON text.")
    report = args.get("data") if args.get("data") is not None else json.loads(args.get("content", ""))
    if isinstance(report, str):
        report = json.loads(report)
    if not isinstance(report, dict):
        raise ValueError("The report must be a JSON object, not a list or standalone text.")
    normalized = {k: v for k, v in args.items() if k not in {"content", "data", "rows", "ops"}}
    return {**normalized, "data": report}


def run(directory: Path) -> int:
    parent_pid = os.getppid()

    def watch_parent():
        while True:
            time.sleep(2)
            if os.getppid() != parent_pid:
                # A crashed Studio must not leave an invisible agent running.
                os.killpg(os.getpgrp(), signal.SIGTERM)
                return

    threading.Thread(target=watch_parent, daemon=True).start()
    request = json.loads((directory / "request.json").read_text())
    lock = threading.Lock()
    outcome = {"status": "incomplete", "reason": "The process ended without a confirmed result.", "failure_kind": ""}

    def emit(kind, **data):
        event = {"type": kind, "time": time.time(), **data}
        with lock, (directory / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def write_result(result):
        temp = directory / "result.tmp"
        temp.write_text(json.dumps(result, ensure_ascii=False))
        temp.replace(directory / "result.json")
        emit("finished", **result)

    state = {}
    try:
        args = prepare(request, directory, emit, outcome, state)
    except Exception as exc:
        outcome.update(status="failed", reason=f"Worker setup failed: {exc}"[:2000], failure_kind="setup")
        emit("error", text=outcome["reason"])
        write_result(outcome)
        raise
    reporter, guard, review_evidence = state["reporter"], state["guard"], state["review_evidence"]
    stderr = sys.stderr = StderrTail(sys.stderr)
    code = 1
    try:
        emit("started", model=request["model"], mode=request["mode"])
        code = cli.main(args)
        if code == 2 and outcome["status"] == "incomplete" and not outcome["failure_kind"]:
            outcome.update(reason="Worker preflight failed: " + (stderr.tail.strip() or "no details"), failure_kind="setup")
    except Exception as exc:
        outcome.update(status="failed", reason=str(exc), failure_kind=exception_failure_kind(exc))
        emit("error", text=str(exc))
        raise
    finally:
        sys.stderr = stderr.stream
        outcome["session_id"] = os.environ.get("APODEX_SESSION_ID", "")
        if review_evidence:
            outcome["review_reads"] = review_evidence.calls
        result = settle_outcome(outcome, mission=bool(request.get("mission")),
                                saved=bool(reporter and reporter.saved), code=code, guard_reason=guard.stop_reason)
        write_result(result)
    return code


def prepare(request, directory, emit, outcome, state):
    """Install the Studio renderer, approval and mission policy; return the CLI arguments."""
    mission = request.get("mission") or {}
    # A plan retry after a progress-guard stop starts with discovery already exhausted.
    guard = ProgressGuard(mission.get("phase"), save_only=mission.get("save_only") is True)
    budget = AttemptBudget(request)
    state.update(guard=guard, reporter=None, review_evidence=None)
    # Every Studio run edits the project shown in the IDE. Keep per-run
    # logs and outputs, but bind file tools and shell work to that same root.
    activate_scratch = session.TerminalSession._activate_session_workspace

    def activate_project(session_id, project=None):
        activate_scratch(session_id, project)
        root = Path(request["cwd"]).resolve()
        link = Path(os.environ["APODEX_WORKSPACE_LINK"])
        if not link.is_symlink():
            raise RuntimeError("Missing working link for the Studio project.")
        link.unlink()
        link.symlink_to(root, target_is_directory=True)
        # Native file tools validate physical roots as well as /workspace.
        # Publishing the session symlink here rejects the exact project
        # path shown in the task, although it names the same directory.
        os.environ["FRONTIER_AGENT_WORKSPACE_DIR"] = str(root)
        os.environ["APODEX_HOST_WORKSPACE_DIR"] = str(root)

    session.TerminalSession._activate_session_workspace = staticmethod(activate_project)

    reporter = None
    inventory = None
    review_evidence = None
    if request.get("mission"):
        from plugins.tools._sandbox import resolve_runtime_path
        expected_report = (Path(request["cwd"]) / "company" / "projects" /
                           request["mission"]["id"] / "reports" /
                           (request["mission"]["attempt"] + ".json")).resolve()
        reporter = MissionReport(request, expected_report)
        import plugins.tools
        plugins.tools._BUILTIN_TOOLS.append(reporter.tool())
        if request["mission"].get("review_packet"):
            try:
                from .review_packet import ReviewEvidence
            except ImportError:
                from review_packet import ReviewEvidence
            def read_snapshot(digest):
                if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                    raise ValueError("Invalid evidence content identifier.")
                return (Path(request["review_objects"]) / digest).read_bytes()
            review_evidence = ReviewEvidence(request["mission"]["review_packet"]["sources"], read_snapshot)
            plugins.tools._BUILTIN_TOOLS.append(review_evidence.tool())
        if request["mission"]["phase"] == "build" and request.get("ssh_targets"):
            inventory = SSHInventory(request)
            plugins.tools._BUILTIN_TOOLS.append(inventory.tool())
        if request["mission"]["phase"] in {"plan", "review", "final"}:
            from apodex.observers import TerminalObserver
            from frontier_agent.core.loop_types import ToolCallIntervention
            original_call = TerminalObserver.on_tool_call

            async def planning_call(observer, ctx, tool_call):
                name = tool_call.get("name")
                args = tool_call.get("args") or {}
                allowed = name in {"read_file", "glob_search", "grep_search", "web_search",
                                   "web_fetch", "recover_result", "add_task", "update_task"}
                if review_evidence:
                    allowed = name == "read_review_evidence"
                if name == "create_file" and isinstance(args.get("path"), str):
                    candidate = Path(resolve_runtime_path(args["path"]))
                    if not candidate.is_absolute():
                        candidate = Path(request["cwd"]) / candidate
                    allowed = candidate.resolve() == expected_report and not args.get("ops")
                if not allowed:
                    return ToolCallIntervention(skip_with_result=PHASE_TOOLS)
                return await original_call(observer, ctx, tool_call)

            TerminalObserver.on_tool_call = planning_call
    state.update(reporter=reporter, review_evidence=review_evidence)
    if request.get("mission"):
        from apodex.observers import TerminalObserver
        from frontier_agent.core.loop_types import Intervention, ToolCallIntervention
        original_guarded_call = TerminalObserver.on_tool_call
        original_turn_end = TerminalObserver.on_turn_end
        original_llm_response = TerminalObserver.on_llm_response
        nudges = []

        async def guarded_call(observer, ctx, tool_call):
            if not reporter.saved and tool_call.get("name") != "save_mission_report":
                if budget.report_only():
                    observer.r.note(REPORT_ONLY)
                    return ToolCallIntervention(skip_with_result=REPORT_ONLY)
                left = budget.tool_seconds()
                if left is not None and left < budget.min_tool_seconds():
                    # A tool call now would run into the time kept for saving the report.
                    message = TOOL_TIME.format(left=max(0, int(left)))
                    observer.r.note(message)
                    return ToolCallIntervention(skip_with_result=message)
            if tool_call.get("name") == "save_mission_report":
                try:
                    report = reporter.validate(tool_call.get("args") or {})
                except (ValueError, TypeError, KeyError) as exc:
                    message = REPORT_REJECTED.format(error=str(exc)[:1800])
                    observer.r.note(message)
                    return ToolCallIntervention(skip_with_result=message)
                # Keep the existing file-write approval and journal. The model
                # cannot choose a path or bypass this by calling the tool directly.
                tool_call = {**tool_call, "name": "create_file", "args": {"path": str(expected_report), "data": report}}
            refused = guard.inspect(tool_call.get("name"), tool_call.get("args") or {})
            if refused:
                observer.r.note(refused)
                return ToolCallIntervention(skip_with_result=refused)
            args = tool_call.get("args") or {}
            normalized = None
            if tool_call.get("name") == "create_file" and isinstance(args.get("path"), str):
                candidate = Path(resolve_runtime_path(args["path"]))
                if not candidate.is_absolute():
                    candidate = Path(request["cwd"]) / candidate
                if candidate.resolve() == expected_report:
                    try:
                        normalized = normalize_report_args(args)
                    except (ValueError, TypeError) as exc:
                        return ToolCallIntervention(skip_with_result=REPORT_FORMAT.format(error=str(exc)[:1800]))
                    tool_call = {**tool_call, "args": normalized}
            prior = await original_guarded_call(observer, ctx, tool_call)
            if normalized is not None:
                prior = prior or ToolCallIntervention()
                if prior.rewrite_args is None:
                    prior.rewrite_args = normalized
            return prior

        async def budget_before_llm(observer, ctx):
            budget.begin_call(ctx.turn, ctx.max_turns)
            message = None if reporter.saved else budget.warning()
            if message:
                observer.r.note("Attempt budget nearly exhausted; the worker was asked to save its report now.")
                return Intervention(inject_messages=[message])
            return None

        async def guarded_llm_response(observer, ctx):
            budget.end_call()
            prior = await original_llm_response(observer, ctx)
            if (reporter.saved or ctx.tool_calls or len(nudges) >= 2 or
                    (ctx.metadata or {}).get("finish_reason") == "length"):
                return prior
            # A plain-text answer would end the run without a report. Ask for the
            # report instead (bounded), without spending a turn of the budget.
            nudges.append(ctx.turn)
            observer.r.note("No report saved yet; the worker was asked to call save_mission_report.")
            return Intervention(inject_messages=[NO_REPORT], continue_to_next_turn=True)

        async def guarded_turn_end(observer, ctx):
            prior = await original_turn_end(observer, ctx)
            if reporter.saved:
                return Intervention(stop_reason="mission_report_saved")
            notice = guard.end_turn()
            if guard.stop_reason:
                return Intervention(stop_reason="progress_guard")
            notes = [notice] if notice else []
            metadata = ctx.metadata if ctx.metadata is not None else {}
            if metadata.get("finalization_phase") and "finalizing" not in budget.warned:
                budget.warned.add("finalizing")
                notes.append(FINALIZING)
            if ctx.max_turns and ctx.turn == ctx.max_turns - 1:
                # The generic last-turn forcer strips every tool from the final turn;
                # a mission run still needs its report tool there.
                metadata.pop("_llm_strip_tools", None)
                notes.append(LAST_TURN)
            if not notes:
                return prior
            prior = prior or Intervention()
            prior.inject_messages = [*(prior.inject_messages or []), *notes]
            return prior

        TerminalObserver.on_tool_call = guarded_call
        TerminalObserver.on_turn_end = guarded_turn_end
        TerminalObserver.on_before_llm = budget_before_llm
        TerminalObserver.on_llm_response = guarded_llm_response
        if budget.seconds:
            from frontier_agent.components.observers.wall_clock_observer import WallClockDeadlineObserver
            original_wall_turn_end = WallClockDeadlineObserver.on_turn_end

            async def approval_aware_turn_end(observer, ctx):
                shift = budget.waited - getattr(observer, "_studio_waited", 0.0)
                if shift > 0 and observer._start is not None:
                    observer._start += shift
                    observer._studio_waited = budget.waited
                return await original_wall_turn_end(observer, ctx)

            WallClockDeadlineObserver.on_turn_end = approval_aware_turn_end
            clamp_tool_timeouts(budget)

    class GuiRenderer(Renderer):
        def __init__(self, *args, **kwargs):
            super().__init__(theme="mono", color=False)
            emit("session", session_id=os.environ.get("APODEX_SESSION_ID", ""))

        def thinking_delta(self, s):
            emit("thinking", text=s)

        def content_delta(self, s):
            emit("content", text=s)

        def turn_text_fallback(self, ai_text, thinking):
            if thinking:
                emit("thinking", text=thinking)
            if ai_text:
                emit("content", text=ai_text)

        def end_turn_text(self):
            emit("turn_end")

        def tool_call(self, name, args, risk_reason="", danger=False, *, call_id=""):
            emit(
                "tool_call", name=name, args=args, risk=risk_reason, danger=danger, call_id=call_id
            )
            super().tool_call(name, args, risk_reason, danger, call_id=call_id)

        def tool_result(self, name, result, *, is_error, ms=0, call_id=""):
            emit(
                "tool_result",
                name=name,
                text=str(result)[:24000],
                is_error=is_error,
                ms=ms,
                call_id=call_id,
            )
            super().tool_result(name, result, is_error=is_error, ms=ms, call_id=call_id)

        def activity_call(self, name, args, *, call_id=""):
            emit("tool_call", name=name, args=args, call_id=call_id)

        def activity_result(self, name, *, call_id="", is_error, ms=0, outcome=""):
            emit("tool_result", name=name, text=outcome, call_id=call_id, is_error=is_error, ms=ms)

        def final(self, text, **kwargs):
            stopped_by = kwargs.get("stopped_by", "")
            if reporter and not reporter.saved:
                # A chat answer is not a mission deliverable.
                outcome.update(status="incomplete", reason=NO_REPORT_REASON + (f" Stop reason: {stopped_by}." if stopped_by else ""),
                               failure_kind="no_report")
                emit("incomplete", text=text, **kwargs)
                super().incomplete(text, **kwargs)
                return
            outcome.update(status="completed", reason=stopped_by, failure_kind="")
            emit("final", text=text, **kwargs)
            super().final(text, **kwargs)

        def incomplete(self, text, **kwargs):
            if reporter and reporter.saved and kwargs.get("stopped_by") == "mission_report_saved":
                self.final("Report verified and saved. The controller will review the phase result.", **kwargs)
                return
            stopped_by = kwargs.get("stopped_by", "")
            outcome.update(status="incomplete", reason=stopped_by,
                           failure_kind=stop_failure_kind(stopped_by) or ("no_report" if reporter else ""))
            emit("incomplete", text=text, **kwargs)
            super().incomplete(text, **kwargs)

        def error(self, msg):
            outcome.update(status="failed", reason=msg, failure_kind=error_failure_kind(msg))
            emit("error", text=msg)
            super().error(msg)

        def llm_failure(self, msg, *, configuration_error=False):
            kind = classify_failure(msg, configuration_error=configuration_error)
            outcome.update(status="incomplete" if kind == "time_limit" else "failed", reason=msg, failure_kind=kind)
            emit("error", text=msg)
            super().llm_failure(msg, configuration_error=configuration_error)

        def note(self, msg):
            visible = msg
            if msg.startswith("workflow →"):
                visible = (
                    "Working mode: team of agents"
                    if request["mode"] == "agent_team"
                    else "Working mode: standalone agent"
                )
            elif msg.startswith("■ interrupted"):
                outcome.update(status="cancelled", reason="Stopped by the owner or by Studio.", failure_kind="")
            emit("note", text=visible)
            super().note(msg)

        def diff_preview(self, diff_text, *, stats=None):
            emit("diff", text=diff_text)

        def todos(self, items):
            emit("todos", items=[vars(i) if hasattr(i, "__dict__") else str(i) for i in items])

        def plan_review(self, plan):
            emit("plan", text=plan)

    class GuiApprover(Approver):
        def __init__(self, **kwargs):
            # The GUI checkbox is authoritative; do not inherit a TUI bypass.
            super().__init__(auto_approve=request["auto_approve"], interactive=True)

        async def confirm(self, name, target, reason, *, dangerous="", preview="", preview_kind=""):
            if self.auto_approve:
                return Decision(True)
            approval_id = uuid.uuid4().hex
            payload = {
                "id": approval_id,
                "name": name,
                "target": target,
                "reason": reason,
                "dangerous": dangerous,
                "preview": preview,
                "preview_kind": preview_kind,
            }
            temporary = directory / f"approval-{approval_id}.tmp"
            temporary.write_text(json.dumps(payload, ensure_ascii=False))
            temporary.replace(directory / f"approval-{approval_id}.json")
            emit("approval", **payload)
            path = directory / f"decision-{approval_id}.json"
            waiting = time.monotonic()
            while not path.exists():
                await asyncio.sleep(0.25)
            waited = time.monotonic() - waiting
            budget.waited += waited
            extend_wall_deadline(waited)
            decision = json.loads(path.read_text())
            emit("decision", id=approval_id, allow=decision.get("allow") is True)
            return Decision(decision.get("allow") is True, feedback=decision.get("feedback", ""))

    cli.Renderer = GuiRenderer
    session.Approver = GuiApprover
    persist_session = session.TerminalSession._persist

    def persist_with_usage(self):
        persist_session(self)
        outcome["usage"] = self.usage.to_dict()
        emit("usage", usage=outcome["usage"])

    session.TerminalSession._persist = persist_with_usage
    load_dotenv(request["env_file"], override=False)
    if request.get("oauth_provider") == "claude_console":
        os.environ["ANTHROPIC_CONFIG_DIR"] = request["oauth_config_dir"]
    configure(Path(request["config"]), request["profile"])
    if not request.get("mission"):
        os.environ["SWITCH_STUDIO_PHASE_INSTRUCTIONS"] = (
            "You are editing the project open in Studio. Save requested source files directly in the project, "
            "not only in chat or run artifacts. File tools map /workspace to the selected project. "
            "Shell commands already start in the physical project directory. Use relative paths in shell; "
            "do not cd to /workspace or create a /workspace mount or symlink. "
            f"The installed Python interpreter is {json.dumps(sys.executable)}; use this absolute path "
            "when running Python tests rather than assuming a command named python exists. "
            "Report the actual command output and any unresolved failures. " + ENGLISH)
    if request.get("mission"):
        phase = request["mission"]["phase"]
        scope = ("Only plan the later work. Do not implement the product. Your sole deliverable is the plan JSON. Do not research the web or attempt SSH now. "
                 "Use at most three local discovery calls, then save a short plan for the execution worker. "
                 "Put requested server inspection into an execution task; do not invent missing access requirements before an actual check. "
                 if phase == "plan" else "Review the supplied immutable evidence packet. Your sole deliverable is the review JSON. "
                 "Use at most two short read_review_evidence calls if needed. Shell execution is unavailable. "
                 "Do not claim you ran tests; controller-owned check results are in the evidence packet when available."
                 if phase in {"review", "final"} else "Implement only the assigned task and save its product files in /workspace.")
        os.environ["SWITCH_STUDIO_PHASE_INSTRUCTIONS"] = (
            f"This run is the {phase.upper()} phase of a multi-run controller. {scope} "
            "Finish by calling save_mission_report with structured named arguments. Never encode a JSON string or choose a report path. The application saves it. "
            "The overall product goal describes later work, not permission to change this phase. "
            "A chat answer or task-board update is not a saved report. Do not put this report in /outputs. "
            "File tools understand the /workspace alias. Shell commands run in the physical project directory; "
            "use relative paths there and never create or modify a /workspace mount or symlink. "
            "During BUILD, use create_file with literal content for product source code, without shell quoting. "
            "Native shell calls are one-shot: do not leave servers or jobs running in the background, "
            "including nohup. Test and temporary service in one bounded Python script using Popen, "
            "request timeouts, and finally terminate/wait for only the child you started. "
            "The controller owns persistent deployment. If a check needs more time than allowed, "
            "report the limitation instead of evading the time limit. "
            "Do not browse the web for routine implementation facts that the provided specification already settles. "
            "Separate observations from hypotheses in reports and documentation. An exit code alone does not "
            "identify the cause of a failure; passing a finite set of checks does not prove the absence of all bugs "
            "or memory leaks. State which checks actually ran and leave an unproven cause unknown. "
            "During review, request correction of unsupported causal claims in delivered documentation. "
            "If an essential input is missing, save a blocked report with concrete questions. " + ENGLISH)
        # Hide forbidden tools from the model as well as enforcing calls. A
        # visible bash schema otherwise invites repeated rejected attempts.
        import yaml
        phase_profile = Path(os.environ["SWITCH_REACT_PROFILE"] + ".yaml")
        settings = yaml.safe_load(phase_profile.read_text())
        elapsed = time.time() - budget.started
        tool_seconds = limit_mission_tools(settings, request["mission"].get("attempt_seconds"), elapsed)
        if tool_seconds:
            limit_stream_timeouts(os.environ, settings["agent"]["research_wall_time_s"])
            os.environ["SWITCH_STUDIO_PHASE_INSTRUCTIONS"] += (
                f" Each tool call has at most {tool_seconds:g} seconds, and the whole attempt ends after about "
                f"{settings['agent']['research_wall_time_s']:g} seconds. A tool call started near the end gets "
                "only the time that is left before the time kept for saving the report, and no tool call starts "
                "once too little is left. Save your report before then; reserve time to save it after a tool failure.")
        allowed_tools = set(settings["agent"]["agent_tools"]) - {"add_task", "update_task"}
        if phase in {"plan", "review", "final"}:
            allowed_tools &= {"read_file", "glob_search", "grep_search", "web_search", "web_fetch",
                              "recover_result", "create_file"}
        if phase == "plan":
            allowed_tools -= {"web_search", "web_fetch"}
        if not os.environ.get("SERPER_API_KEY", "").strip():
            allowed_tools.discard("web_search")
        if phase in {"plan", "review", "final"}:
            allowed_tools.discard("create_file")
        settings["agent"]["agent_tools"] = [t for t in settings["agent"]["agent_tools"] if t in allowed_tools] + ["save_mission_report"]
        if review_evidence:
            settings["agent"]["agent_tools"] = ["read_review_evidence", "save_mission_report"]
        if inventory:
            settings["agent"]["agent_tools"].append("ssh_inventory")
            os.environ["SWITCH_STUDIO_PHASE_INSTRUCTIONS"] += (
                " SSH inventory is configured and available through ssh_inventory. "
                "Call it with target=" + next(iter(inventory.targets)) +
                " and section=system, services, projects, or integrations. "
                "Use its timestamped evidence files for the remote inventory; do not retry bash ssh or HTTP on the SSH port. "
                "Earlier reports about missing SSH support predate this connector. "
                "Never ask for key contents or tokens. Integration key/dependency names prove only configuration presence, not functionality.")
        settings["agent"]["task_board"] = False
        phase_profile.write_text(yaml.safe_dump(settings, sort_keys=False))
    return [
        "--native",
        "--no-tui",
        "--no-color",
        "--cwd",
        request["cwd"],
        "--mode",
        request["mode"],
        "--max-turns",
        str(request["max_turns"]),
        "-p",
        request["task"],
    ]


if __name__ == "__main__":
    raise SystemExit(run(Path(sys.argv[1]).resolve()))
