"""Read-only DuckDB access with a timeout and a row cap."""

from __future__ import annotations

import os
import pathlib
import tempfile
import threading
import time
from dataclasses import dataclass, field
from functools import lru_cache

import duckdb

DEFAULT_DB = pathlib.Path(__file__).resolve().parent.parent / "data" / "switrs.duckdb"


@lru_cache(maxsize=None)
def _spill_directory(database_path: str) -> tempfile.TemporaryDirectory:
    # Connections share a DuckDB instance. Keep its private directory alive until process exit,
    # rather than removing spill files when just one of several connections closes.
    return tempfile.TemporaryDirectory(prefix="switrs-spill-")


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list]
    row_count: int
    truncated: bool
    elapsed_ms: int
    warnings: list[str] = field(default_factory=list)


class Database:
    def __init__(self, path: str | os.PathLike | None = None, timeout_s: float = 60.0, max_rows: int = 200,
                 max_bytes: int = 150_000, max_cell_chars: int = 300):
        self.path = str(path or os.environ.get("SWITRS_DB", DEFAULT_DB))
        self.timeout_s = timeout_s
        self.max_rows = max_rows
        self.max_bytes = max_bytes
        self.max_cell_chars = max_cell_chars
        # read_only blocks writes at the engine level; external access off blocks reading other
        # files; memory and temp-disk caps bound what one query can consume. Lock last.
        self.con = duckdb.connect(self.path, read_only=True)
        locked = self.con.execute("SELECT current_setting('lock_configuration')").fetchone()[0]
        if not locked:
            # Deployment files can be read-only; DuckDB's adjacent .tmp default cannot work there.
            spill = _spill_directory(str(pathlib.Path(self.path).resolve()))
            self.con.execute("SET temp_directory = ?", [spill.name])
            self.con.execute(f"SET memory_limit = '{os.environ.get('SWITRS_MEMORY_LIMIT', '4GB')}'")
            self.con.execute(f"SET max_temp_directory_size = '{os.environ.get('SWITRS_TEMP_LIMIT', '4GB')}'")
            self.con.execute(f"SET threads = {int(os.environ.get('SWITRS_THREADS', '4'))}")
            self.con.execute("SET enable_external_access = false")
            self.con.execute("SET lock_configuration = true")
        # A second connection in the same process shares the locked instance; confirm its settings.
        if self.con.execute("SELECT current_setting('enable_external_access')").fetchone()[0]:
            raise RuntimeError("DuckDB instance allows external access; refusing to run model SQL on it")
        self._lock = threading.Lock()

    def run(self, sql: str, explain_first: bool = False) -> QueryResult:
        """One deadline covers planning (EXPLAIN), execution and fetching."""
        with self._lock:
            timer = threading.Timer(self.timeout_s, self.con.interrupt)
            start = time.monotonic()
            timer.start()
            try:
                if explain_first:
                    self.con.execute(f"EXPLAIN {sql}")
                cur = self.con.execute(sql)
                columns = [d[0] for d in cur.description]
                fetched = cur.fetchmany(self.max_rows + 1)
            except duckdb.InterruptException as exc:
                raise TimeoutError(f"query exceeded {self.timeout_s:.0f}s and was cancelled") from exc
            finally:
                timer.cancel()
            elapsed = int((time.monotonic() - start) * 1000)
        truncated = len(fetched) > self.max_rows
        rows, size = [], 0
        for r in fetched[: self.max_rows]:
            row = [_plain(v, self.max_cell_chars) for v in r]
            size += len(repr(row))
            if size > self.max_bytes:
                truncated = True
                break
            rows.append(row)
        return QueryResult(columns, rows, len(rows), truncated, elapsed)

    def table_columns(self) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        with self._lock:
            for table, column, dtype in self.con.execute(
                "SELECT table_name, column_name, data_type FROM information_schema.columns "
                "ORDER BY table_name, ordinal_position"
            ).fetchall():
                out.setdefault(table, {})[column] = dtype
        return out


def _plain(v, max_chars: int = 300):
    """Make values JSON-friendly without losing precision that matters for answers."""
    if isinstance(v, str):
        return v if len(v) <= max_chars else v[:max_chars] + "...[truncated]"
    if v is None or isinstance(v, (int, float, bool)):
        return v
    s = str(v)
    return s if len(s) <= max_chars else s[:max_chars] + "...[truncated]"
