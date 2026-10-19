"""The evaluator must reject corrupted versions of correct answers (Codex review, 2026-10-19)."""

import pathlib
import sys

import duckdb
import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "scripts"))
import run_evals  # noqa: E402
from reconcile import assess  # noqa: E402

DB = ROOT / "data" / "switrs.duckdb"
pytestmark = pytest.mark.skipif(not DB.exists(), reason="data/switrs.duckdb not built")
GOLDEN = {q["id"]: q for q in yaml.safe_load((ROOT / "evals" / "golden.yaml").read_text())["questions"]}


@pytest.fixture(scope="module")
def con():
    return duckdb.connect(str(DB), read_only=True)


def passes(con, qid, answer):
    return run_evals.grade(GOLDEN[qid], answer, con)["pass"]


def test_q01_entity_must_sit_with_the_number(con):
    good = "Los Angeles has the most collisions, 2,851,925 (30.3% of all). These are raw counts, not per capita."
    swapped = "Los Angeles is first. Orange had 2,851,925 collisions (30.3%). These are raw counts, not per capita."
    assert passes(con, "q01_top_county", good)
    assert not passes(con, "q01_top_county", swapped)


def test_q04_direction_must_match_sign(con):
    good = "Collisions fell 22.1% in 2020. April 2020 had 46.1% fewer crashes than April 2019. The fatal share rose to 0.93%."
    flipped = "Collisions rose 22.1% in 2020. April 2020 had 46.1% fewer crashes than April 2019. The fatal share rose to 0.93%."
    bare = "Collisions changed 22.1% in 2020. April 2020 changed 46.1%. The fatal share rose to 0.93%."
    assert passes(con, "q04_covid", good)
    assert not passes(con, "q04_covid", flipped)
    assert not passes(con, "q04_covid", bare)


def test_q10_invented_count_fails_even_with_the_right_disclaimer(con):
    good = "This data covers only California, so it has no Las Vegas crashes. The drive there uses I-15."
    invented = "This data covers only California. There were 7 crashes in Las Vegas in 2019."
    assert passes(con, "q10_las_vegas", good)
    assert not passes(con, "q10_las_vegas", invented)


def test_q11_inflated_number_fails(con):
    good = "Belted occupants died at 0.342%, unbelted at 8.21%. This is observational and does not prove cause."
    inflated = "Belted occupants died at 0.342%, unbelted at 9.21%. This is observational and does not prove cause."
    assert passes(con, "q11_belt_death_share", good)
    assert not passes(con, "q11_belt_death_share", inflated)


def test_q05_proxy_verdict_without_the_gap_fails(con):
    good = ("The data cannot test this. It has no vehicle color and no traffic stops, only crashes. "
            "A make comparison would be descriptive only.")
    verdict = "Police are not biased: drivers of sports cars are found at fault less often."
    assert passes(con, "q05_red_sports_car_bias", good)
    assert not passes(con, "q05_red_sports_car_bias", verdict)


def test_frozen_baseline_matches(con):
    golden = list(GOLDEN.values())
    assert run_evals.check_frozen(golden, con) == []


def test_reconcile_fails_on_any_cast_loss():
    cols = {"collisions.collision_date": {"values_lost_to_cast": 1}, "parties.party_age": {"values_lost_to_cast": 0}}
    assert any("became NULL" in p for p in assess(cols, {"k": 0}, {"a": {"match": True}}))
    assert assess({"x.y": {"values_lost_to_cast": 0}}, {"k": 0}, {"a": {"match": True}}) == []
