"""Third-review regressions on small, isolated data; no model or production DB calls."""

import asyncio
import io

import duckdb
import pytest
from rich.console import Console

from switrs_agent import cli, tools
from switrs_agent.db import QueryResult


class FixtureDatabase:
    def __init__(self):
        self.connection = duckdb.connect(":memory:")
        self.connection.execute("""
            CREATE TABLE collisions(case_id VARCHAR, county_location VARCHAR, killed_victims INTEGER);
            INSERT INTO collisions VALUES ('A', 'los angeles', 2), ('B', 'los angeles', 1), ('C', 'orange', 0);
            CREATE TABLE parties(case_id VARCHAR, party_number INTEGER);
            INSERT INTO parties VALUES ('A', 1), ('A', 2), ('B', 1), ('C', 1);
            CREATE TABLE victims(case_id VARCHAR, party_number INTEGER, victim_degree_of_injury VARCHAR);
            INSERT INTO victims VALUES ('A', 1, 'killed'), ('B', 1, 'killed');
        """)

    def table_columns(self):
        return {
            table: {row[1]: row[2] for row in self.connection.execute(f"PRAGMA table_info('{table}')").fetchall()}
            for table in ("collisions", "parties", "victims")
        }

    def run(self, sql, explain_first=False):
        cursor = self.connection.execute(sql)
        columns = [column[0] for column in cursor.description]
        rows = [list(row) for row in cursor.fetchall()]
        return QueryResult(columns, rows, len(rows), False, 0)


@pytest.fixture
def kit(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "TRACE_DIR", tmp_path / "traces")
    monkeypatch.setattr(tools, "load_dataset_meta", lambda: {"dataset_version": "review-fixture"})
    database = FixtureDatabase()
    toolkit = tools.Toolkit(db=database, session_id="review-fixture")
    yield toolkit
    database.connection.close()


def begin(kit, question="How many collisions?", status="answerable"):
    kit.bind_question(question)
    opened = kit.begin_question(question, status, "Read the isolated review fixture.")
    assert "turn_id" in opened, opened
    return opened["turn_id"]


def run(kit, sql):
    return kit.run_sql(sql, "Review regression fixture", "Query only the in-memory fixture.")


def submit(kit, turn_id, query, text, column="n", row=0, entity=None):
    claim = {
        "text": text,
        "op": "value",
        "cells": [{"query_id": query["query_id"], "row": row, "column": column}],
        "metric": "count",
        "population": "fixture records",
    }
    if entity is not None:
        claim["entity"] = entity
    result = kit.check_calculation(turn_id, text, [claim])
    assert result["certified"] is False and "final_answer" not in result
    return result


def county_query(kit):
    query = run(kit, "SELECT county_location, COUNT(*) AS n FROM collisions GROUP BY 1 ORDER BY n DESC")
    assert query["rows"] == [["los angeles", 2], ["orange", 1]]
    return query


def test_supported_county_calculation_passes_without_certification(kit):
    turn_id = begin(kit)
    result = submit(kit, turn_id, county_query(kit), "Los Angeles had 2 collisions.", entity="los angeles")
    assert result["ok"], result
    assert result["certified"] is False and "final_answer" not in result


def test_extra_unsupported_number_cannot_hide_inside_supported_claim(kit):
    turn_id = begin(kit)
    result = submit(kit, turn_id, county_query(kit),
                    "Los Angeles had 2 collisions, and Nevada had 777 crashes.", entity="los angeles")
    assert not result["ok"], result


def test_number_in_question_does_not_authorize_factual_answer(kit):
    turn_id = begin(kit, "Were there 7 crashes in Nevada?", "unavailable")
    result = kit.check_calculation(turn_id, "There were 7 crashes in Nevada.", [])
    assert not result["ok"], result


def test_mentioned_entity_is_not_the_asserted_entity(kit):
    turn_id = begin(kit)
    result = submit(kit, turn_id, county_query(kit),
                    "Unlike Los Angeles, Orange had 2 collisions.", entity="los angeles")
    assert not result["ok"], result


@pytest.mark.parametrize("sql", [
    "SELECT 100000 AS n FROM collisions LIMIT 1",
    "WITH x AS (SELECT 100000 AS n FROM collisions LIMIT 1) SELECT n FROM x",
    "SELECT killed_victims AS n FROM collisions UNION ALL SELECT 100000 AS n",
    "SELECT killed_victims AS n FROM collisions UNION ALL (SELECT killed_victims AS n FROM collisions UNION ALL SELECT 100000 AS n)",
    "WITH x AS (SELECT killed_victims AS n FROM collisions UNION ALL SELECT 100000 AS n) SELECT n FROM x",
    "SELECT 100000 AS n UNION ALL SELECT killed_victims AS n FROM collisions",
])
def test_literal_result_cannot_be_used_as_evidence(kit, sql):
    turn_id = begin(kit)
    query = run(kit, sql)
    if "error" in query:
        return  # Rejecting an unsupported SQL shape is also safe.
    row = next(index for index, values in enumerate(query["rows"]) if values[0] == 100000)
    result = submit(kit, turn_id, query, "There were 100000 crashes.", row=row)
    assert not result["ok"], result


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) AS n FROM collisions c JOIN parties p ON TRUE",
    "SELECT COUNT(*) AS n FROM collisions c JOIN parties p ON c.case_id=p.case_id OR TRUE",
    "WITH p AS (SELECT * FROM parties) SELECT COUNT(*) AS n FROM collisions c JOIN p ON TRUE",
    "SELECT COUNT(*) AS n FROM collisions c JOIN (SELECT * FROM parties) p ON TRUE",
    "WITH p AS (SELECT * FROM parties) SELECT SUM(c.killed_victims) AS n FROM collisions c JOIN p ON c.case_id=p.case_id",
    "SELECT SUM(c.killed_victims) AS n FROM collisions c JOIN (SELECT * FROM parties) p ON c.case_id=p.case_id",
])
def test_unsafe_join_or_collision_sum_is_rejected(kit, sql):
    begin(kit)
    result = run(kit, sql)
    assert "error" in result, result


@pytest.mark.parametrize(("sql", "expected"), [
    ("SELECT SUM(c.killed_victims) AS n FROM collisions c WHERE EXISTS (SELECT 1 FROM parties p WHERE p.case_id=c.case_id)", 3),
    ("SELECT COUNT(DISTINCT c.case_id) AS n FROM collisions c JOIN parties p USING(case_id) JOIN victims v USING(case_id,party_number)", 2),
    ("SELECT COUNT(*) AS n FROM collisions", 3),
    ("SELECT COUNT(1) AS n FROM collisions", 3),
])
def test_supported_queries_produce_citable_counts(kit, sql, expected):
    turn_id = begin(kit)
    query = run(kit, sql)
    assert "error" not in query, query
    assert query["rows"] == [[expected]]
    result = submit(kit, turn_id, query, f"There were {expected} records.")
    assert result["ok"], result


@pytest.mark.parametrize("projection", ["*", "c.*"])
def test_wildcard_provenance_covers_every_output_column(kit, projection):
    turn_id = begin(kit)
    query = run(kit, f"SELECT {projection} FROM collisions c ORDER BY case_id")
    assert "error" not in query, query
    assert len(query["data_derived"]) == len(query["columns"])
    assert all(query["data_derived"])
    result = submit(kit, turn_id, query, "Los Angeles had 2 deaths.", column="killed_victims", entity="los angeles")
    assert result["ok"], result


def test_bad_provenance_metadata_returns_validation_failure_not_exception(kit):
    turn_id = begin(kit)
    query = county_query(kit)
    kit.queries[query["query_id"]]["data_derived"] = [True]
    result = submit(kit, turn_id, query, "Los Angeles had 2 collisions.", entity="los angeles")
    assert not result["ok"], result


@pytest.mark.parametrize('certificate_marker', [True, False, None])
def test_cli_publishes_only_checked_final_answer(monkeypatch, certificate_marker):
    checked = "California data only.\n\nCaveats: fixture."

    class FakeAgent:
        name = "fixture"
        session_id = "fixture"

        async def ask(self, question):
            yield {"kind": "tool_call", "id": "1", "tool": "submit_answer", "input": {"claims": [], "turn_id": "t1"}}
            yield {"kind": "tool_result", "id": "1", "tool": "submit_answer", "is_error": False,
                   "result": {"ok": True, "certified": certificate_marker, "claims_checked": 0, "final_answer": checked, "question": question}}
            yield {"kind": "text", "text": "Nevada had 777 crashes.\n\n" + checked}
            yield {"kind": "done", "turns": 1, "cost_usd": 0, "session_id": "fixture", "error": None}

    output = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=output, force_terminal=False, width=120))
    asyncio.run(cli.run_turn(FakeAgent(), "Review fixture", False))
    assert ("California data only." in output.getvalue()) == (certificate_marker is True)
    assert "Nevada had 777 crashes." not in output.getvalue()


@pytest.mark.parametrize("sql", [
    "WITH joined AS (SELECT c.killed_victims FROM collisions c JOIN parties p ON c.case_id=p.case_id) SELECT SUM(killed_victims) AS n FROM joined",
    "SELECT SUM(killed_victims) AS n FROM (SELECT c.killed_victims FROM collisions c JOIN parties p ON c.case_id=p.case_id) joined",
])
def test_outer_aggregate_cannot_hide_collision_fanout(kit, sql):
    begin(kit)
    result = run(kit, sql)
    assert "error" in result, result


@pytest.mark.parametrize("condition", [
    "COALESCE(p.case_id,c.case_id)=p.case_id",
    "p.case_id=COALESCE(p.case_id,c.case_id)",
])
def test_coalesce_cannot_hide_tautological_join(kit, condition):
    begin(kit)
    result = run(kit, f"SELECT COUNT(*) AS n FROM collisions c JOIN parties p ON {condition}")
    assert "error" in result, result


def county_claim(query, text, entity, row):
    return {"text": text, "entity": entity, "op": "value",
            "cells": [{"query_id": query["query_id"], "row": row, "column": "n"}],
            "metric": "collisions", "population": "fixture collisions"}


def test_second_claim_cannot_borrow_first_claim_entity(kit):
    turn_id = begin(kit)
    query = county_query(kit)
    answer = "Los Angeles had 2 collisions, Orange had 2 collisions."
    claim_list = [county_claim(query, text, "los angeles", 0) for text in
                  ("Los Angeles had 2 collisions", "Orange had 2 collisions")]
    result = kit.check_calculation(turn_id, answer, claim_list)
    assert not result["ok"], result


def test_separate_supported_county_claims_in_one_sentence(kit):
    turn_id = begin(kit)
    query = county_query(kit)
    answer = "Los Angeles had 2 collisions, Orange had 1 collision."
    claim_list = [county_claim(query, "Los Angeles had 2 collisions", "los angeles", 0),
                  county_claim(query, "Orange had 1 collision", "orange", 1)]
    result = kit.check_calculation(turn_id, answer, claim_list)
    assert result["ok"], result


def test_ratio_claim_can_cover_only_numeric_phrase(kit):
    turn_id = begin(kit)
    query = county_query(kit)
    answer = "Los Angeles had about 2 times as many collisions as Orange."
    claim = {"text": "about 2 times", "op": "ratio", "entity": ["los angeles", "orange"],
             "cells": [{"query_id": query["query_id"], "row": row, "column": "n"} for row in (0, 1)],
             "metric": "collisions", "population": "fixture collisions"}
    result = kit.check_calculation(turn_id, answer, [claim])
    assert result["ok"], result
    result = kit.check_calculation(turn_id, answer, [{**claim, "entity": ["orange", "los angeles"]}])
    assert not result["ok"], result


@pytest.mark.parametrize("sql", [
    "WITH p AS (SELECT case_id AS crash_key, party_number FROM parties) SELECT COUNT(*) AS n FROM collisions c JOIN p ON c.case_id=p.crash_key",
    "SELECT COUNT(*) AS n FROM collisions c JOIN (SELECT case_id AS crash_key, party_number FROM parties) p ON c.case_id=p.crash_key",
])
def test_renamed_keys_preserve_correct_derived_relation_joins(kit, sql):
    turn_id = begin(kit)
    query = run(kit, sql)
    assert "error" not in query, query
    assert query["rows"] == [[4]]
    result = submit(kit, turn_id, query, "There were 4 parties.")
    assert result["ok"], result


def test_data_only_union_preserves_citable_outputs(kit):
    turn_id = begin(kit)
    query = run(kit, "SELECT killed_victims AS n FROM collisions WHERE case_id='A' "
                     "UNION ALL SELECT killed_victims AS n FROM collisions WHERE case_id='B' ORDER BY n DESC")
    assert "error" not in query, query
    assert query["rows"] == [[2], [1]]
    assert query["data_derived"] == [True]
    result = submit(kit, turn_id, query, "There were 2 deaths.")
    assert result["ok"], result


@pytest.mark.parametrize("projection,grouping", [
    ("c.killed_victims + 0 * COUNT(*) OVER ()", ""),
    ("MAX(c.killed_victims)", "GROUP BY c.case_id, p.party_number"),
])
def test_window_or_person_group_does_not_erase_multiplied_values(kit, projection, grouping):
    begin(kit)
    result = run(kit, f"WITH joined AS (SELECT {projection} AS n FROM collisions c "
                      f"JOIN parties p ON c.case_id=p.case_id {grouping}) SELECT SUM(n) AS n FROM joined")
    assert "error" in result, result


def test_unsupported_semi_join_returns_fixable_error(kit):
    begin(kit)
    result = run(kit, "SELECT COUNT(*) AS n FROM collisions c SEMI JOIN parties p USING(case_id)")
    assert "error" in result and "EXISTS" in result["error"], result
