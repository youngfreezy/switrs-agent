"""The SWITRS agent: the MCP server's tools and instructions, driven by Claude from your terminal.

The tool set and the system prompt are not defined here. They come from `mcp_server.build()`, so
this file follows whatever the server exposes (get_context, begin_question, run_sql,
submit_answer, ...) without copies drifting apart.

Backends
  api  Our own tool loop on the Claude Messages API (anthropic SDK). Needs ANTHROPIC_API_KEY.
       Tools run in this process through an in-memory MCP client.
  sdk  The Claude Agent SDK, which uses the Claude Code login on this machine (a Claude
       subscription works). Every built-in Claude Code tool is off and user settings, hooks and
       CLAUDE.md are ignored. The only tools are the SWITRS server's, run as a stdio subprocess.

Both backends yield the same event dicts:
  {"kind": "tool_call", "id", "tool", "input"}
  {"kind": "tool_result", "id", "tool", "result", "is_error"}
  {"kind": "text", "text"}
  {"kind": "done", "turns", "cost_usd", "session_id", "error"}
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import shutil
import sys
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack

from mcp import Client

from . import mcp_server
from .tools import Toolkit
from .request_binding import write_request

MODEL = os.environ.get("SWITRS_MODEL", "claude-opus-5-5")
logging.getLogger("claude_agent_sdk").setLevel(logging.WARNING)


def _parse(text: str) -> object:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


def _result_text(content) -> str:
    """Join the text of MCP or Agent SDK tool-result content."""
    if isinstance(content, str):
        return content
    parts = []
    for c in content or []:
        text = c.get("text") if isinstance(c, dict) else getattr(c, "text", None)
        if text:
            parts.append(text)
    return "".join(parts)


async def server_spec() -> tuple[str, list[dict]]:
    """The server's instructions and its tools as Messages API tool definitions."""
    async with Client(mcp_server.build(Toolkit(session_id="spec-probe", ))) as c:
        listed = await c.list_tools()
        tools = [{"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
                 for t in listed.tools]
        return c.instructions or "", tools


# ---- backend: Claude Messages API, our own loop ---------------------------------------------

class ApiAgent:
    """Manual tool loop on the Messages API. Keeps the conversation for follow-up questions."""

    name = "api"

    def __init__(self, session_id: str, model: str = MODEL, max_turns: int = 30):
        import anthropic

        self.client = anthropic.AsyncAnthropic()
        self.session_id, self.model, self.max_turns = session_id, model, max_turns
        self.messages: list = []
        self._stack: AsyncExitStack | None = None
        self.mcp: Client | None = None

    async def _connect(self) -> None:
        self._stack = AsyncExitStack()
        self.toolkit = Toolkit(session_id=self.session_id)
        self.mcp = await self._stack.enter_async_context(Client(mcp_server.build(self.toolkit)))
        listed = await self.mcp.list_tools()
        self.system = self.mcp.instructions or ""
        self.tools = [{"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
                      for t in listed.tools]

    def _done(self, turn: int, error: str | None) -> dict:
        return {"kind": "done", "turns": turn, "cost_usd": None, "session_id": self.session_id, "error": error}

    async def ask(self, question: str) -> AsyncIterator[dict]:
        import anthropic

        if self.mcp is None:
            await self._connect()
        self.toolkit.bind_question(question)
        self.messages.append({"role": "user", "content": question})
        for turn in range(1, self.max_turns + 1):
            try:
                resp = await self.client.messages.create(
                    model=self.model,
                    max_tokens=16000,
                    system=self.system,
                    tools=self.tools,
                    messages=self.messages,
                    output_config={"effort": "high"},
                    cache_control={"type": "ephemeral"},
                )
            except anthropic.APIStatusError as e:
                yield self._done(turn, f"API error {e.status_code}: {e.message}")
                return
            except anthropic.APIConnectionError as e:
                yield self._done(turn, f"network error: {e}")
                return
            # Append the full content (thinking blocks included) so the next request stays valid.
            self.messages.append({"role": "assistant", "content": resp.content})
            for block in resp.content:
                if block.type == "text" and block.text.strip():
                    yield {"kind": "text", "text": block.text}
            if resp.stop_reason == "refusal":
                yield self._done(turn, "the model declined this request")
                return
            if resp.stop_reason != "tool_use":
                yield self._done(turn, None if resp.stop_reason == "end_turn" else f"stopped: {resp.stop_reason}")
                return
            results = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                yield {"kind": "tool_call", "id": block.id, "tool": block.name, "input": block.input}
                res = await self.mcp.call_tool(block.name, block.input)
                text = _result_text(res.content)
                yield {"kind": "tool_result", "id": block.id, "tool": block.name, "result": _parse(text),
                       "is_error": bool(res.is_error)}
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": text,
                                "is_error": bool(res.is_error)})
            # All results for one assistant turn go back in a single user message.
            self.messages.append({"role": "user", "content": results})
        yield self._done(self.max_turns, f"stopped after {self.max_turns} model turns")

    async def close(self) -> None:
        if self._stack:
            await self._stack.aclose()
        await self.client.close()


# ---- backend: Claude Agent SDK, Claude Code login --------------------------------------------

class SdkAgent:
    """Claude Agent SDK with built-in tools off and only the SWITRS MCP server attached."""

    name = "sdk"

    def __init__(self, session_id: str, model: str = MODEL, max_turns: int = 30, budget_usd: float = 3.0):
        self.session_id, self.model, self.max_turns, self.budget_usd = session_id, model, max_turns, budget_usd
        self.client = None
        self.workdir = None
        self._names: dict[str, str] = {}

    async def _connect(self) -> None:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        system, tools = await server_spec()
        self.workdir = tempfile.TemporaryDirectory(prefix="switrs-agent-")
        self.request_path = pathlib.Path(self.workdir.name) / "request.json"
        server = {"type": "stdio", "command": sys.executable, "args": ["-m", "switrs_agent.mcp_server"],
                  "env": {"SWITRS_SESSION": self.session_id, "SWITRS_REQUEST_FILE": str(self.request_path)}}
        options = ClaudeAgentOptions(
            system_prompt=system,
            tools=[],                      # no Bash, Read, Edit, WebSearch...
            mcp_servers={"switrs": server},
            strict_mcp_config=True,        # ignore other MCP servers configured on this machine
            allowed_tools=[f"mcp__switrs__{t['name']}" for t in tools],
            setting_sources=[],            # ignore user and project settings, hooks and CLAUDE.md
            model=self.model,
            max_turns=self.max_turns,
            max_budget_usd=self.budget_usd,
            cwd=self.workdir.name,
            # Use the `claude` on PATH, which holds this machine's login. The SDK's bundled copy
            # does not share it. SWITRS_CLAUDE_CLI overrides.
            cli_path=os.environ.get("SWITRS_CLAUDE_CLI") or shutil.which("claude"),
        )
        self.client = ClaudeSDKClient(options=options)
        await self.client.connect()

    async def ask(self, question: str) -> AsyncIterator[dict]:
        from claude_agent_sdk import (AssistantMessage, ResultMessage, TextBlock, ToolResultBlock,
                                      ToolUseBlock, UserMessage)

        if self.client is None:
            await self._connect()
        write_request(self.request_path, question)
        await self.client.query(question)
        async for msg in self.client.receive_response():
            if isinstance(msg, AssistantMessage):
                for b in msg.content:
                    if isinstance(b, TextBlock) and b.text.strip():
                        yield {"kind": "text", "text": b.text}
                    elif isinstance(b, ToolUseBlock):
                        tool = b.name.removeprefix("mcp__switrs__")
                        self._names[b.id] = tool
                        yield {"kind": "tool_call", "id": b.id, "tool": tool, "input": b.input}
            elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
                for b in msg.content:
                    if isinstance(b, ToolResultBlock):
                        yield {"kind": "tool_result", "id": b.tool_use_id, "tool": self._names.get(b.tool_use_id),
                               "result": _parse(_result_text(b.content)), "is_error": bool(b.is_error)}
            elif isinstance(msg, ResultMessage):
                yield {"kind": "done", "turns": msg.num_turns, "cost_usd": msg.total_cost_usd,
                       "session_id": self.session_id,
                       "error": (msg.result or msg.subtype) if msg.is_error else None}

    async def close(self) -> None:
        try:
            if self.client is not None:
                await self.client.disconnect()
        finally:
            if self.workdir is not None:
                self.workdir.cleanup()


def make_agent(backend: str = "auto", model: str = MODEL, session_id: str | None = None):
    """Pick a backend. `auto` uses the API when ANTHROPIC_API_KEY is set, else the Claude login."""
    session_id = session_id or "cli-" + time.strftime("%Y%m%d-%H%M%S")
    if backend == "auto":
        backend = "api" if os.environ.get("ANTHROPIC_API_KEY") else "sdk"
    if backend == "api":
        return ApiAgent(session_id, model)
    if backend == "sdk":
        return SdkAgent(session_id, model)
    raise ValueError(f"unknown backend {backend!r}; use api, sdk or auto")
