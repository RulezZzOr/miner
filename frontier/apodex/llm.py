# Modified for Miner / Switch Studio, 2026-09-23.
# Changes from ApodexAI/FrontierAgent; see frontier/SWITCH.md and THIRD_PARTY.md at the repository root.
"""Build terminal and compaction clients with the selected workflow's protocol."""

from __future__ import annotations

from typing import Any

from apodex.config import ModelConfig
from frontier_agent.core.llm import LLMClient


def build_llm(cfg: ModelConfig) -> LLMClient:
    """Construct a streaming-capable chat client from :class:`ModelConfig`."""
    from frontier_agent.infra.protocol_client import build_protocol_client

    client = build_protocol_client({
        **cfg.client_options, "protocol": cfg.protocol, "model": cfg.model,
        "api_key": cfg.api_key, "base_url": cfg.base_url, "max_tokens": cfg.max_tokens,
    }, title="Switch-Compaction")
    if client is not None:
        return client
    # Imported lazily so ``--help`` / config errors don't pull the LLM stack.
    from frontier_agent.infra.openai_client import OpenAIClient

    kwargs: dict[str, Any] = {
        "model": cfg.model,
        "temperature": cfg.temperature,
        # langchain ``max_tokens`` maps onto OpenAI ``max_completion_tokens``.
        "max_completion_tokens": cfg.max_tokens,
        "chat_dialect": cfg.chat_dialect,
    }
    # Neither is an ``OpenAIClient`` parameter, and neither is a
    # Chat-Completions field, so both ride ``extra_body`` — the same route the
    # workflow profile loaders use. Omitted entirely when unset, so a profile
    # that says nothing keeps the server's own defaults.
    extra_body: dict[str, Any] = dict(cfg.client_options.get("extra_body") or {})
    if cfg.top_p is not None:
        extra_body["top_p"] = cfg.top_p
    if cfg.top_k is not None:
        extra_body["top_k"] = cfg.top_k
    if extra_body:
        kwargs["extra_body"] = extra_body
    if cfg.api_key:
        kwargs["api_key"] = cfg.api_key
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url
    return OpenAIClient(**kwargs)
