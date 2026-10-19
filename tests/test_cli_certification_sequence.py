"""CLI publication follows the latest submitted call, including interrupted revisions."""
import asyncio
import io

import pytest
from rich.console import Console

from switrs_agent import cli

QUESTION = "How many collisions?"
OLD = "Older certified answer."
NEW = "Latest certified answer."


def call(id):
    return {"kind": "tool_call", "id": id, "tool": "submit_answer", "input": {"claims": [], "turn_id": "turn"}}


def result(id, text=OLD, *, ok=True, is_error=False):
    return {"kind": "tool_result", "id": id, "tool": "submit_answer", "is_error": is_error,
            "result": {"ok": ok, "certified": True, "final_answer": text, "question": QUESTION, "claims_checked": 0}}


@pytest.mark.parametrize("events,expected", [
    ([call("one"), result("one"), call("two")], None),
    ([call("one"), call("two"), result("two", NEW, ok=False), result("one")], None),
    ([call("one"), call("two"), result("two", NEW), result("one", ok=False)], NEW),
    ([call("one"), call("two"), result("two", NEW), result("one")], NEW),
    ([call("one"), result("one", is_error=True)], None),
    ([result("one")], None),
    ([call("one"), result("one", ok="true")], None),
    ([call("one"), result("one", {"text": OLD})], None),
    ([call("one"), result("one"), call("two"), result("two", NEW)], NEW),
    ([call("one"), result("one"), call("two"), result("two", NEW, ok=False)], None),
], ids=["pending-revision", "old-success-after-failure", "old-failure-after-success", "old-success-after-success",
        "tool-error", "unpaired-result", "truthy-ok", "nonstring-final", "latest-success", "latest-failure"])
def test_cli_only_publishes_latest_completed_valid_submission(monkeypatch, events, expected):
    class Agent:
        name = "fixture"
        session_id = "fixture"

        async def ask(self, question):
            assert question == QUESTION
            for event in events:
                yield event
            yield {"kind": "text", "text": "The unverified model response."}
            yield {"kind": "done", "turns": 1, "session_id": "fixture", "error": None}

    output = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=output, force_terminal=False, width=140))
    asyncio.run(cli.run_turn(Agent(), QUESTION, False))
    visible = output.getvalue()
    assert OLD not in visible
    assert (NEW in visible) == (expected == NEW), visible
    assert "The unverified model response." not in visible
    if expected is None:
        assert "No answer was certified." in visible
