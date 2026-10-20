"""Queries must be able to spill when the deployed database directory is read-only."""
import hashlib
from pathlib import Path

import duckdb
import pytest

from switrs_agent.db import Database


def test_read_only_database_can_spill_without_writing_beside_database(tmp_path, monkeypatch):
    path = tmp_path / 'snapshot.duckdb'
    with duckdb.connect(str(path)) as con:
        con.execute('CREATE TABLE marker AS SELECT 1 AS n')
    original = hashlib.sha256(path.read_bytes()).hexdigest()
    # Block DuckDB's default spill path even on systems where chmod is ineffective.
    Path(str(path) + '.tmp').write_text('deployment directory is unavailable')
    monkeypatch.setenv('SWITRS_MEMORY_LIMIT', '32MB')
    monkeypatch.setenv('SWITRS_THREADS', '1')
    query = ("SELECT i, repeat(md5(i::VARCHAR), 4) AS payload "
             "FROM range(200000) t(i) ORDER BY payload")
    # Confirm this workload actually needs disk, rather than merely checking a setting.
    with duckdb.connect(str(path), read_only=True) as baseline:
        baseline.execute("SET memory_limit = '32MB'")
        baseline.execute('SET threads = 1')
        with pytest.raises(duckdb.IOException, match='Failed to create directory'):
            baseline.execute(query)
    db = Database(path, max_rows=1)
    try:
        result = db.run(query)
        assert result.row_count == 1 and result.truncated
        # Shared connections retain the same writable spill directory and security limits.
        second = Database(path)
        try:
            assert second.run('SELECT * FROM marker').rows == [[1]]
            assert second.con.execute("SELECT current_setting('temp_directory')").fetchone() == (
                db.con.execute("SELECT current_setting('temp_directory')").fetchone())
            with pytest.raises(duckdb.Error):
                second.con.execute('CREATE TABLE forbidden (n INTEGER)')
            with pytest.raises(duckdb.Error):
                second.con.execute('SET enable_external_access = true')
        finally:
            second.con.close()
    finally:
        db.con.close()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original
