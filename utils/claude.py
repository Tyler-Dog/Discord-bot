"""Minimal async client for the Anthropic Messages API.

Supports plain completions, forced tool output (for structured data), server-sent-event
streaming and an agentic tool-use loop. Only depends on ``aiohttp``.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import aiohttp

logger = logging.getLogger("discord_bot")

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-4-5"

OnText = Callable[[str], Awaitable[None]]
ToolExecutor = Callable[[str, dict], Awaitable[str]]


class ClaudeError(RuntimeError):
    """Raised for API / transport failures with a user-presentable message."""


@dataclass
class RunResult:
    text: str
    tools_used: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    rounds: int = 1


# ── SSE event reduction (pure, unit-tested) ──────────────────────────────────

def new_stream_state() -> dict[str, Any]:
    return {"blocks": {}, "stop_reason": None, "input_tokens": 0, "output_tokens": 0}


def apply_event(state: dict[str, Any], event: dict[str, Any]) -> str:
    """Fold one decoded SSE ``data:`` payload into ``state``. Returns any new text delta."""
    kind = event.get("type")
    if kind == "message_start":
        usage = event.get("message", {}).get("usage", {})
        state["input_tokens"] = usage.get("input_tokens", 0)
    elif kind == "content_block_start":
        block = dict(event["content_block"])
        if block["type"] == "text":
            block["text"] = block.get("text", "")
        elif block["type"] == "tool_use":
            block["_json"] = ""
        state["blocks"][event["index"]] = block
    elif kind == "content_block_delta":
        block = state["blocks"].get(event["index"])
        delta = event["delta"]
        if block is None:
            return ""
        if delta["type"] == "text_delta":
            block["text"] += delta["text"]
            return delta["text"]
        if delta["type"] == "input_json_delta":
            block["_json"] += delta.get("partial_json", "")
    elif kind == "message_delta":
        state["stop_reason"] = event.get("delta", {}).get("stop_reason") or state["stop_reason"]
        state["output_tokens"] = event.get("usage", {}).get("output_tokens", state["output_tokens"])
    elif kind == "error":
        raise ClaudeError(event.get("error", {}).get("message", "stream error"))
    return ""


def finalize_state(state: dict[str, Any]) -> dict[str, Any]:
    """Turn accumulated blocks into a clean assistant message (safe to send back to the API)."""
    content: list[dict[str, Any]] = []
    for index in sorted(state["blocks"]):
        block = state["blocks"][index]
        if block["type"] == "text":
            if block["text"]:
                content.append({"type": "text", "text": block["text"]})
        elif block["type"] == "tool_use":
            raw = block.get("_json", "")
            try:
                tool_input = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                tool_input = {}
            content.append({"type": "tool_use", "id": block["id"], "name": block["name"], "input": tool_input})
    return {
        "content": content,
        "stop_reason": state["stop_reason"],
        "usage": {"input_tokens": state["input_tokens"], "output_tokens": state["output_tokens"]},
    }


def message_text(message: dict[str, Any]) -> str:
    return "".join(b.get("text", "") for b in message.get("content", []) if b.get("type") == "text")


# ── Client ───────────────────────────────────────────────────────────────────

class ClaudeClient:
    def __init__(
        self, api_key: str | None, model: str = DEFAULT_MODEL, *, api_url: str = API_URL
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.api_url = api_url
        self._session: aiohttp.ClientSession | None = None

    @classmethod
    def from_env(cls) -> "ClaudeClient":
        return cls(os.getenv("ANTHROPIC_API_KEY"), os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL))

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key or "",
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120))
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _payload(self, *, messages, system, tools, tool_choice, max_tokens, stream) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice
        if stream:
            payload["stream"] = True
        return payload

    @staticmethod
    async def _raise_for_status(resp: aiohttp.ClientResponse) -> None:
        if resp.status == 200:
            return
        try:
            data = await resp.json()
            message = data.get("error", {}).get("message", f"HTTP {resp.status}")
        except Exception:
            message = f"HTTP {resp.status}"
        raise ClaudeError(message)

    async def create(
        self, *, messages: list[dict], system: str | None = None, tools: list[dict] | None = None,
        tool_choice: dict | None = None, max_tokens: int = 1024,
    ) -> dict[str, Any]:
        """Single non-streaming request. Returns the raw API message."""
        payload = self._payload(messages=messages, system=system, tools=tools,
                                tool_choice=tool_choice, max_tokens=max_tokens, stream=False)
        session = await self._get_session()
        try:
            async with session.post(self.api_url, json=payload, headers=self._headers()) as resp:
                await self._raise_for_status(resp)
                return await resp.json()
        except aiohttp.ClientError as exc:
            raise ClaudeError(f"network error: {exc}") from exc

    async def stream(
        self, *, messages: list[dict], system: str | None = None, tools: list[dict] | None = None,
        max_tokens: int = 1024, on_text: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Streaming request. Calls ``on_text(delta)`` as text arrives; returns the final message."""
        payload = self._payload(messages=messages, system=system, tools=tools,
                                tool_choice=None, max_tokens=max_tokens, stream=True)
        state = new_stream_state()
        session = await self._get_session()
        try:
            async with session.post(self.api_url, json=payload, headers=self._headers()) as resp:
                await self._raise_for_status(resp)
                async for raw in resp.content:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    body = line[5:].strip()
                    if not body or body == "[DONE]":
                        continue
                    delta = apply_event(state, json.loads(body))
                    if delta and on_text:
                        await on_text(delta)
        except aiohttp.ClientError as exc:
            raise ClaudeError(f"network error: {exc}") from exc
        return finalize_state(state)

    async def run(
        self, *, messages: list[dict], system: str | None = None, tools: list[dict] | None = None,
        execute: ToolExecutor | None = None, max_rounds: int = 5, max_tokens: int = 1024,
        on_text: OnText | None = None,
    ) -> RunResult:
        """Stream a reply, executing tool calls until Claude produces a final answer.

        ``on_text`` receives the *full text so far* (across tool rounds) after every delta.
        """
        convo = list(messages)
        result = RunResult(text="")
        finished_text: list[str] = []

        for round_no in range(1, max_rounds + 1):
            current = ""

            async def relay(delta: str) -> None:
                nonlocal current
                current += delta
                if on_text:
                    await on_text("\n\n".join([*finished_text, current]))

            msg = await self.stream(messages=convo, system=system, tools=tools,
                                    max_tokens=max_tokens, on_text=relay)
            result.input_tokens += msg["usage"]["input_tokens"]
            result.output_tokens += msg["usage"]["output_tokens"]
            result.rounds = round_no
            if current.strip():
                finished_text.append(current.strip())

            calls = [b for b in msg["content"] if b["type"] == "tool_use"]
            if msg["stop_reason"] != "tool_use" or not calls or execute is None:
                break

            convo.append({"role": "assistant", "content": msg["content"]})
            tool_results = []
            for call in calls:
                result.tools_used.append(call["name"])
                try:
                    output, is_error = await execute(call["name"], call["input"]), False
                except Exception as exc:  # tool failures are reported to the model, not raised
                    logger.warning("Tool %s failed: %s", call["name"], exc)
                    output, is_error = f"Error: {exc}", True
                tool_results.append({
                    "type": "tool_result", "tool_use_id": call["id"],
                    "content": output, "is_error": is_error,
                })
            convo.append({"role": "user", "content": tool_results})
        else:
            finished_text.append("*(stopped after reaching the tool-call limit)*")

        result.text = "\n\n".join(finished_text) or "(no response)"
        return result
