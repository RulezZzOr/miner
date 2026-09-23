"""Protocol-aware compaction and native workspace isolation regressions."""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from apodex.native import prepare_native_runtime
from apodex.profiles import _build
from apodex.switch_cli import configure


def test_native_invocations_keep_private_aliases_across_new_and_resume(tmp_path, monkeypatch):
    from apodex.session import TerminalSession

    first_env: dict[str, str] = {}
    second_env: dict[str, str] = {}
    prepare_native_runtime(str(tmp_path), "first", environ=first_env)
    first = Path(first_env["APODEX_WORKSPACE_LINK"])
    (first / "owner.txt").write_text("first")
    prepare_native_runtime(str(tmp_path), "second", environ=second_env)
    second = Path(second_env["APODEX_WORKSPACE_LINK"])
    (second / "owner.txt").write_text("second")
    assert first != second
    assert (first / "owner.txt").read_text() == "first"
    for key, value in first_env.items():
        monkeypatch.setenv(key, value)
    TerminalSession._activate_session_workspace("third", str(tmp_path))
    assert first.resolve().parent.name == "third"
    assert not (first / "owner.txt").exists()
    assert (second / "owner.txt").read_text() == "second"
    TerminalSession._activate_session_workspace("first", str(tmp_path))
    assert (first / "owner.txt").read_text() == "first"
    assert (second / "owner.txt").read_text() == "second"


@pytest.mark.parametrize("mode", ["react", "agent_team"])
@pytest.mark.parametrize("protocol,dialect", [
    ("chat_completions", "openai"), ("chat_completions", "ollama"),
    ("anthropic", "openai"), ("bedrock", "openai"), ("responses", "openai"),
])
async def test_real_sdk_compaction_uses_selected_protocol(tmp_path, monkeypatch, protocol, dialect, mode):
    from apodex.llm import build_llm
    from frontier_agent.core.runtime.loop.compact_llm import LLMSummaryCompactor

    received = []
    summary = "SUMMARY_KEPT: earlier decisions and unresolved work"

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, dict(self.headers), data))
            if protocol in ("anthropic", "bedrock"):
                body = {"id": "msg_test", "type": "message", "role": "assistant",
                        "model": "fixture", "content": [{"type": "text", "text": summary}],
                        "stop_reason": "end_turn", "usage": {"input_tokens": 10, "output_tokens": 5}}
            elif protocol == "responses":
                body = {"id": "resp_test", "object": "response", "created_at": 1,
                        "model": "fixture", "status": "completed",
                        "output": [{"id": "msg_test", "type": "message", "role": "assistant",
                                    "status": "completed", "content": [
                                        {"type": "output_text", "text": summary, "annotations": []}]}]}
            else:
                body = {"id": "chatcmpl_test", "object": "chat.completion", "created": 1,
                        "model": "fixture", "choices": [{"index": 0, "finish_reason": "stop",
                            "message": {"role": "assistant", "content": summary}}]}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    if protocol in ("chat_completions", "responses"):
        base += "/v1"
    config = tmp_path / "agent.toml"
    config.write_text(f'''
    default_profile = "fixture"
    [profiles.fixture]
    model = "fixture"
    protocol = "{protocol}"
    chat_dialect = "{dialect}"
    base_url = "{base}"
    auth = "env"
    api_key_env = "SWITCH_SYNTHETIC_KEY"
    context_window = 32768
    max_output_tokens = 4096
    [profiles.fixture.extra_body]
    reasoning_effort = "high"
    ''')
    monkeypatch.setenv("SWITCH_SYNTHETIC_KEY", "synthetic-key")
    original = dict(os.environ)
    client = None
    try:
        configure(config)
        cfg = _build(mode).model_config
        assert cfg.protocol == protocol
        assert cfg.chat_dialect == dialect
        client = build_llm(cfg)
        events = []
        compactor = LLMSummaryCompactor(summary_llm=client, emit_event=events.append)
        compacted = await compactor.compact([
            {"role": "system", "content": "Project instructions"},
            {"role": "user", "content": "Earlier requirements"},
            {"role": "assistant", "content": "Earlier findings"},
            {"role": "user", "content": "Current work"},
        ], keep_recent=1)
        assert any(summary in str(m["content"]) for m in compacted), (compacted, events)
        assert len(received) == 1
        path, headers, data = received[0]
        headers = {k.lower(): v for k, v in headers.items()}
        if protocol == "anthropic":
            assert path == "/v1/messages"
            assert headers["x-api-key"] == "synthetic-key"
        elif protocol == "bedrock":
            assert path == "/model/fixture/invoke"
            assert headers["authorization"] == "Bearer synthetic-key"
            assert data["anthropic_version"] == "bedrock-2023-05-31"
        elif protocol == "responses":
            assert path == "/v1/responses"
            assert "temperature" not in data
            assert data["max_output_tokens"] > 0
        else:
            assert path == "/v1/chat/completions"
            assert data["reasoning_effort"] == "high"
            if dialect == "ollama":
                assert "max_tokens" in data
                assert "max_completion_tokens" not in data
            else:
                assert "max_completion_tokens" in data
    finally:
        if client:
            await client._client.close()
        server.shutdown()
        server.server_close()
        os.environ.clear()
        os.environ.update(original)
