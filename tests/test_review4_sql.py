"""Fourth-review SQL regressions using only the isolated DuckDB fixture."""

import pytest

from test_review_regressions import begin, kit, run, submit


@pytest.mark.parametrize("predicate", [
    "case_id IN (SELECT case_id FROM parties)",
    "EXISTS (SELECT case_id FROM parties)",
    "(SELECT COUNT(*) FROM parties) > 0",
])
def test_scalar_literal_filter_cannot_supply_output_provenance(kit, predicate):
    turn_id = begin(kit)
    query = run(kit, f"SELECT (SELECT 100000 FROM collisions WHERE {predicate} LIMIT 1) AS n "
                     "FROM collisions LIMIT 1")
    if "error" in query:
        return  # A fixable rejection of an unsupported shape is also safe.
    assert query["rows"] == [[100000]]
    result = submit(kit, turn_id, query, "There were 100000 collisions.")
    assert not result["ok"], result


@pytest.mark.parametrize("value", ["x.killed_victims", "x.killed_victims + 0"])
def test_scalar_collision_projection_cannot_erase_fanout(kit, value):
    begin(kit)
    query = run(kit, f"WITH joined AS (SELECT (SELECT {value} FROM collisions x "
                     "WHERE x.case_id=c.case_id) AS n FROM collisions c "
                     "JOIN parties p ON c.case_id=p.case_id) SELECT SUM(n) AS n FROM joined")
    # A's two killed victims repeat for its two parties: the unsafe sum is 5, not 3.
    assert "error" in query, query


@pytest.mark.parametrize("aggregate", ["COUNT(case_id)", "COUNT(*)"])
def test_count_over_joined_cte_preserves_fanout_warning(kit, aggregate):
    begin(kit)
    query = run(kit, "WITH joined AS (SELECT c.case_id FROM collisions c "
                     f"JOIN parties p ON c.case_id=p.case_id) SELECT {aggregate} AS n FROM joined")
    assert "error" not in query, query
    assert query["rows"] == [[4]]
    assert any("fan-out" in warning for warning in query["warnings"]), query


def test_nested_table_alias_does_not_override_outer_alias(kit):
    begin(kit)
    query = run(kit, "SELECT (SELECT COUNT(*) FROM parties t) AS n, t.killed_victims "
                     "FROM collisions t ORDER BY t.case_id")
    assert "error" not in query, query
    assert query["rows"] == [[4, 2], [4, 1], [4, 0]]
    assert query["data_derived"] == [True, True]


def test_real_scalar_numerator_and_denominator_remain_citable(kit):
    turn_id = begin(kit)
    query = run(kit, "SELECT (SELECT SUM(killed_victims) FROM collisions) * 1.0 / "
                     "(SELECT COUNT(*) FROM collisions) AS n")
    assert "error" not in query, query
    assert query["rows"] == [[1.0]]
    assert query["data_derived"] == [True]
    result = submit(kit, turn_id, query, "There was 1 death per collision.")
    assert result["ok"], result


def test_exists_filter_aggregate_cannot_launder_literal_case_output(kit):
    turn_id = begin(kit)
    query = run(kit, 'SELECT CASE WHEN EXISTS(SELECT COUNT(*) FROM parties) '
                     'THEN 100000 ELSE 100000 END AS n FROM collisions LIMIT 1')
    assert 'error' not in query, query
    result = submit(kit, turn_id, query, 'There were 100000 collisions.')
    assert not result['ok'], result
