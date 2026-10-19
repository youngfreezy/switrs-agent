"""Render a session trace (outputs/traces/<session>.jsonl) as a self-contained HTML review page.

usage: uv run python scripts/render_trace.py outputs/traces/<session>.jsonl [--answer-file answer.txt] [--out page.html]
"""

import argparse
import html
import json
import pathlib

CSS = """
:root{color-scheme:light dark;--fg:#1d1d1f;--bg:#fff;--mut:#666;--line:#ddd;--ok:#1a7f37;--bad:#b42318}
@media(prefers-color-scheme:dark){:root{--fg:#eee;--bg:#161618;--mut:#999;--line:#333}}
body{font:15px/1.5 system-ui,sans-serif;color:var(--fg);background:var(--bg);max-width:900px;margin:2rem auto;padding:0 1rem}
h1{font-size:1.3rem}h2{font-size:1rem;color:var(--mut);text-transform:uppercase;letter-spacing:.04em;margin-top:1.6rem}
.call{border:1px solid var(--line);border-radius:8px;padding:.6rem .8rem;margin:.7rem 0}
.tool{font-weight:600}.ts{color:var(--mut);font-size:13px}.err{color:var(--bad)}.ok{color:var(--ok)}
pre{white-space:pre-wrap;background:rgba(127,127,127,.12);padding:.6rem;border-radius:6px;font-size:13px;margin:.4rem 0}
table{border-collapse:collapse;font-size:13px;display:block;overflow-x:auto}th,td{border:1px solid var(--line);padding:2px 7px;text-align:left}
.answer p{margin:.5rem 0}
"""


def esc(x) -> str:
    return html.escape("" if x is None else str(x))


def rows_table(columns, rows) -> str:
    head = "".join(f"<th>{esc(c)}</th>" for c in columns or [])
    body = "".join("<tr>" + "".join(f"<td>{esc(v)}</td>" for v in r) + "</tr>" for r in rows or [])
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>" if head else ""


def render_call(i: int, c: dict) -> str:
    a, r = c.get("args") or {}, c.get("result")
    parts = [f"<div class=call><span class=tool>{i}. {esc(c['tool'])}</span> <span class=ts>{esc(c['ts'])}</span>"]
    if c["tool"] == "run_sql":
        if a.get("purpose"):
            parts.append(f"<p><b>Purpose.</b> {esc(a['purpose'])}</p>")
        if a.get("query_plan"):
            parts.append(f"<p><b>Query plan.</b> {esc(a['query_plan'])}</p>")
        parts.append(f"<pre>{esc(a.get('sql'))}</pre>")
        if isinstance(r, dict) and r.get("error"):
            parts.append(f"<p class=err>Rejected: {esc(r['error'])}</p>")
        elif isinstance(r, dict):
            parts.append(f"<p class=ts>{esc(r.get('query_id'))}, {r.get('row_count')} rows, {r.get('elapsed_ms')} ms</p>")
            for w in r.get("warnings", []):
                parts.append(f"<p class=err>{esc(w)}</p>")
            parts.append(rows_table(r.get("columns"), r.get("rows_preview")))
    elif c["tool"] == "verify_answer" and isinstance(r, dict):
        cls = "ok" if r.get("ok") else "err"
        parts.append(f"<p class={cls}>{'All numbers traced to results' if r.get('ok') else 'Unsupported numbers: ' + esc(', '.join(r.get('unsupported_numbers', [])))}"
                     f" ({r.get('numbers_supported')}/{r.get('numbers_checked')})</p>")
        caveats = ", ".join(x["id"] for x in r.get("required_caveats", []))
        parts.append(f"<p class=ts>Required caveats: {esc(caveats)}</p>")
    else:
        parts.append(f"<pre>{esc(json.dumps(a, default=str))[:1500]}</pre>")
        parts.append(f"<pre>{esc(json.dumps(r, default=str))[:1500]}</pre>")
    parts.append("</div>")
    return "".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("trace")
    ap.add_argument("--answer-file")
    ap.add_argument("--question", default="")
    ap.add_argument("--out")
    args = ap.parse_args()
    path = pathlib.Path(args.trace)
    calls = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    question = args.question or next((c["args"].get("question") for c in calls if c["tool"] == "verify_answer"), path.stem)
    answer = pathlib.Path(args.answer_file).read_text() if args.answer_file else ""
    sql_calls = [c for c in calls if c["tool"] == "run_sql"]
    rejected = sum(1 for c in sql_calls if isinstance(c["result"], dict) and c["result"].get("error"))
    verifies = [c for c in calls if c["tool"] == "verify_answer"]
    final_ok = bool(verifies) and verifies[-1]["result"].get("ok")
    summary = (f"{len(calls)} tool calls, {len(sql_calls)} queries ({rejected} rejected by the guard), "
               f"{len(verifies)} verification calls, final verification {'passed' if final_ok else 'did not pass'}.")
    answer_html = "".join(f"<p>{esc(p)}</p>" for p in answer.split("\n\n") if p.strip())
    page = (f"<!doctype html><html><head><meta charset=utf-8><title>{esc(question)}</title><style>{CSS}</style></head><body>"
            f"<h1>{esc(question)}</h1><p class=ts>Session {esc(path.stem)}. {esc(summary)}</p>"
            + (f"<h2>Answer</h2><div class=answer>{answer_html}</div>" if answer else "")
            + "<h2>Tool calls</h2>" + "".join(render_call(i + 1, c) for i, c in enumerate(calls))
            + "</body></html>")
    out = pathlib.Path(args.out) if args.out else path.with_suffix(".html")
    out.write_text(page)
    print(out)


if __name__ == "__main__":
    main()
