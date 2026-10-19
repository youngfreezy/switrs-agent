"""Fresh adversarial probes of server-owned metrics and certificate boundaries."""
import pytest
import yaml

from evals import run_evals
from test_certified_metrics import rich_kit
from test_certification import publish
from test_review_regressions import begin, kit, run


GOLDEN_QUESTIONS = yaml.safe_load(run_evals.GOLDEN.read_text())["questions"]


@pytest.mark.parametrize("case", GOLDEN_QUESTIONS, ids=lambda q: q["id"])
def test_every_documented_question_only_publishes_server_owned_content(rich_kit, case):
    turn, p, result = publish(rich_kit, case["question"], "partially_available")
    assert result["question"] == case["question"]
    assert result["status"] in ("answered", "unsupported", "no_matching_records")
    altered = p["answer"] + " These records prove seat belts cause fatalities."
    assert not rich_kit.submit_answer(turn, altered, p["claims"])["ok"]


@pytest.mark.parametrize("year", ["2000", "2021", "2022", "0000", "9999", "２０２１"])
def test_unavailable_or_partial_year_never_becomes_a_full_year_count(rich_kit, year):
    _, p, _ = publish(rich_kit, f"How many collisions in {year}?", "partially_available")
    assert p["status"] == "unsupported"
    assert p["claims"] == []


@pytest.mark.parametrize("column,value", [("cellphone_in_use", "NULL"), ("cellphone_in_use", "7")])
def test_unknown_cellphone_status_never_becomes_zero_percent(rich_kit, column, value):
    rich_kit.db.connection.execute(f"UPDATE parties SET {column}={value}")
    _, p, _ = publish(rich_kit, "What share of at-fault drivers were using a cellphone?")
    assert not any(c["op"] == "percent_of" for c in p["claims"])
    assert p["claims"][0]["computed"] == 3
    assert "unavailable" in p["answer"]


def test_unknown_belt_codes_do_not_enter_denominators(rich_kit):
    rich_kit.db.connection.execute("UPDATE victims SET victim_safety_equipment_1='unknown', victim_safety_equipment_2=NULL")
    _, p, _ = publish(rich_kit, "What are recorded victim death shares by lap/shoulder belt use?")
    assert not p["claims"]
    assert "unavailable" in p["answer"]


def test_missing_fatality_count_never_becomes_a_complete_total(rich_kit):
    rich_kit.db.connection.execute("UPDATE collisions SET killed_victims=NULL WHERE case_id='A'")
    turn = begin(rich_kit, "How many people were killed?")
    p = rich_kit.prepare_answer(turn)
    assert "error" in p
    assert not rich_kit.submit_answer(turn, "There was 1 fatality.", [])["ok"]


def test_county_ties_and_missing_counties_are_handled_explicitly(rich_kit):
    rich_kit.db.connection.execute("UPDATE collisions SET county_location=NULL WHERE case_id='A'")
    _, p, _ = publish(rich_kit, "Which county has the most collisions?")
    assert len(p["claims"]) == 2
    assert all(c["computed"] == 1 for c in p["claims"])
    assert "Los Angeles" in p["answer"] and "Orange" in p["answer"]
    assert p["answer"].count("tied") == 2


def test_no_known_county_cannot_create_winner(rich_kit):
    rich_kit.db.connection.execute("UPDATE collisions SET county_location=NULL")
    _, p, _ = publish(rich_kit, "Which county has the most collisions?")
    assert not p["claims"]
    assert p["status"] == "no_matching_records"


def test_exploratory_value_and_alias_cannot_change_certified_scope(rich_kit):
    turn = begin(rich_kit, "How many collisions in Orange County in 2020?")
    q = run(rich_kit, "SELECT COUNT(*) * 0 + 100000 AS orange_2020 FROM collisions")
    assert "error" not in q
    p = rich_kit.prepare_answer(turn)
    assert p["claims"][0]["computed"] == 1
    assert "Orange County in 2020" in p["answer"]
    assert all(c["cells"][0]["query_id"] != q["query_id"] for c in p["claims"])
    assert rich_kit.submit_answer(turn, p["answer"], p["claims"])["ok"]


def test_previous_question_certificate_cannot_be_replayed(rich_kit):
    old_turn, old, _ = publish(rich_kit, "How many collisions in Orange County?")
    new_turn = begin(rich_kit, "How many collisions in Los Angeles County?")
    new = rich_kit.prepare_answer(new_turn)
    assert not rich_kit.submit_answer(old_turn, old["answer"], old["claims"])["ok"]
    assert not rich_kit.submit_answer(new_turn, old["answer"], old["claims"])["ok"]
    assert rich_kit.submit_answer(new_turn, new["answer"], new["claims"])["ok"]


def test_returned_claim_cells_cannot_mutate_stored_preparation(rich_kit):
    turn, p, _ = publish(rich_kit)
    p["claims"][0]["cells"][0]["row"] = 999
    p["claims"][0]["computed"] = 100000
    assert not rich_kit.submit_answer(turn, p["answer"], p["claims"])["ok"]
    fresh = rich_kit.prepare_answer(turn)
    assert fresh["claims"][0]["computed"] == 3
    assert rich_kit.submit_answer(turn, fresh["answer"], fresh["claims"])["ok"]


def test_empty_population_never_fabricates_rates(rich_kit):
    rich_kit.db.connection.execute("DELETE FROM victims; DELETE FROM parties; DELETE FROM collisions")
    for question in (
        "What percentage of collisions involved a parked vehicle?",
        "What share of at-fault drivers were using a cellphone?",
        "What are recorded victim death shares by lap/shoulder belt use?",
        "How has COVID-19 changed collisions, if at all?",
    ):
        _, p, _ = publish(rich_kit, question)
        assert not any(c["op"] in ("percent_of", "percent_change") for c in p["claims"])
        assert "unavailable" in p["answer"]


@pytest.mark.parametrize("invalid", ["-1", "0.5"])
def test_invalid_individual_fatality_counts_cannot_cancel_into_valid_total(rich_kit, invalid):
    # Reconciliation currently checks copy fidelity and keys, not count domains.
    rich_kit.db.connection.execute("ALTER TABLE collisions ALTER COLUMN killed_victims TYPE DOUBLE")
    rich_kit.schema_cols = rich_kit.db.table_columns()
    rich_kit.db.connection.execute(f"UPDATE collisions SET killed_victims=CASE WHEN case_id='A' THEN {invalid} "
                                  f"WHEN case_id='B' THEN 3-({invalid}) ELSE 0 END")
    turn = begin(rich_kit, "How many people were killed?")
    p = rich_kit.prepare_answer(turn)
    assert "error" in p, p


def test_fatality_integer_precision_cannot_change_evidence_value(rich_kit):
    rich_kit.db.connection.execute("ALTER TABLE collisions ALTER COLUMN killed_victims TYPE BIGINT")
    rich_kit.schema_cols = rich_kit.db.table_columns()
    rich_kit.db.connection.execute("UPDATE collisions SET killed_victims=CASE WHEN case_id='A' THEN 9007199254740993 ELSE 0 END")
    turn = begin(rich_kit, "How many people were killed?")
    p = rich_kit.prepare_answer(turn)
    assert "error" in p or "9,007,199,254,740,993" in p["answer"], p


@pytest.mark.parametrize("outcome", [None, "unknown"])
def test_unknown_death_outcomes_cannot_certify_zero_death_share(rich_kit, outcome):
    rich_kit.db.connection.execute("UPDATE victims SET victim_degree_of_injury=?", [outcome])
    turn = begin(rich_kit, "What are recorded victim death shares by lap/shoulder belt use?")
    p = rich_kit.prepare_answer(turn)
    assert ("error" in p or not p["claims"] or
            ("recorded as killed" in p["answer"].lower() and "unknown" in p["answer"].lower()
             and "surviv" in p["answer"].lower())), p


def test_end_of_day_time_cannot_be_certified_as_hour_24(rich_kit):
    rich_kit.db.connection.execute("UPDATE collisions SET collision_time=TIME '24:00:00'")
    turn = begin(rich_kit, "At what hour of the day do the most fatal crashes happen?")
    p = rich_kit.prepare_answer(turn)
    assert "error" in p or all(c["computed"] < 24 for c in p["claims"] if c["metric"] == "hour_of_day"), p


@pytest.mark.parametrize("question,lower,upper", [
    ("How many collisions in 2019?", "2019-04-01", "2020-12-31"),
    ("How many people were killed in 2020?", "2019-01-01", "2020-06-30"),
    ("How has COVID-19 changed collisions, if at all?", "2019-04-01", "2020-12-31"),
    ("How has COVID-19 changed collisions, if at all?", "2019-01-01", "2020-06-30"),
])
def test_dataset_manifest_must_cover_whole_requested_period(rich_kit, question, lower, upper):
    rich_kit.dataset["coverage"] = {"min_collision_date": lower, "max_collision_date": upper}
    turn = begin(rich_kit, question)
    p = rich_kit.prepare_answer(turn)
    assert "error" in p or (not p["claims"] and p["status"] == "unsupported"), p


def test_snapshot_count_remains_valid_with_narrower_manifest(rich_kit):
    rich_kit.dataset["coverage"] = {"min_collision_date": "2019-04-01", "max_collision_date": "2020-04-01"}
    _, p, _ = publish(rich_kit, "How many collisions?")
    assert p["claims"][0]["computed"] == 3
    assert "snapshot" in p["answer"]


@pytest.mark.parametrize("coverage", [None, {}, [], {"min_collision_date": "bad", "max_collision_date": "2020-12-31"},
                                       {"min_collision_date": "2020-12-31", "max_collision_date": "2019-01-01"}])
def test_malformed_manifest_cannot_certify_calendar_count(rich_kit, coverage):
    rich_kit.dataset["coverage"] = coverage
    turn = begin(rich_kit, "How many collisions in 2019?")
    p = rich_kit.prepare_answer(turn)
    assert "error" in p, p


@pytest.mark.parametrize("value,total", [(9007199254740993, 18014398509481986),
                                         (9000000000000000000, 18000000000000000000)])
def test_exact_large_integer_total_is_preserved_in_claim_and_publication(rich_kit, value, total):
    rich_kit.db.connection.execute("ALTER TABLE collisions ALTER COLUMN killed_victims TYPE BIGINT")
    rich_kit.schema_cols = rich_kit.db.table_columns()
    rich_kit.db.connection.execute("UPDATE collisions SET killed_victims=CASE WHEN case_id='C' THEN 0 ELSE ? END", [value])
    _, p, result = publish(rich_kit, "How many people were killed?")
    assert type(p["claims"][0]["computed"]) is int
    assert p["claims"][0]["computed"] == total
    assert f"{total:,}" in p["answer"]
    # Both the answer and its generated evidence table must preserve the same integer.
    assert result["final_answer"].count(f"{total:,}") >= 2


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_nonfinite_fatality_input_never_becomes_a_certified_count(rich_kit, value):
    rich_kit.db.connection.execute("ALTER TABLE collisions ALTER COLUMN killed_victims TYPE DOUBLE")
    rich_kit.schema_cols = rich_kit.db.table_columns()
    rich_kit.db.connection.execute("UPDATE collisions SET killed_victims=? WHERE case_id='A'", [value])
    turn = begin(rich_kit, "How many people were killed?")
    assert "error" in rich_kit.prepare_answer(turn)


def test_tied_midnight_and_evening_hours_preserve_zero_hour(rich_kit):
    rich_kit.db.connection.execute("UPDATE collisions SET collision_time=CASE WHEN case_id='A' THEN TIME '00:00:00' ELSE TIME '18:00:00' END")
    _, p, _ = publish(rich_kit, "At what hour of the day do the most fatal crashes happen?")
    assert [c["computed"] for c in p["claims"] if c["metric"] == "hour_of_day"] == [0, 18]
    assert [c["computed"] for c in p["claims"] if c["metric"] == "fatal_collision_count"] == [1, 1]


@pytest.mark.parametrize("seed", range(6))
def test_valid_party_and_victim_categories_match_independent_python_oracle(rich_kit, seed):
    import random
    rng = random.Random(seed)
    con = rich_kit.db.connection
    con.execute("DELETE FROM victims; DELETE FROM parties")
    used, unused = "lap/shoulder harness used", "lap/shoulder harness not used"
    groups = {used: [], unused: []}
    drinking_cases = set()
    phones = []
    for number in range(1, 31):
        case_id = rng.choice(["A", "B", "C"])
        party_type = rng.choice(["driver", "bicyclist", "parked vehicle"])
        sobriety = rng.choice(["had been drinking, under influence", "had not been drinking", "impairment unknown", None])
        vehicle = rng.choice(["passenger car", "motorcycle"])
        fault, phone = rng.choice([0, 1, None]), rng.choice([0, 1, None])
        con.execute("INSERT INTO parties (case_id, party_number, party_type, party_sobriety, "
                    "statewide_vehicle_type, at_fault, cellphone_in_use) VALUES (?,?,?,?,?,?,?)",
                    [case_id, number, party_type, sobriety, vehicle, fault, phone])
        if party_type == "driver" and sobriety == "had been drinking, under influence":
            drinking_cases.add(case_id)
        if party_type == "driver" and fault == 1:
            phones.append(phone)
        for _ in range(rng.randrange(1, 5)):
            role = rng.choice(["driver", "passenger", "pedestrian"])
            injury = rng.choice(["killed", "severe injury", "no injury", None])
            one, two = rng.choice([used, unused, None, "unknown"]), rng.choice([used, unused, None, "unknown"])
            con.execute("INSERT INTO victims (case_id, party_number, victim_degree_of_injury, victim_role, "
                        "victim_safety_equipment_1, victim_safety_equipment_2) VALUES (?,?,?,?,?,?)",
                        [case_id, number, injury, role, one, two])
            if vehicle == "passenger car" and role in ("driver", "passenger"):
                for status, opposite in [(used, unused), (unused, used)]:
                    if status in (one, two) and opposite not in (one, two):
                        groups[status].append(injury == "killed")
    _, p, _ = publish(rich_kit, "How many collisions involved at least one driver who had been drinking?")
    assert p["claims"][0]["computed"] == len(drinking_cases)
    _, p, _ = publish(rich_kit, "What share of at-fault drivers were using a cellphone?")
    known = [v for v in phones if v is not None]
    expected = ([100 * sum(known) / len(known)] if known else []) + [phones.count(None)]
    assert [c["computed"] for c in p["claims"]] == pytest.approx(expected)
    _, p, _ = publish(rich_kit, "What are recorded victim death shares by lap/shoulder belt use?")
    expected = [100 * sum(groups[s]) / len(groups[s]) for s in (used, unused) if groups[s]]
    assert [c["computed"] for c in p["claims"]] == pytest.approx(expected)
