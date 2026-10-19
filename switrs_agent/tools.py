"""Harness-neutral tools. Plain functions with JSON-serialisable inputs and outputs.

The MCP server, a hosted API, or any agent loop can wrap these. Each instance keeps a query log
so answers can cite query ids and be checked against the rows those queries returned.
"""

from __future__ import annotations

import html
import json
import os
import pathlib
import re
import secrets
import time
from dataclasses import asdict

from . import answerability, claims, grounding, guard, semantic
from .db import Database

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "outputs" / "answers"
TRACE_DIR = ROOT / "outputs" / "traces"
QUESTION_CUES = re.compile(r"\b(bias|cause|caused|because|effect|effective|work|works|prove|disprove|chang\w*|compare|versus|vs)\b", re.I)
YEAR_IN_SQL = re.compile(r"(?<![\d.])(20[0-2]\d)(?![\d])")
DATASET_META = ROOT / "data" / "dataset_meta.json"
MAX_QUERIES_PER_TURN = 25
MAX_QUERIES_PER_SESSION = 200
CHECK_LABEL = ("Claims bound to query cells: each number is the stated operation on the cited result cells, "
               "with the stated entity and direction. This does not prove a query measures what the question "
               "asks. Review the query plans and SQL.")


def load_dataset_meta() -> dict:
    """The reconciliation record. Tools refuse to run on a copy that has not passed it."""
    if not DATASET_META.exists():
        raise RuntimeError("data/dataset_meta.json is missing. Run scripts/reconcile.py first.")
    meta = json.loads(DATASET_META.read_text())
    if meta.get("status") != "ok":
        raise RuntimeError(f"Dataset reconciliation failed: {meta.get('problems')}")
    db_path = pathlib.Path(os.environ.get("SWITRS_DB", ROOT / "data" / "switrs.duckdb"))
    recorded = meta.get("duckdb_file") or {}
    st = db_path.stat()
    if recorded.get("bytes") != st.st_size or recorded.get("mtime_ns") != st.st_mtime_ns:
        raise RuntimeError(f"{db_path} changed after reconciliation. Run scripts/reconcile.py again.")
    return meta


class Toolkit:
    def __init__(self, db: Database | None = None, session_id: str | None = None):
        self.db = db or Database()
        self.schema_cols = self.db.table_columns()
        self.queries: dict[str, dict] = {}
        self._n = 0
        self.run_tag = secrets.token_hex(2)  # query ids stay unique across turns that share a trace
        self.session_id = session_id or time.strftime("%Y%m%d-%H%M%S")
        self.trace: list[dict] = []
        TRACE_DIR.mkdir(parents=True, exist_ok=True)
        self.trace_path = TRACE_DIR / f"{self.session_id}.jsonl"
        self.dataset = load_dataset_meta()
        self.turns: dict[str, dict] = {}
        self.current_turn: str | None = None

    def _log(self, tool: str, args: dict, result) -> None:
        """Append one tool call to the session trace (memory and JSONL on disk)."""
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tool": tool, "args": args, "result": result}
        self.trace.append(entry)
        with self.trace_path.open("a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def get_trace(self) -> dict:
        """Every tool call in this session, in order, with arguments and results."""
        return {"session_id": self.session_id, "trace_file": str(self.trace_path), "calls": self.trace}

    # ---- context -------------------------------------------------------------------------

    def get_context(self) -> str:
        """The grounding context: tables, joins, columns with real values, metrics, caveats,
        and concepts the data does not contain."""
        return semantic.render_context()

    def describe_table(self, table: str) -> dict:
        table = table.lower()
        if table not in self.schema_cols:
            return {"error": f"Unknown table '{table}'. Tables: {', '.join(sorted(self.schema_cols))}."}
        return {
            "table": table,
            "columns": [semantic.describe_column(table, c) for c in self.schema_cols[table]],
        }

    def distinct_values(self, table: str, column: str, contains: str | None = None, limit: int = 50) -> dict:
        table, column = table.lower(), column.lower()
        if column not in {c.lower() for c in self.schema_cols.get(table, {})}:
            return {"error": f"Column '{column}' is not in table '{table}'. Use describe_table."}
        limit = max(1, min(int(limit), 200))
        where = f'WHERE "{column}" IS NOT NULL'
        params_sql = ""
        if contains:
            safe = contains.replace("'", "''").lower()
            params_sql = f" AND lower(CAST(\"{column}\" AS VARCHAR)) LIKE '%{safe}%'"
        res = self.db.run(
            f'SELECT "{column}" AS value, count(*) AS n FROM {table} {where}{params_sql} '
            f"GROUP BY 1 ORDER BY 2 DESC LIMIT {limit}"
        )
        result = {"table": table, "column": column, "values": res.rows}
        self._log("distinct_values", {"table": table, "column": column, "contains": contains}, result)
        return result

    # ---- turns ---------------------------------------------------------------------------

    def begin_question(self, question: str, answerability: str, interpretation: str) -> dict:
        """Open a turn for one user question. Declare answerability (answerable, ambiguous,
        descriptive_only, partially_available, unavailable) and how you read the question. The
        server checks the question for concepts the data lacks and causal or exposure wording."""
        check = _answerability_check(question, answerability)
        if check["error"]:
            result = {"error": check["error"], "detected": check["detected"], "gaps": check["gaps"]}
            self._log("begin_question", {"question": question, "answerability": answerability,
                                         "interpretation": interpretation}, result)
            return result
        turn_id = f"t{len(self.turns) + 1}-{self.run_tag}"
        self.turns[turn_id] = {"question": question, "answerability": answerability, "interpretation": interpretation,
                               "detected": check["detected"], "gaps": check["gaps"],
                               "required_caveats": check["required_caveats"], "queries": []}
        self.current_turn = turn_id
        result = {"turn_id": turn_id, "detected": check["detected"], "gaps_to_state": check["gaps"],
                  "required_caveats": check["required_caveats"], "dataset_version": self.dataset["dataset_version"]}
        self._log("begin_question", {"question": question, "answerability": answerability,
                                     "interpretation": interpretation}, result)
        return result

    # ---- queries -------------------------------------------------------------------------

    def run_sql(self, sql: str, purpose: str, query_plan: str = "") -> dict:
        """Validate and run one read-only SELECT. `purpose` is one sentence naming the tables
        joined and why. `query_plan` is shown to the user. Both are logged."""
        result = self._run_sql(sql, purpose, query_plan)
        logged = {k: v for k, v in result.items() if k != "rows"}
        if "rows" in result:
            logged["rows_preview"] = result["rows"][:20]
        self._log("run_sql", {"sql": sql, "purpose": purpose, "query_plan": query_plan}, logged)
        return result

    def _run_sql(self, sql: str, purpose: str, query_plan: str) -> dict:
        if not self.current_turn:
            return {"error": "Call begin_question before running queries."}
        turn = self.turns[self.current_turn]
        if len(turn["queries"]) >= MAX_QUERIES_PER_TURN or self._n >= MAX_QUERIES_PER_SESSION:
            return {"error": f"Query budget reached ({MAX_QUERIES_PER_TURN} per question, {MAX_QUERIES_PER_SESSION} per session). "
                             "Answer with what you have and state what is missing."}
        try:
            report = guard.check(sql, self.schema_cols)
        except guard.GuardError as e:
            return {"error": str(e), "hint": "Fix the query and call run_sql again."}
        try:
            res = self.db.run(report.sql, explain_first=True)
        except TimeoutError as e:
            return {"error": str(e), "hint": "Narrow the query (filters, fewer joins) and retry."}
        except Exception as e:  # engine compile errors (types, bad functions) and runtime errors
            return {"error": f"Database rejected the query: {e}", "hint": "Fix the query and call run_sql again."}
        if len(report.data_derived) != len(res.columns):
            return {"error": "Output provenance does not match the returned columns. Use explicit, uniquely named columns."}
        self._n += 1
        qid = f"{self.run_tag}-q{self._n}"
        caveats = self._caveats_for(report)
        entry = {
            "query_id": qid,
            "purpose": purpose,
            "query_plan": query_plan,
            "sql": report.sql,
            "columns": res.columns,
            "rows": res.rows,
            "row_count": res.row_count,
            "truncated": res.truncated,
            "elapsed_ms": res.elapsed_ms,
            "warnings": report.warnings,
            "caveats": caveats,
            "data_derived": report.data_derived,
            "turn_id": self.current_turn,
            "dataset_version": self.dataset["dataset_version"],
        }
        self.queries[qid] = entry
        turn["queries"].append(qid)
        return entry

    def _caveats_for(self, report: guard.GuardReport) -> list[str]:
        cols, tables, sql = report.columns, report.tables, report.sql.lower()
        out = ["coverage_window", "reported_only"]
        if "collisions.collision_date" in cols:
            years = [int(y) for y in YEAR_IN_SQL.findall(report.sql)]
            if not years or max(years) >= 2021:
                out.append("partial_recent_months")
        if "helmet" in sql:
            out.append("helmet_codes_shared")
        if "victims.victim_degree_of_injury" in cols:
            out.append("injury_vocabulary")
        if "victims" in tables:
            out.append("victims_exclude_uninjured_drivers")
        if ("collisions.county_location" in cols or "collisions.county_city_location" in cols) and "group by" in sql:
            out.append("per_capita")
        return out

    # ---- answers -------------------------------------------------------------------------

    def submit_answer(self, turn_id: str, answer: str, claims_in: list[dict]) -> dict:
        """Final step for every question. Each claim names its text (verbatim in the answer), an op
        (value, ratio, percent_of, percent_change, difference, sum), the result cells it uses
        ({query_id, row, column}), the exact entity when the row is labelled, and the metric and
        population it measures. On success returns final_answer: the answer plus server-rendered
        gaps, caveats and an evidence table. Use one numeric assertion per claim, with the entity
        at the start of its clause. Present final_answer to the user unchanged."""
        result = self._submit(turn_id, answer, claims_in)
        self._log("submit_answer", {"turn_id": turn_id, "answer": answer, "claims": claims_in}, result)
        return result

    def _submit(self, turn_id: str, answer: str, claims_in: list[dict]) -> dict:
        turn = self.turns.get(turn_id)
        if not turn:
            return {"ok": False, "errors": [f"Unknown turn_id {turn_id!r}. Call begin_question first."]}
        check = claims.check_answer(answer, claims_in or [], self.queries, turn_id, turn["question"])
        if not check["ok"]:
            return {"ok": False, "errors": check["errors"],
                    "hint": "Fix each error (bind every number to a claim, match entity and direction) and submit again."}
        schema, _ = semantic.load()
        caveat_ids = list(dict.fromkeys(["coverage_window", "reported_only"] + turn["required_caveats"]
                                        + [c for q in turn["queries"] for c in self.queries[q]["caveats"]]))
        if QUESTION_CUES.search(turn["question"]) and "observational" not in caveat_ids:
            caveat_ids.append("observational")
        caveat_lines = [f"- {schema['caveats'][c]['text']}" for c in caveat_ids if c in schema["caveats"]]
        gap_lines = [f"- {g}" for g in turn["gaps"]]
        evidence = ["| Claim | How it was computed | Value |", "|---|---|---|"]
        for c in check["claims"]:
            cells = ", ".join(f"{x['query_id']} row {x['row']} {x['column']}" for x in c.get("cells", []))
            evidence.append(f"| {c['text'][:90].replace('|', '/')} | {c.get('op', 'value')}({cells}) | {_fmt_value(c['computed'])} |")
        parts = [answer.strip()]
        if gap_lines:
            parts.append("**Not in the data**\n" + "\n".join(gap_lines))
        parts.append("**Caveats**\n" + "\n".join(caveat_lines))
        if len(evidence) > 2:
            parts.append("**Evidence**\n" + "\n".join(evidence))
        parts.append(f"_Check: {CHECK_LABEL} Dataset {self.dataset['dataset_version']}, turn {turn_id}._")
        final = "\n\n".join(parts)
        turn["final_answer"] = final
        return {"ok": True, "final_answer": final, "claims_checked": len(check["claims"]),
                "caveats": caveat_ids, "gaps": turn["gaps"], "dataset_version": self.dataset["dataset_version"]}

    def render_answer(self, turn_id: str) -> dict:
        """Write the certified answer for a turn as a self-contained HTML page with its queries,
        query plans, results and the full trace. Only turns that passed submit_answer render."""
        turn = self.turns.get(turn_id)
        if not turn or not turn.get("final_answer"):
            return {"error": "Only an answer that passed submit_answer can be rendered."}
        page = _render_html(turn["question"], turn["final_answer"], [self.queries[q] for q in turn["queries"]], self.trace)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", turn["question"].lower()).strip("-")[:50] or "answer"
        path = OUTPUT_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}.html"
        path.write_text(page)
        result = {"path": str(path), "trace_file": str(self.trace_path)}
        self._log("render_answer", {"turn_id": turn_id}, result)
        return result


def _answerability_check(question: str, status: str) -> dict:
    return answerability.check_status(question, status)


def _render_html(question, answer, queries, trace=()) -> str:
    e = html.escape
    def table(q):
        head = "".join(f"<th>{e(c)}</th>" for c in q["columns"])
        body = "".join("<tr>" + "".join(f"<td>{e(_fmt(v))}</td>" for v in r) + "</tr>" for r in q["rows"][:50])
        more = f"<p class=note>{q['row_count']} rows shown of a capped result.</p>" if q["truncated"] else ""
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>{more}"
    paras = "".join(f"<pre class=answer>{e(p)}</pre>" for p in answer.split("\n\n") if p.strip())
    status = "Passed submit_answer: every number is bound to query cells. Review the query plans below."
    q_html = "".join(
        f"<details><summary>{e(q['query_id'])}: {e(q['purpose'])} "
        f"<span class=note>({q['elapsed_ms']} ms)</span></summary>"
        + (f"<p class=reason><b>Query plan.</b> {e(q.get('query_plan') or '')}</p>" if q.get('query_plan') else "")
        + "".join(f"<p class=warn>{e(w)}</p>" for w in q['warnings'])
        + f"<pre>{e(q['sql'])}</pre>{table(q)}</details>"
        for q in queries)
    t_html = "".join(
        f"<li><code>{e(t['ts'][11:])}</code> <b>{e(t['tool'])}</b> "
        + (f"<span class=bad>rejected: {e(t['result'].get('error', ''))}</span>" if isinstance(t['result'], dict) and t['result'].get('error')
           else f"<span class=note>{e(t['result'].get('query_id', '') if isinstance(t['result'], dict) else '')}</span>")
        + (f"<br><span class=note>{e(t['args'].get('query_plan') or t['args'].get('purpose') or '')}</span>" if isinstance(t['args'], dict) else "")
        + (f"<pre>{e(t['args']['sql'])}</pre>" if isinstance(t['args'], dict) and t['args'].get('sql') else "")
        + "</li>"
        for t in trace)
    return f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>{e(question)}</title><style>
:root{{color-scheme:light dark;--fg:#1d1d1f;--bg:#fff;--mut:#666;--line:#ddd;--ok:#1a7f37;--bad:#b42318;--gap:#9a6700}}
@media(prefers-color-scheme:dark){{:root{{--fg:#eee;--bg:#161618;--mut:#999;--line:#333}}}}
body{{font:16px/1.55 system-ui,sans-serif;color:var(--fg);background:var(--bg);max-width:860px;margin:2rem auto;padding:0 1rem}}
h1{{font-size:1.35rem}}h2{{font-size:1rem;margin-top:1.6rem;text-transform:uppercase;letter-spacing:.04em;color:var(--mut)}}
.status{{padding:.5rem .75rem;border-left:4px solid var(--ok)}}.answer{{background:none;padding:0;font:inherit;white-space:pre-wrap}}
.gaps li{{color:var(--gap)}}pre{{white-space:pre-wrap;background:rgba(127,127,127,.12);padding:.75rem;border-radius:6px;font-size:13px}}
table{{border-collapse:collapse;font-size:13px;margin:.5rem 0;display:block;overflow-x:auto}}th,td{{border:1px solid var(--line);padding:3px 8px;text-align:left}}
details{{margin:.5rem 0}}.reason{{font-size:14px}}.warn,.bad{{color:var(--bad);font-size:13px}}.trace li{{margin:.4rem 0}}summary{{cursor:pointer}}.note{{color:var(--mut);font-size:13px}}
</style></head><body>
<h1>{e(question)}</h1>
<div class=status>{e(status)}</div>
{paras}
<h2>Queries</h2>{q_html}
{f'<h2>Full tool trace ({len(trace)} calls)</h2><ol class=trace>{t_html}</ol>' if trace else ''}
<p class=note>Source: SWITRS collision records 2001-01-01 to 2021-06-03 (Kaggle alexgude v6).</p>
</body></html>"""


def _fmt_value(v: float) -> str:
    if abs(v) >= 1000 and float(v).is_integer():
        return f"{int(v):,}"
    return f"{v:,.4f}".rstrip("0").rstrip(".")


def _fmt(v):
    if isinstance(v, float):
        return f"{v:,.4g}" if abs(v) < 1000 else f"{v:,.0f}"
    if isinstance(v, int):
        return f"{v:,}"
    return "" if v is None else str(v)


def dumps(obj) -> str:
    return json.dumps(obj, default=str)
