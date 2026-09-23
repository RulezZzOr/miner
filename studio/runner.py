"""One isolated Frontier process per GUI task; durable events and approval IPC."""

from __future__ import annotations

import asyncio
import json
import os
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


def limit_mission_tools(settings, attempt_seconds):
    """Leave recovery/reporting time after a stuck tool in a bounded attempt."""
    if not attempt_seconds:
        return None  # Legacy requests retain their existing profile budget.
    cap = max(1, int(float(attempt_seconds) / 3))
    agent = settings["agent"]
    current = float(agent.get("tool_timeout_s", cap))
    agent["tool_timeout_s"] = min(current, cap) if current > 0 else cap
    return agent["tool_timeout_s"]


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
    guard = ProgressGuard((request.get("mission") or {}).get("phase"))
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
    if request.get("mission"):
        from plugins.tools._sandbox import resolve_runtime_path
        expected_report = (Path(request["cwd"]) / "company" / "projects" /
                           request["mission"]["id"] / "reports" /
                           (request["mission"]["attempt"] + ".json")).resolve()
        reporter = MissionReport(request, expected_report)
        import plugins.tools
        plugins.tools._BUILTIN_TOOLS.append(reporter.tool())
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
                if name == "create_file" and isinstance(args.get("path"), str):
                    candidate = Path(resolve_runtime_path(args["path"]))
                    if not candidate.is_absolute():
                        candidate = Path(request["cwd"]) / candidate
                    allowed = candidate.resolve() == expected_report and not args.get("ops")
                if not allowed:
                    return ToolCallIntervention(skip_with_result=
                        "This phase allows reading tools and submission via save_mission_report. "
                        "Do not modify product files or select the report path yourself.")
                return await original_call(observer, ctx, tool_call)

            TerminalObserver.on_tool_call = planning_call
    if request.get("mission"):
        from apodex.observers import TerminalObserver
        from frontier_agent.core.loop_types import Intervention, ToolCallIntervention
        original_guarded_call = TerminalObserver.on_tool_call
        original_turn_end = TerminalObserver.on_turn_end

        async def guarded_call(observer, ctx, tool_call):
            if tool_call.get("name") == "save_mission_report":
                try:
                    report = reporter.validate(tool_call.get("args") or {})
                except (ValueError, TypeError, KeyError) as exc:
                    message = "Report NOT saved: " + str(exc)[:1800] + ". Correct the fields and call save_mission_report again."
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
                        return ToolCallIntervention(skip_with_result=
                            f"Report NOT saved: {exc}. Fix the format and call create_file again; "
                            "preferably pass data directly as a JSON object. Do not mark the work as complete.")
                    tool_call = {**tool_call, "args": normalized}
            prior = await original_guarded_call(observer, ctx, tool_call)
            if normalized is not None:
                prior = prior or ToolCallIntervention()
                if prior.rewrite_args is None:
                    prior.rewrite_args = normalized
            return prior

        async def guarded_turn_end(observer, ctx):
            prior = await original_turn_end(observer, ctx)
            if reporter.saved:
                return Intervention(stop_reason="mission_report_saved")
            guard.end_turn()
            if guard.stop_reason:
                return Intervention(stop_reason="progress_guard")
            return prior

        TerminalObserver.on_tool_call = guarded_call
        TerminalObserver.on_turn_end = guarded_turn_end
    lock = threading.Lock()
    outcome = {"status": "incomplete", "reason": "The process ended without a confirmed result."}

    def emit(kind, **data):
        event = {"type": kind, "time": time.time(), **data}
        with lock, (directory / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

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
            outcome.update(status="completed", reason=kwargs.get("stopped_by", ""))
            emit("final", text=text, **kwargs)
            super().final(text, **kwargs)

        def incomplete(self, text, **kwargs):
            if reporter and reporter.saved and kwargs.get("stopped_by") == "mission_report_saved":
                self.final("Report verified and saved. The controller will review the phase result.", **kwargs)
                return
            outcome.update(status="incomplete", reason=kwargs.get("stopped_by", ""))
            emit("incomplete", text=text, **kwargs)
            super().incomplete(text, **kwargs)

        def error(self, msg):
            outcome.update(status="failed", reason=msg)
            emit("error", text=msg)
            super().error(msg)

        def llm_failure(self, msg, *, configuration_error=False):
            outcome.update(status="failed", reason=msg)
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
            while not path.exists():
                await asyncio.sleep(0.25)
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
    if request.get("mission"):
        phase = request["mission"]["phase"]
        scope = ("Only plan the later work. Do not implement the product. Your sole deliverable is the plan JSON. Do not research the web or attempt SSH now. "
                 "Use at most three local discovery calls, then save a short plan for the execution worker. "
                 "Put requested server inspection into an execution task; do not invent missing access requirements before an actual check. "
                 if phase == "plan" else "Read the actual source files without editing them. Your sole deliverable is the review JSON. "
                 "Shell execution is unavailable in this phase. Do not claim you ran tests: the controller executes the approved checks after final review."
                 if phase in {"review", "final"} else "Implement only the assigned task and save its product files in /workspace.")
        os.environ["SWITCH_STUDIO_PHASE_INSTRUCTIONS"] = (
            f"This run is the {phase.upper()} phase of a multi-run controller. {scope} "
            "Finish by calling save_mission_report with structured named arguments. Never encode a JSON string or choose a report path. The application saves it. "
            "The overall product goal describes later work, not permission to change this phase. "
            "A chat answer or task-board update is not a saved report. Do not put this report in /outputs. "
            "File tools understand the /workspace alias. Shell commands run in the physical project directory; "
            "During BUILD, use create_file with literal content for product source code, without shell quoting. "
            "use relative paths there and never create or modify a /workspace mount or symlink. "
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
            "If an essential input is missing, save a blocked report with concrete questions.")
    if request.get("mission"):
        # Hide forbidden tools from the model as well as enforcing calls. A
        # visible bash schema otherwise invites repeated rejected attempts.
        import yaml
        phase_profile = Path(os.environ["SWITCH_REACT_PROFILE"] + ".yaml")
        settings = yaml.safe_load(phase_profile.read_text())
        tool_seconds = limit_mission_tools(settings, request["mission"].get("attempt_seconds"))
        if tool_seconds:
            os.environ["SWITCH_STUDIO_PHASE_INSTRUCTIONS"] += (
                f" Each tool call has at most {tool_seconds:g} seconds; the whole attempt is also bounded. "
                "Reserve time to save your report after a tool failure.")
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
    args = [
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
    code = 1
    try:
        emit("started", model=request["model"], mode=request["mode"])
        code = cli.main(args)
    except Exception as exc:
        outcome.update(status="failed", reason=str(exc))
        emit("error", text=str(exc))
        raise
    finally:
        outcome["session_id"] = os.environ.get("APODEX_SESSION_ID", "")
        if code:
            outcome["status"] = "cancelled" if code == 130 else "failed"
        if guard.stop_reason:
            outcome.update(status="incomplete", reason=guard.stop_reason)
        temp = directory / "result.tmp"
        temp.write_text(json.dumps(outcome, ensure_ascii=False))
        temp.replace(directory / "result.json")
        emit("finished", **outcome)
    return code


if __name__ == "__main__":
    raise SystemExit(run(Path(sys.argv[1]).resolve()))
