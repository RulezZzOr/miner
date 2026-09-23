"Backend účtu Codex pomocí podporovaného app-serveru, nikoli extrahovaných tokenů."

from __future__ import annotations

import json
import os
import queue
import signal
import sys
import threading
import time
import uuid
from pathlib import Path

try:
    from .oauth import CodexRPC
except ImportError:
    from oauth import CodexRPC


def run(directory, rpc_factory=CodexRPC):
    request = json.loads((directory / "request.json").read_text())
    outcome = {"status": "failed", "reason": "Codex skončil bez výsledku."}
    rpc = None
    thread_id = turn_id = None
    cancelled = threading.Event()
    previous = signal.signal(signal.SIGINT, lambda *_: cancelled.set())
    parent_pid = os.getppid()
    items = {}
    final_text = ""

    def emit(kind, **data):
        with (directory / "events.jsonl").open("a") as stream:
            stream.write(
                json.dumps({"type": kind, "time": time.time(), **data}, ensure_ascii=False) + "\n"
            )

    def check_cancel():
        if cancelled.is_set() or os.getppid() != parent_pid:
            raise KeyboardInterrupt

    def approval(message):
        params = message.get("params", {})
        method = message["method"]
        if method not in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            rpc.send(
                {
                    "id": message["id"],
                    "error": {
                        "code": -32601,
                        "message": "Tento požadavek vyžaduje nepodporované UI; zamítnuto.",
                    },
                }
            )
            emit("note", text="Codex požadoval nepodporovanou interakci; akce byla odmítnuta.")
            return
        item = items.get(params.get("itemId"), {})
        changes = item.get("changes", [])
        preview = "\n".join(str(c.get("path", "")) + "\n" + str(c.get("diff", "")) for c in changes)
        payload = {
            "id": uuid.uuid4().hex,
            "name": "Codex · příkaz" if "commandExecution" in method else "Codex · změny souborů",
            "target": params.get("command") or params.get("grantRoot") or request["cwd"],
            "reason": params.get("reason") or "Codex žádá o schválení této akce.",
            "dangerous": "",
            "preview": preview
            or (
                str(params.get("command") or params.get("grantRoot") or "")
                + "\n\nPracovní složka: "
                + str(params.get("cwd") or request["cwd"])
            ),
            "preview_kind": "diff" if preview else "text",
        }
        path = directory / f"approval-{payload['id']}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False))
        tmp.replace(path)
        emit("approval", **payload)
        decision_file = directory / f"decision-{payload['id']}.json"
        while not decision_file.exists():
            check_cancel()
            time.sleep(0.15)
        allowed = json.loads(decision_file.read_text()).get("allow") is True
        denied = "decline"
        if params.get("availableDecisions") and "decline" not in params["availableDecisions"]:
            denied = "cancel"
        rpc.send({"id": message["id"], "result": {"decision": "accept" if allowed else denied}})
        emit("decision", id=payload["id"], allow=allowed)

    try:
        rpc = rpc_factory()
        account = rpc.request("account/read", {"refreshToken": False}).get("account") or {}
        if account.get("type") != "chatgpt":
            raise RuntimeError("Přihlas ChatGPT v okně Modely. API klíč tento profil nenahrazuje.")
        emit("started", model=request["model"], mode="codex")
        started = rpc.request(
            "thread/start",
            {
                "cwd": request["cwd"],
                "model": request["model"],
                "modelProvider": "openai",
                "approvalPolicy": "never" if request["auto_approve"] else "untrusted",
                "approvalsReviewer": "user",
                "sandbox": "workspace-write",
                "ephemeral": True,
                "developerInstructions": "Pracujete v Switch Studio. Dokončete úkol uživatele v poskytnutém projektu. Nevytvářejte podagenty; toto UI spouští jediného agenta Codex.",
            },
        )
        thread_id = started["thread"]["id"]
        emit("session", session_id=thread_id)
        check_cancel()
        turn = rpc.request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": request["task"]}],
            },
        )
        turn_id = turn["turn"]["id"]
        while True:
            check_cancel()
            try:
                event = rpc.events.get(timeout=0.25)
            except queue.Empty:
                continue
            method, params = event.get("method", ""), event.get("params", {})
            if "id" in event and method:
                approval(event)
                continue
            if method == "studio/disconnected":
                raise RuntimeError("Spojení s Codex bylo přerušeno.")
            if params.get("threadId") not in {None, thread_id}:
                continue
            if method == "item/agentMessage/delta":
                emit("content", text=params.get("delta", ""))
            elif method in {"item/reasoning/summaryTextDelta", "item/reasoning/textDelta"}:
                emit("thinking", text=params.get("delta", ""))
            elif method in {"item/started", "item/completed"}:
                item = params.get("item", {})
                items[item.get("id")] = item
                kind = item.get("type")
                if kind == "agentMessage" and method == "item/completed":
                    final_text = item.get("text", "")
                    emit("turn_end")
                elif kind in {"commandExecution", "fileChange", "mcpToolCall", "webSearch"}:
                    if method == "item/started":
                        emit("tool_call", name=kind, args=item, call_id=item.get("id", ""))
                    else:
                        emit(
                            "tool_result",
                            name=kind,
                            text=str(item.get("aggregatedOutput") or item.get("status") or ""),
                            is_error=item.get("status") in {"failed", "declined"},
                            call_id=item.get("id", ""),
                        )
                        if kind == "fileChange":
                            emit(
                                "diff",
                                text="\n".join(c.get("diff", "") for c in item.get("changes", [])),
                            )
                            if item.get("status") == "completed":
                                emit(
                                    "changed_files",
                                    paths=[
                                        c["path"] for c in item.get("changes", []) if "path" in c
                                    ],
                                )
            elif method == "error":
                emit(
                    "error",
                    text="Codex hlásí chybu poskytovatele. "
                    + ("Opakuje požadavek." if params.get("willRetry") else ""),
                )
            elif method == "turn/completed" and params.get("turn", {}).get("id") == turn_id:
                status = params["turn"].get("status")
                outcome.update(
                    status={"completed": "completed", "interrupted": "cancelled"}.get(
                        status, "failed"
                    ),
                    reason=str(status),
                )
                emit(
                    "final" if status == "completed" else "error",
                    text=final_text
                    if status == "completed"
                    else "Codex úlohu nedokončil: " + str(status),
                )
                break
    except KeyboardInterrupt:
        outcome.update(status="cancelled", reason="Zastaveno uživatelem nebo ukončením Studia.")
        if rpc and thread_id and turn_id:
            try:
                rpc.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=2)
            except (OSError, RuntimeError):
                pass
    except Exception as exc:
        outcome.update(status="failed", reason=str(exc))
        emit("error", text=str(exc))
    finally:
        if rpc:
            rpc.close()
        signal.signal(signal.SIGINT, previous)
        outcome["session_id"] = thread_id or ""
        tmp = directory / "result.tmp"
        tmp.write_text(json.dumps(outcome, ensure_ascii=False))
        tmp.replace(directory / "result.json")
        emit("finished", **outcome)
    return 0


if __name__ == "__main__":
    raise SystemExit(run(Path(sys.argv[1]).resolve()))
