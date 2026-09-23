"""Switch launcher: retain the complete upstream product, select our LLM profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def credential_for(model: dict[str, Any]) -> str:
    """Resolve runtime/probe auth without ever reading a key for no-auth profiles."""
    if model.get("auth", "env") == "none":
        return "ollama"
    if model.get("auth") == "oauth" and model.get("oauth_provider") == "claude_console":
        return "oauth-managed"
    key = os.environ.get(model.get("api_key_env", ""), "")
    if not key:
        raise ValueError(f"Missing credential variable: {model.get('api_key_env')}")
    return key


def workflow_settings(source: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    """Copy upstream workflow capabilities, overriding only model/runtime limits."""
    result = json.loads(json.dumps(source))
    protocol = model["protocol"]
    if protocol not in {"chat_completions", "responses", "anthropic", "bedrock"}:
        raise ValueError(f"Full workflow does not support protocol {protocol!r}")
    if model.get("tool_mode", "native") != "native":
        raise ValueError("Full workflows currently require native tool calling")
    context = int(model.get("context_window", 32768))
    output = int(model.get("max_output_tokens", 4096))
    if context < 2048 or output < 128 or output + 1024 >= context:
        raise ValueError("Invalid context/output token limits")
    connection = {
        "provider": "${OPENAI_PROVIDER}",
        "model": "${OPENAI_MODEL}",
        "base_url": "${OPENAI_BASE_URL}",
        "api_key": "${OPENAI_API_KEY}",
        "protocol": protocol,
        "chat_dialect": model.get("chat_dialect", "openai"),
        "max_tokens": "${OPENAI_MAX_TOKENS}",
        "extra_body": model.get("extra_body", {}),
    }
    if model.get("temperature") is not None:
        connection["temperature"] = model["temperature"]
    if model.get("oauth_provider") == "claude_console":
        connection["auth_profile"] = model["auth_profile"]
    # Preserve native provider configuration supported by the upstream builders.
    if protocol == "responses":
        connection["reasoning"] = model.get("reasoning", {})
    if protocol in {"anthropic", "bedrock"}:
        thinking = model.get("thinking", {})
        connection["thinking_type"] = thinking.get("type", "adaptive")
        if "budget_tokens" in thinking:
            connection["thinking_budget_tokens"] = thinking["budget_tokens"]
        connection["effort"] = model.get("effort", "")
    for name in ("llm", "report_llm"):
        if name in result:
            result[name] = dict(connection)
            if (
                protocol == "chat_completions"
                and "temperature" not in connection
                and "temperature" in source[name]
            ):
                # Preserve upstream sampling defaults (main=1, reporter=0).
                # Omitting this would hit the builders' implicit temperature=0.
                result[name]["temperature"] = source[name]["temperature"]
    agent = result.setdefault("agent", {})
    agent.update({
        "max_len": context,
        "max_input_tokens": context - output,
        "thinking_format": (
            "content_block" if protocol != "chat_completions"
            else model.get("thinking_format", "none")
        ),
        "thinking_in_history": model.get("thinking_in_history", False),
        "llm_timeout_s": 600,
        "reasoning_only_timeout_s": 600,
        "reasoning_only_max_tokens": max(64, output * 3 // 4),
    })
    return result


def configure(path: Path, name: str | None = None) -> str:
    """Resolve credentials in-process; generated YAML contains placeholders only."""
    load_dotenv(path.resolve().parent / ".env", override=False)
    load_dotenv(ROOT / ".env", override=False)
    with path.open("rb") as stream:
        settings = tomllib.load(stream)
    name = str(name or settings["default_profile"])
    model = settings["profiles"][name]
    key = credential_for(model)
    os.environ.pop("SWITCH_ANTHROPIC_PROFILE", None)
    protocol = model["protocol"]
    provider = {"anthropic": "anthropic", "bedrock": "bedrock"}.get(protocol, "local")
    default_url = {"anthropic": "https://api.anthropic.com", "responses": "https://api.openai.com/v1"}
    base_url = model.get("base_url") or default_url.get(protocol, "https://api.openai.com/v1")
    # These are process-local settings. No global shell/user configuration changes.
    os.environ.update({
        "OPENAI_PROVIDER": provider,
        "OPENAI_MODEL": model["model"],
        "OPENAI_API_KEY": key,
        "OPENAI_BASE_URL": base_url,
        "LOCAL_BASE_URL": base_url,
        "LOCAL_API_KEY": key,
        "OPENAI_CONTEXT_WINDOW": str(model.get("context_window", 32768)),
        "OPENAI_MAX_TOKENS": str(model.get("max_output_tokens", 4096)),
        # Web tools otherwise use a separately cached/default extraction model,
        # which may send pages to an unselected cloud endpoint. The selected
        # worker can read and extract the bounded page text itself.
        "SWITCH_RAW_WEB_FETCH": "1",
    })
    if model.get("chat_dialect") == "ollama":
        # Local Ollama may have one decode slot and slow prompt prefill.
        # Queue here rather than repeatedly timing out inside the server.
        os.environ.setdefault("FRONTIER_AGENT_LLM_MAX_CONCURRENT", "1")
        os.environ.setdefault("FRONTIER_AGENT_LLM_FIRST_CHUNK_S", "600")
        os.environ.setdefault("FRONTIER_AGENT_LLM_STREAM_STALL_S", "600")
    digest = hashlib.sha256(json.dumps(model, sort_keys=True).encode()).hexdigest()[:20]
    cache = path.resolve().parent / ".switch-agent" / "frontier-profiles" / digest
    cache.mkdir(parents=True, exist_ok=True)
    for mode, folder, variable in (
        ("react", "stateful_react_agent", "SWITCH_REACT_PROFILE"),
        ("agent_team", "agent_team", "SWITCH_TEAM_PROFILE"),
    ):
        source = yaml.safe_load((ROOT / "workflows" / folder / "profiles" / "tui.yaml").read_text())
        resolved = workflow_settings(source, model)
        target = cache / f"{mode}.yaml"
        with tempfile.NamedTemporaryFile(mode="w", dir=cache, delete=False) as stream:
            stream.write(yaml.safe_dump(resolved, sort_keys=False))
            temporary = Path(stream.name)
        temporary.replace(target)
        os.environ[variable] = str(target.with_suffix(""))
    return name


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--llm-profile")
    parser.add_argument("--config", type=Path, default=ROOT.parent / "agent.toml")
    args, remaining = parser.parse_known_args()
    if any(arg in remaining for arg in ("--help", "-h")):
        from apodex.cli import build_parser
        help_parser = build_parser()
        help_parser.prog = "switch"
        help_parser.description = "Switch — full local fork of FrontierAgent."
        help_parser.add_argument("--llm-profile", help="model profile from agent.toml")
        help_parser.add_argument("--config", help="path to model configuration TOML")
        help_parser.print_help()
        return
    if not any(arg in remaining for arg in ("--help", "-h", "--version")):
        try:
            configure(args.config, args.llm_profile)
        except (KeyError, ValueError, OSError) as exc:
            raise SystemExit(f"Switch configuration error: {exc}") from exc
    sys.argv = ["switch", *remaining]
    from apodex.cli import main as upstream_main
    raise SystemExit(upstream_main())


if __name__ == "__main__":
    main()
