"""Switch-specific wiring checks; upstream capability tests remain unchanged."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml
from openai import AsyncOpenAI

from apodex.switch_cli import ROOT, configure, workflow_settings
from frontier_agent.infra.openai_client import OpenAIClient


@pytest.mark.parametrize("streaming", [False, True])
async def test_ollama_sdk_wire_contract(streaming):
    recorded = []

    async def respond(request):
        body = json.loads(request.content)
        recorded.append(body)
        assert body["max_tokens"] == 137
        assert "max_completion_tokens" not in body
        assert body["messages"][0]["reasoning"] == "prior reasoning"
        assert "reasoning_content" not in body["messages"][0]
        assert body["reasoning_effort"] == "high"
        message = {"role": "assistant", "content": "OK", "reasoning": "current reasoning"}
        common = {"id": "test", "model": "fixture", "created": 1}
        if streaming:
            data = {**common, "object": "chat.completion.chunk", "choices": [
                {"index": 0, "delta": message, "finish_reason": "stop"},
            ]}
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  text=f"data: {json.dumps(data)}\n\ndata: [DONE]\n\n")
        return httpx.Response(200, json={**common, "object": "chat.completion", "choices": [
            {"index": 0, "message": message, "finish_reason": "stop"},
        ]})

    client = OpenAIClient(model="fixture", api_key="fixture", chat_dialect="ollama",
                          max_completion_tokens=256, extra_body={"reasoning_effort": "high"})
    await client._client.close()
    client._client = AsyncOpenAI(api_key="fixture", base_url="https://fixture.invalid/v1",
                                http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    history = [{"role": "assistant", "content": "prior", "reasoning_content": "prior reasoning"}]
    try:
        if streaming:
            result = [delta async for delta in client.stream(history, max_tokens=137)]
            assert "".join(delta.content for delta in result) == "OK"
            assert "".join(delta.reasoning_content for delta in result) == "current reasoning"
        else:
            result = await client.chat(history, max_tokens=137)
            assert result.content == "OK"
            assert result.reasoning_content == "current reasoning"
        assert len(recorded) == 1
        assert history[0]["reasoning_content"] == "prior reasoning"
    finally:
        await client._client.close()


@pytest.mark.parametrize("workflow", ["stateful_react_agent", "agent_team"])
def test_profile_preserves_capabilities_and_bounds_context(workflow):
    source = yaml.safe_load((ROOT / "workflows" / workflow / "profiles/tui.yaml").read_text())
    model = {"protocol": "chat_completions", "model": "qwen3.8:27b", "chat_dialect": "ollama",
             "context_window": 32768, "max_output_tokens": 4096,
             "extra_body": {"reasoning_effort": "high"}, "thinking_format": "reasoning_content"}
    result = workflow_settings(source, model)
    for key in ["agent_tools", "main_agent_tools", "sub_agent_tools", "task_board",
                "context_compaction", "compaction_spill", "planning_mode"]:
        assert result["agent"].get(key) == source["agent"].get(key)
    assert result["agent"]["max_input_tokens"] + 4096 == 32768
    assert result["llm"]["extra_body"] == {"reasoning_effort": "high"}
    assert "chat_template_kwargs" in source["llm"]["extra_body"]  # source was not mutated
    assert result["llm"]["chat_dialect"] == "ollama"
    assert result["llm"]["temperature"] == source["llm"]["temperature"]
    if "report_llm" in source:
        assert result["report_llm"]["temperature"] == source["report_llm"]["temperature"]


def test_generated_profiles_contain_no_credentials_and_load_in_both_workflows(tmp_path, monkeypatch):
    # configure() mutates only the current process environment. Restore it after this test.
    import os
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.delenv("SWITCH_TEST_KEY", raising=False)
    (tmp_path / ".env").write_text("SWITCH_TEST_KEY=test-secret-never-persist\n")
    path = tmp_path / "agent.toml"
    path.write_text('''default_profile = "test"
[profiles.test]
protocol = "chat_completions"
model = "fixture"
base_url = "http://127.0.0.1:12345/v1"
api_key_env = "SWITCH_TEST_KEY"
chat_dialect = "ollama"
''')
    assert configure(path) == "test"
    assert os.environ["SWITCH_RAW_WEB_FETCH"] == "1"
    assert os.environ["FRONTIER_AGENT_LLM_MAX_CONCURRENT"] == "1"
    assert os.environ["FRONTIER_AGENT_LLM_FIRST_CHUNK_S"] == "600"
    assert os.environ["FRONTIER_AGENT_LLM_STREAM_STALL_S"] == "600"
    from workflows.agent_team.profile import load_swarm_profile
    from workflows.stateful_react_agent.profile import load_react_profile
    for loader, env in [(load_react_profile, "SWITCH_REACT_PROFILE"),
                        (load_swarm_profile, "SWITCH_TEAM_PROFILE")]:
        file = Path(os.environ[env] + ".yaml")
        assert "test-secret-never-persist" not in file.read_text()
        settings = loader(os.environ[env])
        assert settings["llm"]["api_key"] == "test-secret-never-persist"
        assert settings["llm"]["model"] == "fixture"


def test_responses_profile_does_not_receive_qwen_template_options():
    source = yaml.safe_load((ROOT / "workflows/agent_team/profiles/tui.yaml").read_text())
    result = workflow_settings(source, {"protocol": "responses", "reasoning": {"effort": "high"}})
    assert result["agent"]["thinking_format"] == "content_block"
    assert result["llm"]["reasoning"] == {"effort": "high"}
    assert result["llm"]["extra_body"] == {}
    assert result["report_llm"] == result["llm"]
