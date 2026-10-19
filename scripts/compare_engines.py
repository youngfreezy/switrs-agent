"""Run the same portable SQL on the SQLite source and the DuckDB copy; compare results and timing.

Accuracy first: any result difference is reported before any speed number.
SQLite empty strings and DuckDB NULLs are reported as a difference unless --equate-empty is set,
because that mapping is a cleaning decision made during conversion.

usage: uv run python scripts/compare_engines.py --sqlite ~/Desktop/switrs/switrs.sqlite [--equate-empty]
"""

import argparse
import json
import pathlib
import sqlite3
import time

import duckdb

ROOT = pathlib.Path(__file__).resolve().parent.parent

YEAR = "substr(CAST(collision_date AS VARCHAR), 1, 4)"
QUERIES = {
    "top_counties": "SELECT county_location, count(*) FROM collisions GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 5",
    "parked_share_two_definitions": (
        "SELECT (SELECT sum(CASE WHEN motor_vehicle_involved_with = 'parked motor vehicle' THEN 1 ELSE 0 END) FROM collisions),"
        " (SELECT count(DISTINCT case_id) FROM parties WHERE movement_preceding_collision = 'parked'),"
        " (SELECT count(*) FROM collisions)"),
    "belt_cohorts_both_fields": (
        "SELECT CASE WHEN (e1 = 'U' OR e2 = 'U') AND (e1 = 'N' OR e2 = 'N') THEN 'conflict'"
        " WHEN e1 = 'U' OR e2 = 'U' THEN 'used' WHEN e1 = 'N' OR e2 = 'N' THEN 'not used' ELSE 'other' END g,"
        " count(*), sum(CASE WHEN inj = 'killed' THEN 1 ELSE 0 END), sum(CASE WHEN inj = 'no injury' THEN 1 ELSE 0 END)"
        " FROM (SELECT CASE v.victim_safety_equipment_1 WHEN 'lap/shoulder harness used' THEN 'U' WHEN 'lap/shoulder harness not used' THEN 'N' END e1,"
        "              CASE v.victim_safety_equipment_2 WHEN 'lap/shoulder harness used' THEN 'U' WHEN 'lap/shoulder harness not used' THEN 'N' END e2,"
        "              v.victim_degree_of_injury inj"
        "       FROM victims v JOIN parties p ON p.case_id = v.case_id AND p.party_number = v.party_number"
        "       WHERE p.statewide_vehicle_type = 'passenger car' AND v.victim_role IN ('driver', 'passenger')) t"
        " GROUP BY 1 ORDER BY 1"),
    "yearly_totals_and_fatal": (
        f"SELECT {YEAR} y, count(*), sum(CASE WHEN collision_severity = 'fatal' THEN 1 ELSE 0 END), sum(killed_victims)"
        f" FROM collisions WHERE {YEAR} >= '2018' GROUP BY 1 ORDER BY 1"),
    "drinking_driver_collisions": (
        "SELECT count(DISTINCT case_id), count(*) FROM parties"
        " WHERE party_type = 'driver' AND party_sobriety LIKE 'had been drinking%'"),
    "fatal_by_hour": (
        "SELECT substr(CAST(collision_time AS VARCHAR), 1, 2) h, count(*) FROM collisions"
        " WHERE collision_severity = 'fatal' GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 3"),
    "cellphone_at_fault_drivers": (
        "SELECT count(*), count(cellphone_in_use), sum(cellphone_in_use) FROM parties"
        " WHERE party_type = 'driver' AND at_fault = 1"),
    "missing_party_sex": "SELECT count(*) FROM parties WHERE party_sex IS NULL",
    "missing_or_empty_party_sex": "SELECT count(*) FROM parties WHERE party_sex IS NULL OR party_sex = ''",
}


def normalise(rows, equate_empty):
    out = []
    for r in rows:
        row = []
        for v in r:
            if equate_empty and v == "":
                v = None
            if isinstance(v, float):
                v = round(v, 6)
            row.append(v)
        out.append(tuple(row))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", required=True)
    ap.add_argument("--duckdb", default=str(ROOT / "data" / "switrs.duckdb"))
    ap.add_argument("--equate-empty", action="store_true")
    args = ap.parse_args()
    src = sqlite3.connect(f"file:{pathlib.Path(args.sqlite).expanduser()}?immutable=1", uri=True)
    dst = duckdb.connect(args.duckdb, read_only=True)

    report = []
    for name, sql in QUERIES.items():
        t0 = time.monotonic(); s_rows = src.execute(sql).fetchall(); t_s = time.monotonic() - t0
        t0 = time.monotonic(); d_rows = dst.execute(sql).fetchall(); t_d = time.monotonic() - t0
        same = normalise(s_rows, args.equate_empty) == normalise(d_rows, args.equate_empty)
        report.append({"query": name, "identical": same, "sqlite_s": round(t_s, 2), "duckdb_s": round(t_d, 3),
                       "sqlite": s_rows if not same else None, "duckdb": d_rows if not same else None})
        print(f"{'SAME' if same else 'DIFF'}  {name:32s} sqlite {t_s:6.1f}s  duckdb {t_d:6.3f}s", flush=True)
        if not same:
            print(f"      sqlite: {s_rows[:5]}\n      duckdb: {d_rows[:5]}")
    out = ROOT / "outputs" / "engine_comparison.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=1, default=str))
    diffs = [r["query"] for r in report if not r["identical"]]
    total_s = sum(r["sqlite_s"] for r in report); total_d = sum(r["duckdb_s"] for r in report)
    print(f"\n{len(report) - len(diffs)}/{len(report)} identical. Differences: {diffs or 'none'}")
    print(f"total time: sqlite {total_s:.0f}s, duckdb {total_d:.1f}s")


if __name__ == "__main__":
    main()
