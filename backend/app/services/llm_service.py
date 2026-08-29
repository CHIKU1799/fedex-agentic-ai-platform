"""
Provider-agnostic LLM layer for the agentic planner.

Two interchangeable providers speak one normalized interface, so the
planner's tool-calling loop is written once and survives a provider
outage:

- Anthropic (Claude): the primary brain when credits are available.
- Groq (Llama 3.3 70B via the OpenAI-compatible API): free-tier fallback
  with real tool calling, so the platform stays fully agentic even when
  the Anthropic account is out of credits.

A provider that fails with a billing/credential error is demoted for the
lifetime of the process (`ProviderBillingError`), so the very next turn
goes straight to the fallback provider instead of burning a failed API
call every request.
"""
import json
from dataclasses import dataclass, field
from typing import Optional

import httpx

from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger()

_client = None
_demoted = set()  # provider names disabled for this process (billing/credentials)

_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

_BILLING_MARKERS = ("credit", "billing", "quota", "insufficient", "invalid api key", "authentication")


class ProviderBillingError(Exception):
    """The provider cannot serve requests at all (credits/credentials)."""


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class ChatTurn:
    """One normalized model response inside the agentic loop."""
    text: str = ""
    tool_calls: list = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw: object = None


def get_llm_client():
    """Lazily construct a shared Anthropic client, or None if unconfigured.

    Returning None (rather than raising) is what lets the whole platform
    degrade gracefully to the offline/keyword path when no key is present.
    """
    global _client
    if _client is None:
        api_key = settings.ANTHROPIC_API_KEY
        if not api_key:
            return None
        from anthropic import Anthropic
        _client = Anthropic(api_key=api_key)
    return _client


# Backwards-compatible aliases.
get_openai_client = get_llm_client
_get_client = get_llm_client


def _looks_like_billing(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status in (401, 402, 403):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _BILLING_MARKERS)


def demote(provider_name: str) -> None:
    logger.warning(f"LLM provider '{provider_name}' demoted for this process (billing/credentials).")
    _demoted.add(provider_name)


def is_demoted(provider_name: str) -> bool:
    return provider_name in _demoted


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, client):
        self._client = client

    def chat(self, system: str, messages: list, tools: list) -> ChatTurn:
        try:
            response = self._client.messages.create(
                model=settings.ANTHROPIC_MODEL,
                max_tokens=settings.ANTHROPIC_MAX_TOKENS,
                system=system,
                messages=messages,
                tools=tools,
            )
        except Exception as exc:
            if _looks_like_billing(exc):
                raise ProviderBillingError(str(exc)) from exc
            raise
        turn = ChatTurn(raw=response)
        usage = getattr(response, "usage", None)
        if usage is not None:
            turn.prompt_tokens = (
                (getattr(usage, "input_tokens", 0) or 0)
                + (getattr(usage, "cache_read_input_tokens", 0) or 0)
                + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
            )
            turn.completion_tokens = getattr(usage, "output_tokens", 0) or 0
        for block in response.content:
            block_type = getattr(block, "type", "")
            if block_type == "text":
                turn.text += block.text
            elif block_type == "tool_use":
                turn.tool_calls.append(ToolCall(id=block.id, name=block.name, args=dict(block.input or {})))
        return turn

    def assistant_messages(self, turn: ChatTurn) -> list:
        # Record the assistant turn (with its tool-use blocks) verbatim.
        return [{"role": "assistant", "content": turn.raw.content}]

    def tool_result_messages(self, results: list) -> list:
        # All tool results for the turn go back in a single user message.
        return [{
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": r["id"],
                    "content": r["content"],
                    "is_error": r["is_error"],
                }
                for r in results
            ],
        }]


def _groq_post(payload: dict) -> dict:
    """Single HTTP boundary to Groq — tests stub this function."""
    response = httpx.post(
        _GROQ_URL,
        json=payload,
        headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
        timeout=60.0,
    )
    if response.status_code in (401, 402, 403):
        raise ProviderBillingError(f"Groq rejected the request: {response.status_code} {response.text[:200]}")
    response.raise_for_status()
    return response.json()


def _to_openai_tools(tools: list) -> list:
    """Translate Anthropic tool schemas to the OpenAI/Groq function format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


class GroqProvider:
    name = "groq"

    def chat(self, system: str, messages: list, tools: list) -> ChatTurn:
        payload = {
            "model": settings.GROQ_MODEL,
            "max_tokens": settings.ANTHROPIC_MAX_TOKENS,
            "messages": [{"role": "system", "content": system}] + messages,
        }
        if tools:
            payload["tools"] = _to_openai_tools(tools)
            payload["tool_choice"] = "auto"
        data = _groq_post(payload)
        message = data["choices"][0]["message"]
        turn = ChatTurn(text=message.get("content") or "", raw=message)
        usage = data.get("usage") or {}
        turn.prompt_tokens = usage.get("prompt_tokens", 0) or 0
        turn.completion_tokens = usage.get("completion_tokens", 0) or 0
        for call in message.get("tool_calls") or []:
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except (ValueError, TypeError):
                args = {}
            turn.tool_calls.append(ToolCall(id=call["id"], name=call["function"]["name"], args=args))
        return turn

    def assistant_messages(self, turn: ChatTurn) -> list:
        message = {"role": "assistant", "content": turn.text or None}
        raw_calls = (turn.raw or {}).get("tool_calls") if isinstance(turn.raw, dict) else None
        if raw_calls:
            message["tool_calls"] = raw_calls
        return [message]

    def tool_result_messages(self, results: list) -> list:
        # OpenAI-style APIs expect one 'tool' role message per call.
        return [
            {"role": "tool", "tool_call_id": r["id"], "content": r["content"]}
            for r in results
        ]


def available_provider_names() -> list:
    """Which providers are configured (ignores runtime demotion) — for /health."""
    names = []
    if settings.ANTHROPIC_API_KEY and settings.LLM_PROVIDER in ("auto", "anthropic"):
        names.append("anthropic")
    if settings.GROQ_API_KEY and settings.LLM_PROVIDER in ("auto", "groq"):
        names.append("groq")
    return names


def generate_llm_response(query: str) -> str:
    """Plain single-shot completion (no tools) via whichever brain is alive."""
    client = get_llm_client()
    providers = []
    if client is not None and settings.LLM_PROVIDER in ("auto", "anthropic") and not is_demoted("anthropic"):
        providers.append(AnthropicProvider(client))
    if settings.GROQ_API_KEY and settings.LLM_PROVIDER in ("auto", "groq") and not is_demoted("groq"):
        providers.append(GroqProvider())
    if not providers:
        return "LLM Error: no LLM provider configured. Set ANTHROPIC_API_KEY or GROQ_API_KEY in .env."
    last_error = ""
    for provider in providers:
        try:
            turn = provider.chat("You are a FedEx assistant.", [{"role": "user", "content": query}], [])
            return turn.text
        except ProviderBillingError as exc:
            demote(provider.name)
            last_error = str(exc)
        except Exception as exc:
            last_error = str(exc)
    return f"LLM Error: {last_error}"
