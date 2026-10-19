"""Historical answer badges must obey the same boundary as current streamed answers."""
import copy

import pytest
from fastapi.testclient import TestClient

from ui import server
from ui.store import JsonChatStore

QUESTION = "How many collisions?"
ANSWER = "There were 3 reported collisions."


def submission(call_id="one", *, answer=ANSWER, question=QUESTION, marker=True, ok=True, is_error=False,
               tool="submit_answer"):
    result = {"ok": ok, "question": question, "final_answer": answer}
    if marker is not None:
        result["certified"] = marker
    return [
        {"kind": "tool_call", "id": call_id, "tool": tool, "input": {}},
        {"kind": "tool_result", "id": call_id, "result": result, "is_error": is_error},
    ]


def history_response(tmp_path, monkeypatch, events, question=QUESTION):
    store = JsonChatStore(tmp_path / "chats.json")
    monkeypatch.setattr(server, "store", store)
    chat = store.create("history", "Old chat")
    store.add_turn(chat, question, copy.deepcopy(events), 1)
    original_file = store.path.read_bytes()
    with TestClient(server.app) as client:
        response = client.get("/api/chats/history")
    assert response.status_code == 200
    # Presentation filtering must preserve the original saved chat and tool evidence.
    assert store.path.read_bytes() == original_file
    assert store.get("history")["turns"][0]["events"] == events
    return response.json()["turns"][0]["events"]


@pytest.mark.parametrize("reason", [
    "legacy", "false_marker", "failed", "other_question", "edited_final", "failed_revision",
    "pending_revision", "no_call", "different_tool", "tool_error", "truthy_ok", "truthy_marker",
])
def test_old_or_invalid_answer_is_preserved_only_as_unverified_notes(tmp_path, monkeypatch, reason):
    events = submission()
    final_answer = ANSWER
    if reason == "legacy":
        events = submission(marker=None)
    elif reason == "false_marker":
        events = submission(marker=False)
    elif reason == "failed":
        events = submission(ok=False)
    elif reason == "other_question":
        events = submission(question="How many people were killed?")
    elif reason == "edited_final":
        final_answer = "The records prove seat belts cause deaths."
    elif reason == "failed_revision":
        events += submission("two", ok=False)
    elif reason == "pending_revision":
        events += [{"kind": "tool_call", "id": "two", "tool": "submit_answer", "input": {}}]
    elif reason == "no_call":
        events = events[1:]
    elif reason == "different_tool":
        events = submission(tool="run_sql")
    elif reason == "tool_error":
        events = submission(is_error=True)
    elif reason == "truthy_ok":
        events = submission(ok="true")
    elif reason == "truthy_marker":
        events = submission(marker=1)
    events.append({"kind": "certified", "final_answer": final_answer})
    restored = history_response(tmp_path, monkeypatch, events)
    assert not any(e["kind"] == "certified" for e in restored), restored
    assert any(e["kind"] == "text" and final_answer in e["text"] for e in restored)
    assert any(e["kind"] == "uncertified" for e in restored)
    assert [e for e in restored if e["kind"].startswith("tool_")] == [e for e in events if e["kind"].startswith("tool_")]


def test_current_matching_certificate_survives_reopening(tmp_path, monkeypatch):
    events = submission() + [{"kind": "certified", "final_answer": ANSWER}]
    restored = history_response(tmp_path, monkeypatch, events)
    assert [e for e in restored if e["kind"] == "certified"] == [{"kind": "certified", "final_answer": ANSWER}]
    assert not any(e["kind"] == "uncertified" for e in restored)


def test_later_failed_submission_revokes_earlier_stored_final(tmp_path, monkeypatch):
    events = submission() + [{"kind": "certified", "final_answer": ANSWER}] + submission("two", ok=False)
    restored = history_response(tmp_path, monkeypatch, events)
    assert not any(e["kind"] == "certified" for e in restored)
    assert any(e["kind"] == "text" and ANSWER in e["text"] for e in restored)


def test_only_latest_success_is_restored_with_a_checked_badge(tmp_path, monkeypatch):
    revised = "There were 3 reported collisions across the available snapshot."
    events = submission() + [{"kind": "certified", "final_answer": ANSWER}]
    events += submission("two", answer=revised) + [{"kind": "certified", "final_answer": revised}]
    restored = history_response(tmp_path, monkeypatch, events)
    assert [e for e in restored if e["kind"] == "certified"] == [{"kind": "certified", "final_answer": revised}]
    assert any(e["kind"] == "text" and ANSWER in e["text"] for e in restored)


def test_final_badge_without_supporting_tool_history_is_unverified(tmp_path, monkeypatch):
    restored = history_response(tmp_path, monkeypatch, [{"kind": "certified", "final_answer": ANSWER}])
    assert not any(e["kind"] == "certified" for e in restored)
    assert any(e["kind"] == "text" and ANSWER in e["text"] for e in restored)


def test_late_result_from_earlier_call_cannot_replace_latest_submission(tmp_path, monkeypatch):
    old = submission("one", answer="Unsupported old answer.")
    current = submission("two")
    events = [old[0], current[0], current[1], old[1], {"kind": "certified", "final_answer": ANSWER}]
    restored = history_response(tmp_path, monkeypatch, events)
    assert [e for e in restored if e["kind"] == "certified"] == [{"kind": "certified", "final_answer": ANSWER}]


def test_late_old_success_cannot_rescue_a_failed_latest_submission(tmp_path, monkeypatch):
    old = submission("one")
    failed = submission("two", ok=False)
    events = [old[0], failed[0], failed[1], old[1], {"kind": "certified", "final_answer": ANSWER}]
    restored = history_response(tmp_path, monkeypatch, events)
    assert not any(e["kind"] == "certified" for e in restored)
    assert any(e["kind"] == "text" and ANSWER in e["text"] for e in restored)


def test_interrupted_turn_does_not_create_a_previously_absent_final_badge(tmp_path, monkeypatch):
    restored = history_response(tmp_path, monkeypatch, submission())
    assert not any(e["kind"] == "certified" for e in restored)
    assert restored == submission()
