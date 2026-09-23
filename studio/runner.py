"Jeden izolovaný Frontier proces na úlohu GUI; trvalé události a IPC schválení."

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
    "Po uváznutí nástroje ponechte čas na obnovu/oznamování v rámci omezeného počtu pokusů."
    if not attempt_seconds:
        return None  # Legacy requests retain their existing profile budget.
    cap = max(1, int(float(attempt_seconds) / 3))
    agent = settings["agent"]
    current = float(agent.get("tool_timeout_s", cap))
    agent["tool_timeout_s"] = min(current, cap) if current > 0 else cap
    return agent["tool_timeout_s"]


def normalize_report_args(args):
    "Před zápisem ověřte JSON řadiče a normalizujte kódovaná data."
    if args.get("ops") or args.get("rows") is not None:
        raise ValueError("Pro report použij data jako JSON objekt nebo content jako platný JSON text.")
    report = args.get("data") if args.get("data") is not None else json.loads(args.get("content", ""))
    if isinstance(report, str):
        report = json.loads(report)
    if not isinstance(report, dict):
        raise ValueError("Report musí být JSON objekt, ne seznam ani samostatný text.")
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
            raise RuntimeError("Chybí pracovní odkaz na projekt ve Studio.")
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
                        "Tato fáze dovoluje čtecí nástroje a odevzdání přes save_mission_report. "
                        "Produktové soubory neupravuj a nevybírej cestu reportu sám.")
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
                    message = "Report NEBYL uložen: " + str(exc)[:1800] + ". Opravte pole a znovu zavolejte save_mission_report."
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
                            f"Report NEBYL uložen: {exc}. Oprav formát a zavolej create_file znovu; "
                            "nejlépe předávej data přímo jako JSON objekt. Neoznačuj práci za hotovou.")
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
    outcome = {"status": "incomplete", "reason": "Proces skončil bez potvrzeného výsledku."}

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
                self.final("Report ověřen a uložen. Řadič zkontroluje výsledek fáze.", **kwargs)
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
            if msg.startswith("pracovní postup →"):
                visible = (
                    "Pracovní režim: tým agentů"
                    if request["mode"] == "agent_team"
                    else "Pracovní režim: samostatný agent"
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
    if not request.get("mission"):
        os.environ["SWITCH_STUDIO_PHASE_INSTRUCTIONS"] = (
            "Upravujete projekt otevřený ve Studiu. Uložte požadované zdrojové soubory přímo do projektu, "
            "nejen v chatu nebo artefaktech běhu. Nástroje pro práci se soubory mapují /workspace na vybraný projekt. "
            "Příkazové řádky již začínají ve fyzickém adresáři projektu. V shellu používejte relativní cesty; "
            "Nepřecházej příkazem cd do /workspace a nevytvářej pro /workspace přípojný bod ani symbolický odkaz. "
            f"Nainstalovaný Python interpreter je {json.dumps(sys.executable)}; tuto absolutní cestu použij "
            "při spouštění testů Pythonu místo předpokladu existence příkazu python. "
            "Nahlášte skutečný výstup příkazu a všechny nevyřešené selhání.")
    if request.get("mission"):
        phase = request["mission"]["phase"]
        scope = ("Nyní pouze naplánuj pozdější práci. Produkt neimplementuj. Jediným výstupem je JSON s plánem. Nyní nehledej na webu ani nezkoušej SSH. "
                 "Použijte nejvýše tři lokální volání objevování, poté uložte krátký plán pro pracovníka. "
                 "Vložte požadovanou inspekci serveru do úkolu ke spuštění; před skutečnou kontrolou nevymýšlejte chybějící požadavky na přístup. "
                 if phase == "plan" else "Přečtěte si aktuální zdrojové soubory bez jejich úpravy. Vaším jediným výstupem je kontrolní JSON. "
                 "V této fázi není spuštění shellu dostupné. Neuvádějte, že jste spustili testy: schválené kontroly provede řadič až po finální kontrolě."
                 if phase in {"review", "final"} else "Implementujte pouze přidělený úkol a uložte jeho výstupní soubory do /workspace.")
        os.environ["SWITCH_STUDIO_PHASE_INSTRUCTIONS"] = (
            f"Tento běh je fáze {phase.upper()} víceběhového řadiče. {scope} "
            "Dokončete voláním save_mission_report s pojmenovanými argumenty ve strukturovaném formátu. Nikdy nekódujte JSON jako řetězec ani nevolte cestu k reportu – aplikace ho uloží sama. "
            "Celkový cíl produktu popisuje pozdější práci; nedává oprávnění měnit tuto fázi. "
            "Odpověď v chatu ani aktualizace seznamu úkolů nejsou uloženou zprávou. Tuto zprávu neukládej do /outputs. "
            "Nástroje pro práci se soubory rozumí aliasu /workspace. Příkazy shellu běží ve fyzickém adresáři projektu; "
            "Během BUILD používejte create_file s doslovným obsahem pro zdrojový kód produktu, bez uvozovek pro shell. "
            "Používej tam relativní cesty a nikdy nevytvářej ani neupravuj přípojný bod nebo symbolický odkaz /workspace. "
            "Nativní volání shellu jsou jednorázová: neponechávejte servery ani úlohy běžící na pozadí, "
            "včetně nohup. Otestujte dočasnou službu v jednom omezeném Python skriptu pomocí Popen, "
            "časové limity požadavků a nakonec terminate/wait pouze pro podproces, který jsi spustil. "
            "Trvalé nasazení vlastní řadič. Pokud kontrola potřebuje více času, než je povoleno, "
            "uveď toto omezení; časový limit neobcházej. "
            "Nehledej na webu běžné implementační údaje, které již určuje zadaná specifikace. "
            "Ve zprávách a dokumentaci odděluj pozorování od hypotéz. Samotný návratový kód nestačí k tomu, aby bylo možné "
            "určit příčinu selhání; úspěšné provedení konečné sady kontrol neprokazuje nepřítomnost všech chyb "
            "ani úniků paměti. Uveďte, které kontroly skutečně proběhly, a neznámou příčinu ponechte nedokázanou. "
            "Během kontroly požadujte opravu nepodložených příčinných tvrzení v doručené dokumentaci. "
            "Pokud chybí nezbytný podklad, ulož zprávu se stavem blocked a konkrétními otázkami.")
    if request.get("mission"):
        # Hide forbidden tools from the model as well as enforcing calls. A
        # visible bash schema otherwise invites repeated rejected attempts.
        import yaml
        phase_profile = Path(os.environ["SWITCH_REACT_PROFILE"] + ".yaml")
        settings = yaml.safe_load(phase_profile.read_text())
        tool_seconds = limit_mission_tools(settings, request["mission"].get("attempt_seconds"))
        if tool_seconds:
            os.environ["SWITCH_STUDIO_PHASE_INSTRUCTIONS"] += (
                f" Každé volání nástroje má nejvýše {tool_seconds:g} sekund; časově omezený je i celý pokus. "
                "Rezervujte čas na uložení reportu i po selhání nástroje.")
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
                " Zásobník SSH je nakonfigurován a dostupný přes ssh_inventory. "
                "Zavolejte jej s parametrem target=" + next(iter(inventory.targets)) +
                " a section=system, services, projects nebo integrations. "
                "Použijte jeho časově označené důkazové soubory pro vzdálený inventář; neopakujte bash ssh ani HTTP na SSH portu. "
                "Předchozí reporty o chybějící podpoře SSH jsou starší než tento konektor. "
                "Nikdy nepožadujte obsah klíčů ani tokeny. Názvy integračního klíče nebo závislostí prokazují pouze přítomnost konfigurace, nikoli funkčnost.")
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
