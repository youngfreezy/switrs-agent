"""Chat storage behind a small interface, so a memory layer can replace the JSON file later.

A store keeps chats (title, Claude session id) and their turns (user message, streamed events).
Long-term memory (user preferences, prior findings) would be a second store that the agent can
read but never cite as evidence: every number in an answer must still come from a query.
"""

from __future__ import annotations

import json
import pathlib
import threading
import time
from typing import Protocol


class ChatStore(Protocol):
    def list(self) -> list[dict]: ...
    def get(self, chat_id: str) -> dict | None: ...
    def create(self, chat_id: str, title: str) -> dict: ...
    def add_turn(self, chat: dict, message: str, events: list[dict], seconds: int) -> None: ...


class JsonChatStore:
    def __init__(self, path: pathlib.Path):
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("{}")

    def _load(self) -> dict:
        return json.loads(self.path.read_text() or "{}")

    def _save(self, data: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, default=str))
        tmp.replace(self.path)

    def list(self) -> list[dict]:
        data = self._load()
        rows = [{"id": c["id"], "title": c["title"], "updated": c["updated"], "turns": len(c["turns"])} for c in data.values()]
        return sorted(rows, key=lambda r: r["updated"], reverse=True)

    def get(self, chat_id: str) -> dict | None:
        return self._load().get(chat_id)

    def create(self, chat_id: str, title: str) -> dict:
        chat = {"id": chat_id, "title": title, "created": time.time(), "updated": time.time(),
                "claude_session_id": None, "turns": []}
        with self._lock:
            data = self._load()
            data[chat_id] = chat
            self._save(data)
        return chat

    def add_turn(self, chat: dict, message: str, events: list[dict], seconds: int) -> None:
        done = next((e for e in reversed(events) if e["kind"] == "done"), {})
        with self._lock:
            data = self._load()
            rec = data.get(chat["id"], chat)
            rec["claude_session_id"] = chat.get("claude_session_id") or rec.get("claude_session_id")
            rec["turns"].append({"user": message, "events": events, "seconds": seconds,
                                 "cost_usd": done.get("cost_usd"), "at": time.time()})
            rec["updated"] = time.time()
            data[chat["id"]] = rec
            self._save(data)
