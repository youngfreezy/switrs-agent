"""`switrs`: talk to the SWITRS agent from a terminal, or call its tools directly.

  switrs ask "Which county has the most collisions?"     one question, then exit
  switrs chat                                            conversation with follow-ups
  switrs sql "SELECT ..." --purpose "..."                run one guarded query, no model
  switrs context | describe TABLE | values TABLE COLUMN  look at the data, no model
  switrs trace SESSION                                   write the HTML review page for a session
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys
import time

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.markup import escape
from rich.table import Table
from rich.text import Text

console = Console(highlight=False)
ROOT = pathlib.Path(__file__).resolve().parent.parent


# ---- rendering ------------------------------------------------------------------------------

def _summary(ev: dict) -> str:
    """One line describing a tool result."""
    r, tool = ev["result"], ev["tool"]
    if ev["is_error"] or (isinstance(r, dict) and "error" in r):
        msg = r.get("error") if isinstance(r, dict) else str(r)
        return f"[red]rejected[/red] [dim]{str(msg)[:110]}[/dim]"
    if tool == "run_sql" and isinstance(r, dict):
        bits = [f"[cyan]{r.get('query_id')}[/cyan]", f"{r.get('row_count')} rows", f"{r.get('elapsed_ms')} ms"]
        if r.get("warnings"):
            bits.append(f"[yellow]{len(r['warnings'])} warning(s)[/yellow]")
        return "  ".join(bits)
    if tool == "begin_question" and isinstance(r, dict):
        bits = [f"[cyan]{r.get('turn_id')}[/cyan]"]
        if r.get("gaps_to_state"):
            bits.append(f"[yellow]gaps: {'; '.join(str(g) for g in r['gaps_to_state'])[:110]}[/yellow]")
        return "  ".join(bits)
    if tool == "submit_answer" and isinstance(r, dict):
        if r.get("ok"):
            return f"[green]accepted, {r.get('claims_checked')} claims bound to query cells[/green]"
        errs = r.get("errors") or []
        return f"[red]rejected ({len(errs)} error(s))[/red] [dim]{str(errs[0])[:100] if errs else ''}[/dim]"
    if tool == "get_context":
        size = len(r) if isinstance(r, str) else len(json.dumps(r, default=str))
        return f"[dim]{size:,} chars of schema, metrics and caveats[/dim]"
    if tool == "distinct_values" and isinstance(r, dict):
        vals = [str(v[0]) for v in r.get("values", [])[:4]]
        return f"[dim]{', '.join(vals)}{'...' if len(r.get('values', [])) > 4 else ''}[/dim]"
    return "[dim]done[/dim]"


def _call_line(ev: dict) -> str:
    inp, tool = ev["input"], ev["tool"]
    if tool == "run_sql":
        return inp.get("purpose", "")
    if tool == "distinct_values":
        return f"{inp.get('table')}.{inp.get('column')}" + (f" ~ {inp['contains']!r}" if inp.get("contains") else "")
    if tool == "describe_table":
        return inp.get("table", "")
    if tool == "begin_question":
        return f"[dim]{inp.get('answerability')}:[/dim] {str(inp.get('interpretation', ''))[:90]}"
    if tool == "submit_answer":
        return f"{len(inp.get('claims', []))} claims for {inp.get('turn_id')}"
    return ""


async def run_turn(agent, question: str, verbose: bool) -> dict:
    calls: dict[str, dict] = {}
    texts: list[str] = []
    final: str | None = None
    done: dict = {}
    started = time.time()
    with console.status("[dim]thinking...[/dim]", spinner="dots") as status:
        async for ev in agent.ask(question):
            k = ev["kind"]
            if k == "tool_call":
                calls[ev["id"]] = ev
                status.update(f"[dim]{ev['tool']}...[/dim]")
            elif k == "tool_result":
                call = calls.get(ev["id"], {"tool": ev["tool"], "input": {}})
                room = max(30, console.width - 22)
                line = _call_line(call)
                plain = Text.from_markup(line).plain
                if len(plain) > room:
                    line = escape(plain[:room - 3]) + "..."
                console.print(f"  [bold]{call['tool']:<15}[/bold] {line}")
                summ = _summary(ev)
                if len(Text.from_markup(summ).plain) > room:
                    summ = escape(Text.from_markup(summ).plain[:room - 3]) + "..."
                console.print(f"  {'':<15} {summ}", no_wrap=True, overflow="ellipsis")
                r = ev["result"]
                if call["tool"] == "submit_answer" and isinstance(r, dict) and r.get("ok"):
                    final = r.get("final_answer")
                if verbose and call["tool"] == "run_sql":
                    _print_query(call["input"], ev["result"])
                status.update("[dim]thinking...[/dim]")
            elif k == "text":
                texts.append(ev["text"])
            elif k == "done":
                done = ev
    console.print()
    if final:
        console.print(Markdown(final))
    elif texts:
        console.print(Panel("No answer passed submit_answer. The text below is unverified.",
                            border_style="red"))
        console.print(Markdown(texts[-1]))
    elapsed = time.time() - started
    if done.get("error"):
        console.print(f"\n[red]error:[/red] {done['error']}")
    foot = [f"backend {agent.name}", f"{done.get('turns', '?')} model turns", f"{elapsed:.0f}s"]
    if done.get("cost_usd") is not None:
        foot.append(f"${done['cost_usd']:.3f} est.")
    foot.append(f"trace outputs/traces/{done.get('session_id', agent.session_id)}.jsonl")
    console.print("\n[dim]" + "  |  ".join(foot) + "[/dim]")
    return done


def _print_query(inp: dict, result) -> None:
    if inp.get("query_plan"):
        console.print(Panel(inp["query_plan"], title="query plan", title_align="left", border_style="dim"))
    console.print(Syntax(inp.get("sql", ""), "sql", word_wrap=True, background_color="default"))
    if isinstance(result, dict) and result.get("rows"):
        _print_rows(result["columns"], result["rows"], limit=8)


def _print_rows(columns: list[str], rows: list[list], limit: int = 20) -> None:
    t = Table(show_edge=False, header_style="bold")
    for c in columns:
        t.add_column(str(c))
    for row in rows[:limit]:
        t.add_row(*[f"{v:,}" if isinstance(v, int) else str(v) for v in row])
    console.print(t)
    if len(rows) > limit:
        console.print(f"[dim]... {len(rows) - limit} more rows[/dim]")


# ---- commands -------------------------------------------------------------------------------

def _agent(args):
    from .agent import make_agent
    return make_agent(args.backend, args.model, session_id=args.session)


async def cmd_ask(args) -> int:
    agent = _agent(args)
    console.print(f"[bold]> {args.question}[/bold]\n")
    try:
        done = await run_turn(agent, args.question, args.verbose)
    finally:
        await agent.close()
    return 1 if done.get("error") else 0


async def cmd_chat(args) -> int:
    agent = _agent(args)
    console.print(f"[bold]SWITRS agent[/bold] [dim](backend {agent.name}, model {args.model}). "
                  "Ask about California collisions, 2001 to 2021-06-03. Empty line or Ctrl-D to quit.[/dim]")
    try:
        while True:
            try:
                q = console.input("\n[bold cyan]you>[/bold cyan] ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not q:
                break
            if not sys.stdin.isatty():
                console.print(q)
            console.print()
            await run_turn(agent, q, args.verbose)
    finally:
        await agent.close()
    return 0


def cmd_sql(args) -> int:
    from .tools import Toolkit
    kit = Toolkit(session_id=args.session)
    if hasattr(kit, "begin_question"):  # the server requires an open turn before queries
        opened = kit.begin_question(args.purpose, "answerable", "ad hoc SQL from the switrs CLI")
        if "error" in opened:
            console.print(f"[red]rejected:[/red] {opened['error']}")
            return 1
    res = kit.run_sql(args.sql, args.purpose, args.plan or "")
    if args.json:
        print(json.dumps(res, default=str, indent=2))
        return 1 if "error" in res else 0
    if "error" in res:
        console.print(f"[red]rejected:[/red] {res['error']}")
        if res.get("hint"):
            console.print(f"[dim]{res['hint']}[/dim]")
        return 1
    console.print(f"[cyan]{res['query_id']}[/cyan]  {res['row_count']} rows  {res['elapsed_ms']} ms")
    for w in res.get("warnings", []):
        console.print(f"[yellow]warning:[/yellow] {w}")
    _print_rows(res["columns"], res["rows"])
    if res.get("caveats"):
        console.print("[dim]caveats: " + "; ".join(str(c) for c in res["caveats"]) + "[/dim]")
    return 0


def cmd_context(args) -> int:
    from .tools import Toolkit
    print(Toolkit(session_id=args.session).get_context())
    return 0


def cmd_describe(args) -> int:
    from .tools import Toolkit
    r = Toolkit(session_id=args.session).describe_table(args.table)
    if "error" in r:
        console.print(f"[red]{r['error']}[/red]")
        return 1
    for c in r["columns"]:
        console.print(c if isinstance(c, str) else json.dumps(c, default=str))
    return 0


def cmd_values(args) -> int:
    from .tools import Toolkit
    r = Toolkit(session_id=args.session).distinct_values(args.table, args.column, args.contains, args.limit)
    if "error" in r:
        console.print(f"[red]{r['error']}[/red]")
        return 1
    _print_rows(["value", "count"], r["values"], limit=args.limit)
    return 0


def cmd_trace(args) -> int:
    import subprocess
    path = ROOT / "outputs" / "traces" / f"{args.session_id}.jsonl"
    if not path.exists():
        console.print(f"[red]no trace at {path}[/red]")
        return 1
    return subprocess.call([sys.executable, str(ROOT / "scripts" / "render_trace.py"), str(path)])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="switrs", description="Ask the SWITRS collision agent, or query its tools directly.")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--session", default=None, help="session id for the trace file (default: timestamp)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    _add = sub.add_parser
    sub.add_parser = lambda *a, **k: _add(*a, parents=[common], **k)

    def agent_opts(p):
        p.add_argument("--backend", default=os.environ.get("SWITRS_BACKEND", "auto"), choices=["auto", "api", "sdk"],
                       help="api: Messages API with ANTHROPIC_API_KEY. sdk: Claude Agent SDK on your Claude login. "
                            "auto (default): api if ANTHROPIC_API_KEY is set, else sdk")
        from .agent import MODEL
        p.add_argument("--model", default=MODEL)
        p.add_argument("-v", "--verbose", action="store_true", help="print each query plan, SQL and result rows")

    p = sub.add_parser("ask", help="ask one question")
    p.add_argument("question")
    agent_opts(p)
    p = sub.add_parser("chat", help="interactive conversation with follow-ups")
    agent_opts(p)
    p = sub.add_parser("sql", help="run one guarded SELECT (no model)")
    p.add_argument("sql")
    p.add_argument("--purpose", default="ad hoc query from the CLI")
    p.add_argument("--plan", default="")
    p.add_argument("--json", action="store_true")
    sub.add_parser("context", help="print the grounding context the agent reads")
    p = sub.add_parser("describe", help="columns of one table")
    p.add_argument("table")
    p = sub.add_parser("values", help="most common values of a column")
    p.add_argument("table")
    p.add_argument("column")
    p.add_argument("--contains")
    p.add_argument("--limit", type=int, default=20)
    p = sub.add_parser("trace", help="render a session trace to HTML")
    p.add_argument("session_id")

    args = ap.parse_args(argv)
    if args.cmd == "ask":
        return asyncio.run(cmd_ask(args))
    if args.cmd == "chat":
        return asyncio.run(cmd_chat(args))
    return {"sql": cmd_sql, "context": cmd_context, "describe": cmd_describe,
            "values": cmd_values, "trace": cmd_trace}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
