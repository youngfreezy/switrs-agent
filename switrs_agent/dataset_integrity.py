"""Integrity checks shared by dataset preparation and serving."""
import hashlib
from datetime import date, time
from decimal import Decimal
from itertools import zip_longest

KEYS = {'collisions': ('case_id',), 'parties': ('case_id', 'party_number'),
        'victims': ('id',), 'case_ids': ('case_id',)}
INTEGRITY_VERSION = 2
BINARY_COLUMNS = {'motorcycle_collision', 'bicycle_collision', 'pedestrian_collision',
                  'truck_collision', 'alcohol_involved', 'at_fault', 'cellphone_in_use'}
COUNT_COLUMNS = {'killed_victims', 'injured_victims', 'party_count',
                 'party_number_killed', 'party_number_injured'}


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def validate_source(source, tables=tuple(KEYS)):
    """Inspect SQLite values before any lossy integer conversion can occur.

    SQLite INTEGER affinity stores valid numeric integers as integers already.
    Empty strings and NULL remain the documented missing-value normalization.
    One aggregate scan per table checks all numeric fields together.
    """
    problems = []
    for table in tables:
        checks = {}
        for _, name, declaration, *_ in source.execute(f'PRAGMA table_info({quoted(table)})'):
            col = quoted(name)
            invalid = []
            if declaration.upper().startswith('INT'):
                invalid.append(f"typeof({col}) <> 'integer'")
            elif declaration.upper() == 'REAL':
                invalid.append(f"typeof({col}) NOT IN ('integer','real') OR ABS({col}) > 1.7976931348623157e308")
            if name in BINARY_COLUMNS:
                invalid.append(f"typeof({col}) NOT IN ('integer','real') OR {col} NOT IN (0,1)")
            if name in COUNT_COLUMNS:
                invalid.append(f'{col} < 0')
            if invalid:
                checks[name] = f"SUM(CASE WHEN {col} IS NOT NULL AND {col} <> '' AND ({' OR '.join(invalid)}) THEN 1 ELSE 0 END)"
        if checks:
            counts = source.execute(f'SELECT {",".join(checks.values())} FROM {quoted(table)}').fetchone()
            problems.extend(f'{table}.{name}: {count} invalid source values'
                            for name, count in zip(checks, counts) if count)
    return problems


def canonical(value, name, declaration):
    """Independent source representation; never round integer-affinity values."""
    if value is None or value == '':
        return None
    if name in ('collision_date', 'process_date'):
        return date.fromisoformat(str(value))
    if name == 'collision_time':
        return time.fromisoformat(str(value))
    if declaration.upper().startswith('INT'):
        return Decimal(str(value))
    if declaration.upper() == 'REAL':
        return float(value)
    return str(value)


def batches(cursor):
    while rows := cursor.fetchmany(2048):
        yield from rows


def compare_records(source, target, table):
    """Compare every column in key order with bounded Python memory.

    Both engines sort textual keys with binary collation and NULLs first. Counts,
    duplicate/null keys, and values must match; physical row order is irrelevant.
    """
    columns = [(c[1], c[2]) for c in source.execute(f'PRAGMA table_info({quoted(table)})')]
    names = [c[0] for c in columns]
    keys = KEYS[table]
    target_names = [c[1] for c in target.execute(f'PRAGMA table_info({quoted(table)})').fetchall()]
    if set(names) != set(target_names) or not set(keys) <= set(names):
        return {'match': False, 'error': 'Missing keys or mismatched column sets.'}
    key_sql = ','.join(map(quoted, keys))
    missing = ' OR '.join(f'{quoted(k)} IS NULL' for k in keys)
    for engine in (source, target):
        if engine.execute(f'SELECT COUNT(*) FROM {quoted(table)} WHERE {missing}').fetchone()[0]:
            return {'match': False, 'error': 'NULL record key.'}
        duplicates = engine.execute(f'SELECT COUNT(*) FROM (SELECT {key_sql} FROM {quoted(table)} '
                                    f'GROUP BY {key_sql} HAVING COUNT(*) > 1)').fetchone()[0]
        if duplicates:
            return {'match': False, 'error': 'Duplicate record keys.'}
    projection = ','.join(map(quoted, names))
    source_order = ','.join(f'CAST({quoted(k)} AS TEXT) COLLATE BINARY' for k in keys)
    target_order = ','.join(f'CAST({quoted(k)} AS VARCHAR) NULLS FIRST' for k in keys)
    left = source.execute(f'SELECT {projection} FROM {quoted(table)} ORDER BY {source_order}')
    right = target.execute(f'SELECT {projection} FROM {quoted(table)} ORDER BY {target_order}')
    mismatches, compared, samples = 0, 0, []
    for a, b in zip_longest(batches(left), batches(right)):
        compared += 1
        expected = tuple(canonical(v, *column) for v, column in zip(a, columns)) if a is not None else None
        if expected != b:
            mismatches += 1
            if len(samples) < 5:
                samples.append({'position': compared, 'source_key': [a[names.index(k)] for k in keys] if a else None})
    return {'match': mismatches == 0, 'compared': compared, 'mismatched_records': mismatches, 'samples': samples}


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for block in iter(lambda: source.read(8 << 20), b''):
            digest.update(block)
    return digest.hexdigest()
