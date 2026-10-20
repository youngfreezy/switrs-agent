"""Local chat UI for the SWITRS agent.

The harness is Claude Code in headless mode (`claude -p`) with only the switrs MCP tools.
Each chat keeps its Claude session id, so follow-up questions resume the same conversation.
Tool calls stream to the browser as they happen.

run:  uv run uvicorn ui.server:app --host 127.0.0.1 --port 8800
open: http://127.0.0.1:8800
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import secrets
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from switrs_agent.claude_auth import billing_status, claude_env
from switrs_agent.request_binding import write_request
from switrs_agent.publication import latest_certificate
from ui.store import JsonChatStore

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORK_CWD = pathlib.Path("/tmp/switrs-ui-cwd")  # empty working dir: the agent sees only the MCP tools
MODEL = os.environ.get("SWITRS_MODEL", "claude-opus-5-5")
BUDGET_USD = os.environ.get("SWITRS_TURN_BUDGET_USD", "1.50")
MAX_CONCURRENT = int(os.environ.get("SWITRS_MAX_CONCURRENT", "2"))

app = FastAPI(title="SWITRS agent")
store = JsonChatStore(ROOT / "outputs" / "ui" / "chats.json")
slots = asyncio.Semaphore(MAX_CONCURRENT)
chat_locks: dict[str, asyncio.Lock] = {}  # one turn at a time per chat, so a chat keeps one Claude session


class ChatIn(BaseModel):
    message: str
    chat_id: str | None = None


def mcp_config_for(chat_id: str, question: str) -> pathlib.Path:
    request_path = ROOT / "outputs" / "ui" / f"request-{chat_id}.json"
    write_request(request_path, question)
    cfg = {"mcpServers": {"switrs": {
        "command": "uv",
        "args": ["run", "--directory", str(ROOT), "python", "-m", "switrs_agent.mcp_server"],
        "env": {"SWITRS_SESSION": f"ui-{chat_id}", "SWITRS_REQUEST_FILE": str(request_path)},
    }}}
    path = ROOT / "outputs" / "ui" / f"mcp-{chat_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg))
    return path


def claude_cmd(chat: dict, message: str) -> list[str]:
    cmd = ["claude", "-p", "--model", MODEL, "--output-format", "stream-json", "--verbose",
           "--setting-sources", "project", "--tools", "", "--strict-mcp-config",
           "--mcp-config", str(mcp_config_for(chat["id"], message)), "--allowedTools", "mcp__switrs__*",
           "--max-budget-usd", BUDGET_USD]
    if chat.get("claude_session_id"):
        cmd += ["--resume", chat["claude_session_id"]]
    return cmd + [message]


def simplify(event: dict) -> list[dict]:
    """Turn claude stream-json events into UI events."""
    t = event.get("type")
    out = []
    if t == "system" and event.get("subtype") == "init":
        out.append({"kind": "session", "session_id": event.get("session_id")})
    elif t == "assistant":
        for b in event["message"]["content"]:
            if b.get("type") == "text" and b.get("text", "").strip():
                out.append({"kind": "text", "text": b["text"]})
            elif b.get("type") == "tool_use":
                out.append({"kind": "tool_call", "id": b["id"], "tool": b["name"].replace("mcp__switrs__", ""),
                            "input": b.get("input", {})})
    elif t == "user":
        for b in event["message"]["content"]:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                content = b.get("content")
                text = content if isinstance(content, str) else "".join(
                    c.get("text", "") for c in content or [] if isinstance(c, dict))
                try:
                    parsed = json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    parsed = text
                out.append({"kind": "tool_result", "id": b.get("tool_use_id"), "result": parsed,
                            "is_error": bool(b.get("is_error"))})
    elif t == "result":
        out.append({"kind": "done", "cost_usd": event.get("total_cost_usd"), "turns": event.get("num_turns"),
                    "error": event.get("is_error"), "result": event.get("result")})
    return out


def history_events(events: list[dict], question: str) -> list[dict]:
    """Rebuild display badges without deleting or rewriting stored history."""
    final = latest_certificate(events, question)
    published = any(e.get("kind") == "certified" and e.get("final_answer") == final
                    for e in events) if final is not None else False
    restored = []
    had_verdict = False
    for event in events:
        kind = event.get("kind")
        if kind == "certified":
            had_verdict = True
            if not published or event.get("final_answer") != final:
                text = event.get("final_answer")
                if isinstance(text, str) and text:
                    restored.append({"kind": "text", "text": "Unverified historical answer:\n\n" + text})
        elif kind == "uncertified":
            had_verdict = True
        else:
            restored.append(event)
    if had_verdict:
        restored.append({"kind": "certified", "final_answer": final} if published else {"kind": "uncertified"})
    return restored


@app.get("/")
def index():
    return FileResponse(ROOT / "ui" / "index.html")


@app.get("/api/status")
def status():
    b = billing_status()
    return {"billing_ok": b["ok"], "billing": b["detail"], "model": MODEL, "turn_budget_usd": BUDGET_USD}


@app.get("/api/chats")
def list_chats():
    return store.list()


@app.get("/api/chats/{chat_id}")
def get_chat(chat_id: str):
    chat = store.get(chat_id)
    if not chat:
        raise HTTPException(404)
    return {**chat, "turns": [{**turn, "events": history_events(turn.get("events", []), turn.get("user"))}
                              for turn in chat.get("turns", [])]}


@app.get("/api/chats/{chat_id}/trace")
def get_trace(chat_id: str):
    path = ROOT / "outputs" / "traces" / f"ui-{chat_id}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@app.post("/api/chat")
async def chat(body: ChatIn, request: Request):
    message = body.message.strip()
    if not message:
        raise HTTPException(400, "empty message")
    chat_rec = store.get(body.chat_id) if body.chat_id else None
    if not chat_rec:
        chat_rec = store.create(secrets.token_hex(4), title=message[:80])
    WORK_CWD.mkdir(exist_ok=True)

    billing = billing_status()
    lock = chat_locks.setdefault(chat_rec["id"], asyncio.Lock())

    async def stream():
        nonlocal chat_rec
        yield sse({"kind": "chat", "chat_id": chat_rec["id"]})
        if not billing["ok"]:
            yield sse({"kind": "done", "error": True, "result": billing["detail"]})
            return
        if lock.locked():
            yield sse({"kind": "status", "text": "Waiting for the previous message in this chat to finish..."})
        async with lock:
            chat_rec = store.get(chat_rec["id"]) or chat_rec  # pick up the session id the previous turn saved
            async for chunk in run_turn():
                yield chunk

    async def run_turn():
        if slots.locked():
            yield sse({"kind": "status", "text": "Waiting for a free slot..."})
        async with slots:
            proc = await asyncio.create_subprocess_exec(
                *claude_cmd(chat_rec, message), cwd=WORK_CWD, stdin=asyncio.subprocess.DEVNULL, env=claude_env(),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024)
            events, started = [], time.time()
            try:
                async for raw in proc.stdout:
                    if await request.is_disconnected():
                        proc.kill()
                        return
                    try:
                        ev = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    for ui_ev in simplify(ev):
                        if ui_ev["kind"] == "session" and not chat_rec.get("claude_session_id"):
                            chat_rec["claude_session_id"] = ui_ev["session_id"]
                        events.append(ui_ev)
                        yield sse(ui_ev)
                await proc.wait()
                certified = latest_certificate(events, message)
                ev = {"kind": "certified", "final_answer": certified} if certified else {"kind": "uncertified"}
                events.append(ev)
                yield sse(ev)
                if proc.returncode and not any(e["kind"] == "done" for e in events):
                    err = (await proc.stderr.read()).decode()[-800:]
                    ev = {"kind": "done", "error": True, "result": err or f"claude exited {proc.returncode}"}
                    events.append(ev)
                    yield sse(ev)
            finally:
                if proc.returncode is None:
                    proc.kill()
                store.add_turn(chat_rec, message, events, seconds=round(time.time() - started))

    return StreamingResponse(stream(), media_type="text/event-stream")


def sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, default=str)}\n\n"
