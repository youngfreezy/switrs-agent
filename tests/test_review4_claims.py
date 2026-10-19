"""Fourth-review claim regressions on isolated data; no models or production DB."""

import asyncio
import io

import pytest
from mcp import Client
from rich.console import Console

from switrs_agent import cli, mcp_server
from test_review_regressions import begin, county_query, kit, run, submit


def comparison_claim(query, text, op):
    return {
        "text": text,
        "op": op,
        "entity": ["los angeles", "orange"],
        "metric": "collisions",
        "population": "fixture collisions",
        "cells": [
            {"query_id": query["query_id"], "row": row, "column": "n"}
            for row in (0, 1)
        ],
    }


def test_direction_outside_numeric_claim_span_is_checked(kit):
    turn_id = begin(kit)
    query = county_query(kit)
    claim = comparison_claim(query, "100%", "percent_change")
    result = kit.check_calculation(
        turn_id, "Los Angeles decreased by 100% relative to Orange.", [claim]
    )
    assert not result["ok"], result


def test_numeric_only_positive_direction_span_is_supported(kit):
    turn_id = begin(kit)
    query = county_query(kit)
    claim = comparison_claim(query, "100%", "percent_change")
    result = kit.check_calculation(
        turn_id, "Los Angeles increased by 100% relative to Orange.", [claim]
    )
    assert result["ok"], result


def test_ratio_cannot_be_presented_as_unconverted_percentage(kit):
    turn_id = begin(kit)
    query = county_query(kit)
    claim = comparison_claim(query, "2%", "ratio")
    result = kit.check_calculation(
        turn_id, "Los Angeles had 2% as many collisions as Orange.", [claim]
    )
    assert not result["ok"], result


@pytest.mark.parametrize(("text", "op"), [("2 times", "ratio"), ("200%", "percent_of")])
def test_comparison_units_with_correct_scale_are_supported(kit, text, op):
    turn_id = begin(kit)
    query = county_query(kit)
    claim = comparison_claim(query, text, op)
    result = kit.check_calculation(
        turn_id, f"Los Angeles had {text} as many collisions as Orange.", [claim]
    )
    assert result["ok"], result


@pytest.mark.parametrize("number", ["2 billion", "2e6", "−2"])
def test_number_format_cannot_hide_scale_or_sign(kit, number):
    turn_id = begin(kit)
    query = run(kit, "SELECT COUNT(*) AS n FROM collisions WHERE county_location='los angeles'")
    assert query["rows"] == [[2]], query
    result = submit(kit, turn_id, query, f"There were {number} collisions.")
    assert not result["ok"], result


@pytest.mark.parametrize(
    ("number", "factor"), [("2 billion", 1_000_000_000), ("2e6", 1_000_000), ("−2", -1)]
)
def test_matching_scale_and_negative_number_formats_are_supported(kit, number, factor):
    turn_id = begin(kit)
    query = run(
        kit,
        f"SELECT COUNT(*) * {factor} AS n FROM collisions WHERE county_location='los angeles'",
    )
    assert "error" not in query, query
    assert query["rows"] == [[2 * factor]], query
    result = submit(kit, turn_id, query, f"The computed value was {number}.")
    assert result["ok"], result


@pytest.mark.parametrize("malformation", ["cell_string", "query_id_list"])
def test_malformed_nested_claim_returns_validation_failure(kit, malformation):
    turn_id = begin(kit)
    query = county_query(kit)
    claim = {
        "text": "Los Angeles had 2 collisions.",
        "op": "value",
        "entity": "los angeles",
        "metric": "collisions",
        "population": "fixture collisions",
        "cells": [{"query_id": query["query_id"], "row": 0, "column": "n"}],
    }
    if malformation == "cell_string":
        claim["cells"] = ["bad"]
    else:
        claim["cells"][0]["query_id"] = []
    result = kit.check_calculation(turn_id, claim["text"], [claim])
    assert not result["ok"] and result["errors"], result


def test_cli_displays_rejected_null_claims_without_crashing(kit, monkeypatch):
    turn_id = begin(kit)
    args = {"turn_id": turn_id, "answer": "No answer.", "claims": None}
    output = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=output, force_terminal=False, width=120))

    async def exercise():
        async with Client(mcp_server.build(kit)) as client:
            rejected = await client.call_tool("submit_answer", args)
        assert rejected.is_error
        error_text = "".join(item.text for item in rejected.content)

        class FakeAgent:
            name = "fixture"
            session_id = "fixture"

            async def ask(self, question):
                yield {"kind": "tool_call", "id": "1", "tool": "submit_answer", "input": args}
                yield {
                    "kind": "tool_result", "id": "1", "tool": "submit_answer",
                    "is_error": True, "result": error_text,
                }
                yield {
                    "kind": "done", "turns": 1, "cost_usd": 0,
                    "session_id": "fixture", "error": None,
                }

        await cli.run_turn(FakeAgent(), "Review fixture", False)

    asyncio.run(exercise())
    assert "rejected" in output.getvalue()
    assert "claims" in output.getvalue()


def test_huge_exponent_returns_validation_error(kit):
    turn_id = begin(kit)
    query = run(kit, 'SELECT COUNT(*) AS n FROM collisions')
    result = submit(kit, turn_id, query, 'There were 2e' + '9' * 5000 + ' collisions.')
    assert not result['ok'], result


def test_rejected_tool_arguments_cannot_inject_terminal_markup(monkeypatch):
    class FakeAgent:
        name = 'fixture'
        session_id = 'fixture'

        async def ask(self, question):
            yield {'kind': 'tool_call', 'id': '1', 'tool': 'begin_question',
                   'input': {'answerability': None, 'interpretation': '[/bad]'}}
            yield {'kind': 'tool_result', 'id': '1', 'tool': 'begin_question',
                   'is_error': True, 'result': {'error': 'Invalid interpretation [/bad]'}}
            yield {'kind': 'done', 'turns': 1, 'session_id': 'fixture', 'error': None}

    output = io.StringIO()
    monkeypatch.setattr(cli, 'console', Console(file=output, force_terminal=False, width=120))
    asyncio.run(cli.run_turn(FakeAgent(), 'fixture', False))
    assert 'rejected' in output.getvalue()
    assert '[/bad]' in output.getvalue()
