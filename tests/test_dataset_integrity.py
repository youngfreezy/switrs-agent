"""File-backed regression fixtures for the five certification review findings."""
import json
import os
import shutil
import sqlite3
import sys

import duckdb
import pytest

from scripts import build_duckdb, reconcile
from switrs_agent import tools


@pytest.fixture
def snapshot(tmp_path):
    def make(name, source_sql='', copy_sql=''):
        folder = tmp_path / name
        folder.mkdir()
        source, target = folder / 'source.sqlite', folder / 'copy.duckdb'
        with sqlite3.connect(source) as src:
            src.executescript("""
                CREATE TABLE collisions(case_id VARCHAR, county_location VARCHAR,
                  collision_date VARCHAR, collision_severity VARCHAR, killed_victims INTEGER,
                  injured_victims INTEGER, motorcycle_collision INTEGER);
                INSERT INTO collisions VALUES
                  ('A','orange','2019-01-01','fatal',2,0,1),
                  ('B','los angeles','2020-12-31','fatal',1,0,0),
                  ('C','los angeles','2020-04-01','property damage only',0,0,0);
                CREATE TABLE parties(case_id VARCHAR,party_number INTEGER);
                INSERT INTO parties VALUES ('A',1),('B',1),('C',1);
                CREATE TABLE victims(id INTEGER,case_id VARCHAR,party_number INTEGER,victim_degree_of_injury VARCHAR);
                INSERT INTO victims VALUES (1,'A',1,'killed'),(2,'A',1,'killed'),(3,'B',1,'killed');
                CREATE TABLE case_ids(case_id VARCHAR);
                INSERT INTO case_ids VALUES ('A'),('B'),('C');
            """)
            src.executescript(source_sql)
            with duckdb.connect(str(target)) as dst:
                for table in reconcile.TABLES:
                    cols = list(src.execute(f'PRAGMA table_info({table})'))
                    dst.execute('CREATE TEMP TABLE raw (' + ','.join(f'"{c[1]}" VARCHAR' for c in cols) + ')')
                    rows = src.execute(f'SELECT * FROM {table}').fetchall()
                    if rows:
                        dst.executemany('INSERT INTO raw VALUES (' + ','.join('?' for _ in cols) + ')',
                                        [[None if x is None else str(x) for x in row] for row in rows])
                    expressions = ','.join(build_duckdb._cast(c[1], c[2].upper()) for c in cols)
                    dst.execute(f'CREATE TABLE {table} AS SELECT {expressions} FROM raw')
                    dst.execute('DROP TABLE raw')
                if copy_sql:
                    dst.execute(copy_sql)
        return source, target, folder / 'manifest.json'
    return make


def reconcile_pair(monkeypatch, source, target, meta):
    monkeypatch.setattr(reconcile, 'ROOT', meta.parent)
    (meta.parent / 'data').mkdir(exist_ok=True)
    monkeypatch.setattr(sys, 'argv', ['reconcile', '--sqlite', str(source), '--duckdb', str(target), '--out', str(meta)])
    with pytest.raises(SystemExit) as exit:
        reconcile.main()
    result = json.loads(meta.read_text())
    assert exit.value.code == (0 if result['status'] == 'ok' else 1)
    monkeypatch.setattr(tools, 'DATASET_META', meta)
    monkeypatch.setenv('SWITRS_DB', str(target))
    monkeypatch.setattr(tools, 'TRACE_DIR', meta.parent / 'traces')
    return result


def test_r3_substitution_with_same_size_and_timestamp_is_rejected(snapshot, monkeypatch):
    source, target, meta = snapshot('approved')
    assert reconcile_pair(monkeypatch, source, target, meta)['status'] == 'ok'
    original = target.stat()
    _, replacement, _ = snapshot('replacement', source_sql='UPDATE collisions SET killed_victims=0;')
    assert replacement.stat().st_size == original.st_size
    shutil.copyfile(replacement, target)
    os.utime(target, ns=(original.st_atime_ns, original.st_mtime_ns))
    with pytest.raises(RuntimeError, match='content|SHA|digest'):
        tools.load_dataset_meta()


def test_unchanged_reconciled_copy_still_loads(snapshot, monkeypatch):
    source, target, meta = snapshot('valid')
    expected = reconcile_pair(monkeypatch, source, target, meta)
    assert tools.load_dataset_meta() == expected


@pytest.mark.parametrize('copy_sql', [
    "UPDATE collisions SET county_location=CASE WHEN case_id='A' THEN 'los angeles' WHEN case_id='C' THEN 'orange' ELSE county_location END",
    "UPDATE collisions SET motorcycle_collision=1-motorcycle_collision",
    "UPDATE victims SET party_number=1, case_id=CASE WHEN id=1 THEN 'B' ELSE case_id END",
])
def test_r1_changed_values_cannot_hide_behind_matching_marginals(snapshot, monkeypatch, copy_sql):
    source, target, meta = snapshot('permuted', copy_sql=copy_sql)
    result = reconcile_pair(monkeypatch, source, target, meta)
    assert result['status'] == 'failed'
    assert any('record' in p for p in result['problems'])
    with pytest.raises(RuntimeError, match='reconciliation failed'):
        tools.load_dataset_meta()


def test_record_order_does_not_change_reconciliation(snapshot, monkeypatch):
    source, target, meta = snapshot('reordered', copy_sql='''
        CREATE TABLE reordered AS SELECT * FROM collisions ORDER BY case_id DESC;
        DROP TABLE collisions;
        ALTER TABLE reordered RENAME TO collisions;
    ''')
    assert reconcile_pair(monkeypatch, source, target, meta)['status'] == 'ok'
