"""A normal model stop is not a successful command unless a certificate was published."""
import asyncio
import io
from types import SimpleNamespace

import pytest
from rich.console import Console

from switrs_agent import cli

QUESTION = "How many collisions?"
DONE = {"kind": "done", "turns": 4, "cost_usd": 0.125, "session_id": "fixture", "error": None}


def call(id):
    return {"kind": "tool_call", "id": id, "tool": "submit_answer", "input": {"turn_id": "t1", "claims": []}}


def result(id, *, ok=True, answer="There were 3 reported collisions."):
    return {"kind": "tool_result", "id": id, "tool": "submit_answer", "is_error": False,
            "result": {"ok": ok, "certified": ok, "final_answer": answer, "question": QUESTION, "claims_checked": 0}}


class Agent:
    name = "fixture"
    session_id = "fixture"

    def __init__(self, events):
        self.events = events

    async def ask(self, question):
        assert question == QUESTION
        for event in self.events:
            yield event
        yield dict(DONE)

    async def close(self):
        pass


@pytest.fixture
def console(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=output, force_terminal=False, width=120))
    return output


@pytest.mark.parametrize("events,status", [
    ([], 1),
    ([{"kind": "text", "text": "Unverified model answer."}], 1),
    ([call("one"), result("one", ok=False)], 1),
    ([call("one"), result("one"), call("two")], 1),
    ([call("one"), result("one"), call("two"), result("two", ok=False)], 1),
    ([call("one"), result("one")], 0),
    ([call("one"), result("one", answer="This question cannot be answered from the recorded fields.")], 0),
], ids=["empty-normal-stop", "unverified-normal-stop", "failed-submission", "pending-revision", "failed-revision",
        "certified-answer", "certified-limitation"])
def test_ask_exit_status_requires_a_published_certificate(monkeypatch, console, events, status):
    monkeypatch.setattr(cli, "_agent", lambda args: Agent(events))
    actual = asyncio.run(cli.cmd_ask(SimpleNamespace(question=QUESTION, verbose=False)))
    assert actual == status, console.getvalue()


@pytest.mark.parametrize("published", [False, True])
def test_run_turn_preserves_done_metadata_and_exposes_publication(console, published):
    events = [call("one"), result("one")] if published else []
    summary = asyncio.run(cli.run_turn(Agent(events), QUESTION, False))
    assert summary["published"] is published
    assert {key: summary[key] for key in DONE} == DONE
