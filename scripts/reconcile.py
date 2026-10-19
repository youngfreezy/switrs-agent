"""Reconcile the DuckDB copy against the SQLite source and write data/dataset_meta.json.

Checks, per column: non-empty values in SQLite vs non-NULL values in DuckDB. A loss means
TRY_CAST rejected values or an empty string became NULL; both are counted separately.
Also checks key uniqueness, orphans, and aggregates (rows by year, severity, county; sums of
killed and injured). The agent tools refuse to start unless the meta file says status "ok".

usage: uv run python scripts/reconcile.py --sqlite ~/Desktop/switrs/switrs.sqlite
"""

import argparse
import json
import pathlib
import sqlite3
import sys
import time

import duckdb

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from switrs_agent.dataset_integrity import INTEGRITY_VERSION, compare_records, sha256, validate_source

ROOT = pathlib.Path(__file__).resolve().parent.parent
TRANSFORM_VERSION = "build_duckdb v1: TRY_CAST INT->BIGINT, REAL->DOUBLE, dates->DATE, time->TIME, ''->NULL"
TABLES = ["collisions", "parties", "victims", "case_ids"]
AGGREGATES = {
    "rows_by_year": "SELECT substr(CAST(collision_date AS VARCHAR), 1, 4) y, count(*) FROM collisions GROUP BY 1 ORDER BY 1",
    "rows_by_severity": "SELECT collision_severity, count(*) FROM collisions GROUP BY 1 ORDER BY 1",
    "rows_by_county": "SELECT county_location, count(*) FROM collisions GROUP BY 1 ORDER BY 1",
    "sum_killed_injured": "SELECT sum(killed_victims), sum(injured_victims) FROM collisions",
    "victims_by_injury": "SELECT victim_degree_of_injury, count(*) FROM victims GROUP BY 1 ORDER BY 1",
}
KEY_CHECKS = {
    "collisions.case_id unique": "SELECT count(*) - count(DISTINCT case_id) FROM collisions",
    "parties (case_id, party_number) unique": "SELECT count(*) FROM (SELECT case_id, party_number FROM parties GROUP BY 1, 2 HAVING count(*) > 1)",
    "parties without collision": "SELECT count(*) FROM parties p WHERE NOT EXISTS (SELECT 1 FROM collisions c WHERE c.case_id = p.case_id)",
    "victims without party": "SELECT count(*) FROM victims v WHERE NOT EXISTS (SELECT 1 FROM parties p WHERE p.case_id = v.case_id AND p.party_number = v.party_number)",
    "case_ids without collision": "SELECT count(*) FROM case_ids i WHERE NOT EXISTS (SELECT 1 FROM collisions c WHERE c.case_id = i.case_id)",
    "collisions without case_ids": "SELECT count(*) FROM collisions c WHERE NOT EXISTS (SELECT 1 FROM case_ids i WHERE i.case_id = c.case_id)",
}


ALLOWED_LOSSES: dict[str, str] = {}  # "table.column": reason. Empty: no conversion loss is accepted.


def assess(columns_report: dict, keys: dict, aggregates: dict) -> list[str]:
    """Every problem that must block publishing the DuckDB copy."""
    problems = []
    for col, v in columns_report.items():
        lost = v["values_lost_to_cast"]
        if lost < 0:
            problems.append(f"{col}: DuckDB has {-lost} more non-NULL values than the source")
        elif lost > 0 and col not in ALLOWED_LOSSES:
            problems.append(f"{col}: {lost:,} source values became NULL in conversion")
    problems += [f"key check failed: {k} = {v}" for k, v in keys.items() if v]
    problems += [f"aggregate mismatch: {k}" for k, v in aggregates.items() if not v.get("match")]
    return problems


def duckdb_fingerprint(path: pathlib.Path) -> dict:
    st = path.stat()
    return {"bytes": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": sha256(path)}


def norm(rows):
    """Normalise NULL/empty labels so SQLite '' and DuckDB NULL compare equal."""
    out = {}
    for k, v in rows:
        key = "" if k is None else str(k)
        out[key] = out.get(key, 0) + int(v or 0)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", required=True)
    ap.add_argument("--duckdb", default=str(ROOT / "data" / "switrs.duckdb"))
    ap.add_argument("--out", default=str(ROOT / "data" / "dataset_meta.json"))
    args = ap.parse_args()
    src_path = pathlib.Path(args.sqlite).expanduser()

    print("fingerprinting source ...", flush=True)
    src_hash = sha256(src_path)
    src = sqlite3.connect(f"file:{src_path}?immutable=1", uri=True)
    dst = duckdb.connect(args.duckdb, read_only=True)
    print("validating source numeric domains ...", flush=True)
    source_problems = validate_source(src)

    columns_report = {}
    for table in TABLES:
        cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})")]
        print(f"scanning {table} ({len(cols)} columns) ...", flush=True)
        s_sel = ", ".join(f'sum("{c}" IS NOT NULL AND "{c}" <> \'\'), sum("{c}" = \'\')' for c in cols)
        s_vals = src.execute(f"SELECT {s_sel} FROM {table}").fetchone()
        d_sel = ", ".join(f'count("{c}")' for c in cols)
        d_vals = dst.execute(f"SELECT {d_sel} FROM {table}").fetchone()
        for i, c in enumerate(cols):
            src_nonempty, src_empty = int(s_vals[2 * i] or 0), int(s_vals[2 * i + 1] or 0)
            dst_nonnull = int(d_vals[i])
            lost = src_nonempty - dst_nonnull
            columns_report[f"{table}.{c}"] = {"source_nonempty": src_nonempty, "source_empty_strings": src_empty,
                                              "duckdb_nonnull": dst_nonnull, "values_lost_to_cast": lost}

    print("checking keys and aggregates ...", flush=True)
    keys = {name: int(dst.execute(sql).fetchone()[0]) for name, sql in KEY_CHECKS.items()}
    aggregates = {}
    for name, sql in AGGREGATES.items():
        a, b = norm(src.execute(sql).fetchall()) if name != "sum_killed_injured" else None, None
        if name == "sum_killed_injured":
            s_row, d_row = src.execute(sql).fetchone(), dst.execute(sql).fetchone()
            ok = [int(x or 0) for x in s_row] == [int(x or 0) for x in d_row]
            aggregates[name] = {"source": list(s_row), "duckdb": list(d_row), "match": ok}
        else:
            b = norm(dst.execute(sql).fetchall())
            ok = a == b
            aggregates[name] = {"match": ok, "diff": {k: [a.get(k), b.get(k)] for k in set(a) | set(b) if a.get(k) != b.get(k)}}

    print("comparing complete records by key ...", flush=True)
    records = {}
    for table in TABLES:
        try:
            records[table] = compare_records(src, dst, table)
        except (ValueError, ArithmeticError) as exc:
            records[table] = {"match": False, "error": str(exc)}
    problems = source_problems + assess(columns_report, keys, aggregates)
    problems += [f"record comparison failed: {table}" for table, result in records.items() if not result['match']]
    lossy = {k: v for k, v in columns_report.items() if v["values_lost_to_cast"] > 0}
    rows = {t: int(dst.execute(f"SELECT count(*) FROM {t}").fetchone()[0]) for t in TABLES}
    coverage = dst.execute("SELECT min(collision_date), max(collision_date) FROM collisions").fetchone()
    meta = {
        "status": "ok" if not problems else "failed",
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": {"path": str(src_path), "bytes": src_path.stat().st_size, "sha256": src_hash},
        "dataset_version": f"switrs-v6-{src_hash[:12]}",
        "transform_version": TRANSFORM_VERSION,
        "duckdb_file": duckdb_fingerprint(pathlib.Path(args.duckdb)),
        "allowed_losses": ALLOWED_LOSSES,
        "rows": rows,
        "coverage": {"min_collision_date": str(coverage[0]), "max_collision_date": str(coverage[1])},
        "key_checks": keys,
        "record_comparison": records,
        "integrity_version": INTEGRITY_VERSION,
        "source_validation": {"match": not source_problems, "problems": source_problems},
        "aggregates": aggregates,
        "columns_with_cast_losses": lossy,
        "problems": problems,
    }
    pathlib.Path(args.out).write_text(json.dumps(meta, indent=1, default=str))
    (ROOT / "data" / "column_reconciliation.json").write_text(json.dumps(columns_report, indent=1))
    print(json.dumps({k: meta[k] for k in ("status", "dataset_version", "rows", "coverage", "key_checks", "problems")}, indent=1))
    print(f"columns with cast losses: {len(lossy)}")
    for k, v in sorted(lossy.items(), key=lambda kv: -kv[1]["values_lost_to_cast"])[:15]:
        print(f"  {k}: {v['values_lost_to_cast']:,} of {v['source_nonempty']:,}")
    sys.exit(0 if not problems else 1)


if __name__ == "__main__":
    main()
