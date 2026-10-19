"""Party measures must retain their grain through victim joins and projections."""

import duckdb
import pytest

from switrs_agent import guard

SCHEMA = {
    "collisions": {"case_id": "VARCHAR", "killed_victims": "BIGINT"},
    "parties": {"case_id": "VARCHAR", "party_number": "BIGINT", "party_number_killed": "BIGINT"},
    "victims": {"case_id": "VARCHAR", "party_number": "BIGINT", "victim_number": "BIGINT"},
}
JOIN = "FROM parties p JOIN victims v ON p.case_id=v.case_id AND p.party_number=v.party_number"


@pytest.mark.parametrize("aggregate", [
    "SUM(p.party_number_killed)", "SUM(DISTINCT p.party_number_killed)",
    "AVG(p.party_number_killed)", "FSUM(p.party_number_killed)",
    "KAHAN_SUM(p.party_number_killed)", "SUMKAHAN(p.party_number_killed)",
    "MEAN(p.party_number_killed)", "SUM(p.party_number_killed) OVER ()",
    "LIST_SUM(LIST(p.party_number_killed))", "ARRAY_SUM(ARRAY_AGG(p.party_number_killed))",
])
def test_party_measures_cannot_be_aggregated_after_victim_join(aggregate):
    with pytest.raises(guard.GuardError):
        guard.check(f"SELECT {aggregate} {JOIN}", SCHEMA)


@pytest.mark.parametrize("projection,grouping", [
    ("p.party_number_killed", ""), ("p.party_number_killed + 0", ""),
    ("FIRST_VALUE(p.party_number_killed) OVER ()", ""),
    ("MAX(p.party_number_killed)", "GROUP BY v.case_id, v.party_number, v.victim_number"),
    ("(SELECT x.party_number_killed FROM parties x WHERE x.case_id=p.case_id "
     "AND x.party_number=p.party_number)", ""),
])
def test_derived_relations_cannot_erase_repeated_party_values(projection, grouping):
    sql = f"WITH joined AS (SELECT {projection} AS n {JOIN} {grouping}) SELECT SUM(n) FROM joined"
    with pytest.raises(guard.GuardError):
        guard.check(sql, SCHEMA)


def test_renamed_party_projection_cannot_hide_grain():
    sql = ("WITH renamed AS (SELECT case_id AS c, party_number AS p, party_number_killed AS deaths FROM parties) "
           "SELECT SUM(r.deaths) FROM renamed r JOIN victims v ON r.c=v.case_id AND r.p=v.party_number")
    with pytest.raises(guard.GuardError):
        guard.check(sql, SCHEMA)


@pytest.mark.parametrize("sql,expected", [
    (f"SELECT COUNT(*) {JOIN}", 4),
    (f"SELECT SUM(v.victim_number) {JOIN}", 7),
    ("SELECT SUM(p.party_number_killed) FROM parties p WHERE EXISTS "
     "(SELECT 1 FROM victims v WHERE p.case_id=v.case_id AND p.party_number=v.party_number)", 3),
    ("SELECT SUM(p.party_number_killed) FROM parties p JOIN collisions c ON p.case_id=c.case_id", 3),
])
def test_valid_victim_aggregates_and_party_exists_queries_still_execute(sql, expected):
    report = guard.check(sql, SCHEMA)
    with duckdb.connect() as con:
        for table, columns in SCHEMA.items():
            con.execute(f"CREATE TABLE {table} ({', '.join(f'{c} {t}' for c, t in columns.items())})")
        con.execute("INSERT INTO collisions VALUES ('A', 2), ('B', 1)")
        con.execute("INSERT INTO parties VALUES ('A', 1, 2), ('B', 1, 1)")
        con.execute("INSERT INTO victims VALUES ('A', 1, 1), ('A', 1, 2), ('A', 1, 3), ('B', 1, 1)")
        assert con.execute(report.sql).fetchone()[0] == expected


@pytest.mark.parametrize("parent,measure,key", [
    ("parties", "party_number_killed", "AND p.party_number=v.party_number"),
    ("collisions", "killed_victims", ""),
])
def test_scalar_parent_lookup_over_victims_cannot_bypass_grain(parent, measure, key):
    sql = (f"SELECT SUM((SELECT p.{measure} FROM {parent} p "
           f"WHERE p.case_id=v.case_id {key})) FROM victims v")
    with pytest.raises(guard.GuardError):
        guard.check(sql, SCHEMA)


@pytest.mark.parametrize("join", ["JOIN", "LEFT JOIN", "RIGHT JOIN", "FULL JOIN"])
def test_reversed_join_and_null_padding_cannot_hide_party_fanout(join):
    sql = ("SELECT SUM(COALESCE(p.party_number_killed, 0)) FROM victims v "
           f"{join} parties p ON p.case_id=v.case_id AND p.party_number=v.party_number")
    with pytest.raises(guard.GuardError):
        guard.check(sql, SCHEMA)


def test_union_does_not_erase_party_fanout():
    sql = (f"WITH joined AS (SELECT p.party_number_killed AS n {JOIN}), "
           "combined AS (SELECT n FROM joined UNION ALL SELECT party_number_killed FROM parties) "
           "SELECT SUM(n) FROM combined")
    with pytest.raises(guard.GuardError):
        guard.check(sql, SCHEMA)


def test_parent_count_over_child_rows_warns_through_ctes():
    report = guard.check(f"WITH joined AS (SELECT p.case_id {JOIN}) SELECT COUNT(*) FROM joined", SCHEMA)
    assert any("fan-out" in warning for warning in report.warnings)


def test_standalone_party_count_does_not_warn():
    assert not guard.check("SELECT COUNT(*) FROM parties", SCHEMA).warnings
