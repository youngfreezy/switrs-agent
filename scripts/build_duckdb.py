"""Convert the SWITRS SQLite file to DuckDB and generate the value catalog.

usage: uv run python scripts/build_duckdb.py --sqlite PATH --out data/switrs.duckdb
"""

import argparse
import json
import pathlib
import sys
import sqlite3

import duckdb

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from switrs_agent.dataset_integrity import validate_source

TABLES = ["collisions", "parties", "victims", "case_ids"]
EXPECTED_ROWS = {
    "collisions": 9_424_334,
    "parties": 18_669_166,
    "victims": 9_639_334,
    "case_ids": 9_424_334,
}
CATEGORY_MAX = 80  # columns with at most this many distinct values get a full value list


def convert(con: duckdb.DuckDBPyConnection, sqlite_path: str) -> None:
    with sqlite3.connect(pathlib.Path(sqlite_path).resolve().as_uri() + '?immutable=1', uri=True) as source:
        problems = validate_source(source)
    if problems:
        raise ValueError('Source validation failed: ' + '; '.join(problems))
    con.execute("INSTALL sqlite; LOAD sqlite;")
    # Some INT columns hold stray text codes, so read everything as text and cast below.
    con.execute("SET sqlite_all_varchar = true;")
    for table in TABLES:
        print(f"copying {table} ...", flush=True)
        select = ", ".join(_cast(name, decl) for name, decl in _sqlite_columns(sqlite_path, table))
        con.execute(
            f"CREATE OR REPLACE TABLE {table} AS SELECT {select} FROM sqlite_scan(?, ?)",
            [sqlite_path, table],
        )


def _sqlite_columns(sqlite_path: str, table: str) -> list[tuple[str, str]]:
    import sqlite3

    uri = f"file:{sqlite_path}?immutable=1"
    with sqlite3.connect(uri, uri=True) as src:
        return [(r[1], (r[2] or "").upper()) for r in src.execute(f"PRAGMA table_info({table})")]


def _cast(name: str, decl: str) -> str:
    col = f'"{name}"'
    if name == "collision_date":
        return f"TRY_CAST({col} AS DATE) AS {col}"
    if name == "process_date":
        return f"TRY_CAST({col} AS DATE) AS {col}"
    if name == "collision_time":
        return f"TRY_CAST({col} AS TIME) AS {col}"
    if decl.startswith("INT"):
        return f"TRY_CAST({col} AS BIGINT) AS {col}"
    if decl == "REAL":
        return f"TRY_CAST({col} AS DOUBLE) AS {col}"
    return f"NULLIF({col}, '') AS {col}"


def check_counts(con: duckdb.DuckDBPyConnection) -> None:
    for table, want in EXPECTED_ROWS.items():
        got = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        status = "ok" if got == want else "MISMATCH"
        print(f"{table}: {got:,} rows (expected {want:,}) {status}")
        if got != want:
            sys.exit(1)


def build_catalog(con: duckdb.DuckDBPyConnection) -> dict:
    catalog: dict = {}
    for table in TABLES:
        cols = con.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = ? ORDER BY ordinal_position",
            [table],
        ).fetchall()
        total = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        catalog[table] = {"rows": total, "columns": {}}
        for name, dtype in cols:
            col = f'"{name}"'
            distinct, nulls = con.execute(
                f"SELECT approx_count_distinct({col}), count(*) - count({col}) FROM {table}"
            ).fetchone()
            entry = {"type": dtype, "null_pct": round(100 * nulls / total, 2), "distinct_approx": distinct}
            if dtype == "VARCHAR" and distinct <= CATEGORY_MAX:
                rows = con.execute(
                    f"SELECT {col}, count(*) FROM {table} WHERE {col} IS NOT NULL "
                    f"GROUP BY 1 ORDER BY 2 DESC"
                ).fetchall()
                entry["values"] = {v: n for v, n in rows}
            elif dtype == "VARCHAR":
                rows = con.execute(
                    f"SELECT {col}, count(*) FROM {table} WHERE {col} IS NOT NULL "
                    f"GROUP BY 1 ORDER BY 2 DESC LIMIT 5"
                ).fetchall()
                entry["top_values"] = {v: n for v, n in rows}
            else:
                lo, hi = con.execute(f"SELECT min({col}), max({col}) FROM {table}").fetchone()
                entry["min"], entry["max"] = str(lo), str(hi)
            catalog[table]["columns"][name] = entry
        print(f"catalogued {table}", flush=True)
    return catalog


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", required=True)
    ap.add_argument("--out", default="data/switrs.duckdb")
    ap.add_argument("--catalog", default="switrs_agent/semantic/catalog.json")
    ap.add_argument("--catalog-only", action="store_true")
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(out))
    if not args.catalog_only:
        convert(con, str(pathlib.Path(args.sqlite).expanduser()))
    check_counts(con)
    catalog = build_catalog(con)
    pathlib.Path(args.catalog).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(args.catalog).write_text(json.dumps(catalog, indent=1, default=str))
    con.close()
    print(f"wrote {out} and {args.catalog}")


if __name__ == "__main__":
    main()
