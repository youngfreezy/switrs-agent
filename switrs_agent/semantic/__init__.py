"""Load the semantic layer and render it as the agent's grounding context."""

from __future__ import annotations

import json
import pathlib
import copy
from functools import lru_cache

import yaml

from ..coverage import statement as coverage_statement

HERE = pathlib.Path(__file__).resolve().parent
MAX_LISTED_VALUES = 40


@lru_cache(maxsize=1)
def load() -> tuple[dict, dict]:
    schema = yaml.safe_load((HERE / "schema.yaml").read_text())
    catalog_path = HERE / "catalog.json"
    catalog = json.loads(catalog_path.read_text()) if catalog_path.exists() else {}
    return schema, catalog


def describe_column(table: str, column: str, statistics: bool = True) -> str:
    schema, catalog = load()
    meta = catalog.get(table, {}).get("columns", {}).get(column, {})
    note = schema.get("columns", {}).get(table, {}).get(column)
    parts = [f"{column} {meta.get('type', '?')}"]
    if note:
        parts.append(f"-- {note}")
    if not statistics:
        if 'values' in meta:
            parts.append('catalog categories: ' + ', '.join(repr(v) for v in list(meta['values'])[:MAX_LISTED_VALUES]))
        return ' '.join(parts)
    if meta:
        parts.append(f"[null {meta['null_pct']}%]")
        if "values" in meta:
            vals = list(meta["values"].items())
            shown = ", ".join(f"'{v}' ({n:,})" for v, n in vals[:MAX_LISTED_VALUES])
            more = f", +{len(vals) - MAX_LISTED_VALUES} more" if len(vals) > MAX_LISTED_VALUES else ""
            parts.append(f"values: {shown}{more}")
        elif "top_values" in meta:
            parts.append("e.g. " + ", ".join(f"'{v}'" for v in list(meta["top_values"])[:3]))
        elif "min" in meta:
            parts.append(f"range {meta['min']} .. {meta['max']}")
    return " ".join(parts)


def render_context(dataset: dict | None = None) -> str:
    """Full grounding context: tables, joins, every column with meaning and real values, metrics,
    caveats, and what the data does not contain. Deterministic so it caches well."""
    schema, catalog = load()
    if dataset is not None:
        schema = copy.deepcopy(schema)
        schema['dataset']['coverage'] = coverage_statement(dataset)
        schema['caveats']['coverage_window']['text'] = coverage_statement(dataset)
        schema['not_in_data'][-1] = {'concept': 'dates outside this snapshot', 'note': coverage_statement(dataset)}
    ds = schema["dataset"]
    lines = [
        f"# Dataset: {ds['name']}",
        f"Coverage: {ds['coverage']}. {ds['grain']}",
        ds["source_note"].strip(),
        "",
        "## Tables and joins",
    ]
    for name, t in schema["tables"].items():
        rows = dataset.get('rows', {}).get(name) if dataset is not None else catalog.get(name, {}).get('rows')
        lines.append(f"- {name}" + (f" ({rows:,} rows)" if rows else "") + f": {t['description'].strip()}")
    lines += ["Joins:"] + [f"- {j}" for j in schema["joins"]]
    lines += ["", "## Columns (type, meaning, null share, real values)"]
    for table in schema["tables"]:
        lines.append(f"### {table}")
        cols = catalog.get(table, {}).get("columns") or schema.get("columns", {}).get(table, {})
        for column in cols:
            lines.append("- " + describe_column(table, column, statistics=dataset is None))
    lines += ["", "## Named metrics (use these definitions)"]
    for name, m in schema["metrics"].items():
        lines.append(f"- {name}: {m['sql']}" + (f"  -- {m['note']}" if m.get("note") else ""))
    lines += ["", "## Caveats (include each one that applies to your queries)"]
    for cid, c in schema["caveats"].items():
        lines.append(f"- [{cid}] when {c['trigger']}: {c['text']}")
    lines += ["", "## Not in the data (say so plainly when a question needs one of these)"]
    for item in schema["not_in_data"]:
        lines.append(f"- {item['concept']}: {item['note']}")
    return "\n".join(lines)
