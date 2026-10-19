import pytest

from switrs_agent import grounding, guard

SCHEMA = {
    "collisions": {"case_id": "VARCHAR", "county_location": "VARCHAR", "killed_victims": "BIGINT",
                   "collision_date": "DATE", "collision_severity": "VARCHAR"},
    "parties": {"case_id": "VARCHAR", "party_number": "BIGINT", "vehicle_make": "VARCHAR", "at_fault": "BIGINT"},
    "victims": {"case_id": "VARCHAR", "party_number": "BIGINT", "victim_degree_of_injury": "VARCHAR"},
}


@pytest.mark.parametrize("sql,needle", [
    ("DELETE FROM collisions", "Only SELECT"),
    ("DROP TABLE parties", "Only SELECT"),
    ("SELECT 1; SELECT 2", "exactly one"),
    ("SELECT colour FROM parties", "Unknown column 'colour'"),
    ("SELECT vehicle_color FROM parties", "Unknown column"),
    ("SELECT p.killed_victims FROM parties p", "It is in collisions"),
    ("SELECT * FROM crashes", "Unknown table"),
    ("SELECT * FROM read_csv('/etc/passwd')", "not allowed"),
    ("SELECT x.case_id FROM collisions c", "Unknown table alias"),
    ("SELEC case_id FROM collisions", "parse"),
])
def test_guard_rejects(sql, needle):
    with pytest.raises(guard.GuardError, match=needle):
        guard.check(sql, SCHEMA)


def test_guard_accepts_aliases_ctes_and_derived_names():
    sql = """
    WITH by_county AS (SELECT county_location, count(*) AS n FROM collisions GROUP BY 1)
    SELECT county_location, n, 100.0 * n / sum(n) OVER () AS pct FROM by_county ORDER BY n DESC
    """
    report = guard.check(sql, SCHEMA)
    assert report.tables == {"collisions"}
    assert not report.warnings


def test_fanout_warning_and_distinct_ok():
    bad = "SELECT count(*) FROM collisions c JOIN parties p ON p.case_id = c.case_id WHERE p.at_fault = 1"
    good = "SELECT count(DISTINCT c.case_id) FROM collisions c JOIN parties p ON p.case_id = c.case_id"
    assert guard.check(bad, SCHEMA).warnings
    assert not guard.check(good, SCHEMA).warnings


RESULTS = [{"rows": [["los angeles", 2851925], ["orange", 728565]], "columns": ["county", "n"]},
           {"rows": [[9424334]], "columns": ["total"]}]


def test_grounding_accepts_values_and_derivations():
    answer = ("Los Angeles has 2,851,925 collisions, 30.26% of all 9,424,334 collisions, "
              "about 3.9x Orange (728,565). That is 2.85 million crashes.")
    out = grounding.verify(answer, RESULTS)
    assert out["ok"], out


def test_grounding_flags_invented_numbers():
    out = grounding.verify("Los Angeles has 3,100,000 collisions and 41% of fatalities.", RESULTS)
    assert not out["ok"]
    assert "3,100,000" in out["unsupported_numbers"]
    assert any(n.startswith("41") for n in out["unsupported_numbers"])


def test_grounding_exempts_years_and_question_numbers():
    out = grounding.verify("Between 2019 and 2020, in the top 3 counties ...", RESULTS, question="top 3 counties")
    assert out["ok"]


def test_grounding_ignores_query_ids():
    from switrs_agent.grounding import extract_numbers
    claims = extract_numbers("Belted 0.353% (7328-q1), unbelted 7.668% (7328-q2, aee1-q3, q4).")
    assert [c.text for c in claims] == ["0.353%", "7.668%"]
