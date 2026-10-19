"""Codex review cases (2026-10-19): each must be rejected. Runs against the real DuckDB copy."""

import pathlib

import pytest

from switrs_agent.tools import Toolkit

DB = pathlib.Path(__file__).resolve().parent.parent / "data" / "switrs.duckdb"
pytestmark = pytest.mark.skipif(not DB.exists(), reason="data/switrs.duckdb not built")


@pytest.fixture(scope="module")
def kit():
    return Toolkit(session_id="pytest-adversarial")


def open_turn(kit, q="Which county has the most collisions?", status="answerable"):
    kit.bind_question(q)
    r = kit.begin_question(q, status, "collisions by county, all years")
    assert "turn_id" in r, r
    return r["turn_id"]


def county_query(kit):
    r = kit.run_sql("SELECT county_location, count(*) AS n FROM collisions GROUP BY 1 ORDER BY n DESC LIMIT 3",
                    "collisions only", "count crashes per county")
    assert "query_id" in r, r
    return r["query_id"]


def test_wrong_entity_for_a_correct_number_is_rejected(kit):
    t = open_turn(kit)
    q = county_query(kit)
    answer = "Orange had 2,851,925 collisions."
    bad = kit.check_calculation(t, answer, [{"text": answer, "op": "value", "cells": [{"query_id": q, "row": 0, "column": "n"}],
                                         "entity": "orange", "metric": "collisions", "population": "all reported crashes"}])
    assert not bad["ok"] and any("does not match the cited row" in e for e in bad["errors"])
    missing = kit.check_calculation(t, answer, [{"text": answer, "op": "value", "cells": [{"query_id": q, "row": 0, "column": "n"}],
                                             "metric": "collisions", "population": "all reported crashes"}])
    assert not missing["ok"] and any("set 'entity'" in e for e in missing["errors"])


def test_correct_entity_passes_arithmetic_only(kit):
    t = open_turn(kit)
    q = county_query(kit)
    answer = "Los Angeles had 2,851,925 collisions."
    ok = kit.check_calculation(t, answer, [{"text": answer, "op": "value", "cells": [{"query_id": q, "row": 0, "column": "n"}],
                                        "entity": "los angeles", "metric": "collisions", "population": "all reported crashes"}])
    assert ok["ok"], ok
    assert ok["certified"] is False and "final_answer" not in ok


def covid_turn(kit):
    t = open_turn(kit, "How did total collisions change from 2019 to 2020?", "answerable")
    q = kit.run_sql("SELECT count(*) FILTER (WHERE year(collision_date) = 2020) AS n2020, "
                    "count(*) FILTER (WHERE year(collision_date) = 2019) AS n2019 FROM collisions",
                    "collisions only", "yearly totals")["query_id"]
    cells = [{"query_id": q, "row": 0, "column": "n2020"}, {"query_id": q, "row": 0, "column": "n2019"}]
    return t, cells


def test_sign_flip_is_rejected(kit):
    t, cells = covid_turn(kit)
    for text in ("Collisions increased by 22.1% in 2020.", "Collisions changed by 22.1% in 2020."):
        r = kit.check_calculation(t, text, [{"text": text, "op": "percent_change", "cells": cells,
                                         "metric": "collisions", "population": "all reported crashes"}])
        assert not r["ok"], text


def test_decrease_wording_or_negative_sign_passes(kit):
    t, cells = covid_turn(kit)
    for text in ("Collisions fell 22.1% in 2020.", "Collisions changed by -22.1% in 2020."):
        r = kit.check_calculation(t, text, [{"text": text, "op": "percent_change", "cells": cells,
                                         "metric": "collisions", "population": "all reported crashes"}])
        assert r["ok"], (text, r)


def test_unbound_number_is_rejected(kit):
    t = open_turn(kit, "How many crashes happened in Las Vegas in 2019?", "unavailable")
    r = kit.check_calculation(t, "There were 7 crashes in Nevada.", [])
    assert not r["ok"] and any("'7'" in e for e in r["errors"])
    ok = kit.check_calculation(t, "This data covers California only, so it has no Las Vegas crashes.", [])
    assert ok["ok"], ok
    assert ok["certified"] is False and "final_answer" not in ok


def test_literal_outputs_cannot_be_cited(kit):
    open_turn(kit)
    assert "error" in kit.run_sql("SELECT 100000 AS red_car_crashes", "none", "none")
    t = open_turn(kit)
    q = kit.run_sql("SELECT 100000 AS red_car_crashes, count(*) AS n FROM collisions", "collisions only", "x")["query_id"]
    text = "There were 100,000 red car crashes."
    r = kit.check_calculation(t, text, [{"text": text, "op": "value", "cells": [{"query_id": q, "row": 0, "column": "red_car_crashes"}],
                                     "metric": "crashes", "population": "red cars"}])
    assert not r["ok"] and any("literal" in e for e in r["errors"])


def test_queries_from_another_turn_cannot_be_cited(kit):
    open_turn(kit)
    q = county_query(kit)
    t2 = open_turn(kit, "Which county has the most collisions, again?")
    text = "Los Angeles had 2,851,925 collisions."
    r = kit.check_calculation(t2, text, [{"text": text, "op": "value", "cells": [{"query_id": q, "row": 0, "column": "n"}],
                                      "entity": "los angeles", "metric": "collisions", "population": "all"}])
    assert not r["ok"] and any("another question" in e for e in r["errors"])


@pytest.mark.parametrize("sql,needle", [
    ("SELECT SUM(c.killed_victims) FROM collisions c JOIN parties p USING (case_id)", "crash-level column"),
    ("SELECT count(DISTINCT c.case_id) FROM collisions c JOIN parties p ON c.case_id <> p.case_id", "non-equality"),
    ("SELECT count(*) FROM victims v JOIN parties p ON p.case_id = v.case_id", "party_number"),
    ("SELECT COUNT(*) FROM collisions c JOIN parties p ON TRUE", "no key equality"),
    ("SELECT COUNT(*) FROM collisions c JOIN parties p ON c.case_id = p.case_id OR TRUE", "uses OR"),
])
def test_bad_joins_are_rejected(kit, sql, needle):
    open_turn(kit)
    r = kit.run_sql(sql, "test", "test")
    assert needle in r.get("error", ""), r


def test_answerability_contradiction_is_rejected(kit):
    q = "Is there data showing police are biased against red sports cars?"
    kit.bind_question(q)
    r = kit.begin_question(q, "answerable", "at-fault share by make")
    assert "error" in r and "vehicle_color" in r["detected"]
    ok = kit.begin_question(q, "partially_available", "color is not recorded; describe at-fault share by make only")
    assert "turn_id" in ok
    assert any("color" in g for g in ok["gaps_to_state"]) and "observational" in ok["required_caveats"]


def test_queries_need_an_open_turn():
    fresh = Toolkit(session_id="pytest-no-turn")
    assert "begin_question" in fresh.run_sql("SELECT count(*) FROM collisions", "x", "x")["error"]


@pytest.mark.parametrize("sql", [
    "SELECT SUM(c.killed_victims) FROM collisions c WHERE EXISTS (SELECT 1 FROM parties p WHERE p.case_id = c.case_id)",
    "SELECT COUNT(DISTINCT c.case_id) FROM collisions c JOIN parties p USING (case_id) JOIN victims v USING (case_id, party_number)",
    "SELECT count(*) FROM collisions c JOIN parties p ON p.case_id = c.case_id JOIN victims v ON v.case_id = c.case_id AND v.party_number = p.party_number",
])
def test_correct_queries_are_accepted(kit, sql):
    open_turn(kit)
    r = kit.run_sql(sql, "test", "test")
    assert "query_id" in r, r


def test_cte_wrapped_literal_cannot_be_cited(kit):
    t = open_turn(kit)
    q = kit.run_sql("WITH x AS (SELECT 100000 AS n FROM collisions LIMIT 1) SELECT n FROM x", "x", "x")
    assert q["data_derived"] == [False]
    text = "There were 100,000 red car crashes."
    r = kit.check_calculation(t, text, [{"text": text, "op": "value", "cells": [{"query_id": q["query_id"], "row": 0, "column": "n"}],
                                     "metric": "crashes", "population": "red cars"}])
    assert not r["ok"] and any("literal" in e for e in r["errors"])


def test_final_answer_always_carries_required_caveats(kit):
    kit.bind_question("Which county is safest?")
    t = kit.begin_question("Which county is safest?", "descriptive_only", "crash counts by county")["turn_id"]
    # A raw count cannot establish safety. The server-owned limitation must
    # still carry all required caveats through the actual publication path.
    prepared = kit.prepare_answer(t)
    assert prepared['status'] in ('unsupported', 'needs_clarification')
    assert prepared['claims'] == []
    r = kit.submit_answer(t, prepared['answer'], prepared['claims'])
    assert r["ok"] and r['certified'], r
    assert "traffic volume" in r["final_answer"] and "per capita" in r["final_answer"]


def test_cross_row_ratio_needs_one_entity_per_cell(kit):
    t = open_turn(kit)
    q = county_query(kit)
    answer = "Los Angeles had about 3.9 times as many collisions as Orange."
    cells = [{"query_id": q, "row": 0, "column": "n"}, {"query_id": q, "row": 1, "column": "n"}]
    base = {"text": "about 3.9 times", "op": "ratio", "cells": cells, "metric": "collisions", "population": "all"}
    assert kit.check_calculation(t, answer, [{**base, "entity": ["los angeles", "orange"]}])["ok"]
    assert not kit.check_calculation(t, answer, [{**base, "entity": ["orange", "los angeles"]}])["ok"]
