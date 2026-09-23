"""Deterministic JSONL peer for the real Studio transport and runner tests."""

import json
import sys
from pathlib import Path

directory, scenario = Path(sys.argv[1]), sys.argv[2]


def send(message):
    print(json.dumps(message), flush=True)


def event(method, **params):
    send({"method": method, "params": {"threadId": "test-thread", **params}})


for line in sys.stdin:
    message = json.loads(line)
    with (directory / "rpc.jsonl").open("a") as log:
        log.write(json.dumps(message) + "\n")
    method = message.get("method")
    result = {}
    if method == "account/read":
        result = {"account": {"type": "chatgpt", "planType": "pro"}}
    elif method == "model/list":
        result = {"data": [{"id": "test-model", "displayName": "Test model"}]}
    elif method == "thread/start":
        result = {"thread": {"id": "test-thread"}}
    elif method == "turn/start":
        send({"id": message["id"], "result": {"turn": {"id": "test-turn"}}})
        event("item/agentMessage/delta", delta="Pracuji…")
        if scenario == "failure":
            event("turn/completed", turn={"id": "test-turn", "status": "failed"})
        elif scenario == "disconnect":
            sys.exit(1)
        elif scenario == "cancel":
            continue
        else:
            send(
                {
                    "id": "approval-from-server",
                    "method": "item/commandExecution/requestApproval",
                    "params": {
                        "threadId": "test-thread",
                        "itemId": "cmd",
                        "command": "create result.txt",
                        "cwd": str(directory),
                    },
                }
            )
        continue
    elif method == "turn/interrupt":
        send({"id": message["id"], "result": {}})
        event("turn/completed", turn={"id": "test-turn", "status": "interrupted"})
        continue
    elif method == "account/login/start":
        send(
            {
                "id": message["id"],
                "result": {
                    "loginId": "login-test",
                    "authUrl": "https://auth.openai.com/oauth/authorize?state=test",
                },
            }
        )
        event("account/login/completed", loginId="login-test", success=True)
        continue
    elif message.get("id") == "approval-from-server":
        allowed = message.get("result", {}).get("decision") == "accept"
        if allowed:
            (directory / "result.txt").write_text("OAUTH_TEST_OK")
            event(
                "item/completed",
                item={
                    "id": "edit",
                    "type": "fileChange",
                    "status": "completed",
                    "changes": [{"path": str(directory / "result.txt"), "diff": "+OAUTH_TEST_OK"}],
                },
            )
        event(
            "item/completed",
            item={
                "id": "answer",
                "type": "agentMessage",
                "text": "Done" if allowed else "Rejected",
            },
        )
        event("turn/completed", turn={"id": "test-turn", "status": "completed"})
        continue
    if "id" in message:
        send({"id": message["id"], "result": result})
