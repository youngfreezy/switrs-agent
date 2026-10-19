"""Golden-set eval runner, harness-agnostic.

  # recompute expected values from the golden SQL (no model calls)
  uv run python evals/run_evals.py --expected

  # grade answers produced by any harness: JSONL lines {"id": ..., "answer": ...}
  uv run python evals/run_evals.py --answers outputs/evals/<run>/answers.jsonl

  # run an agent command per question, then grade. {question}, {session} and {mcp_config}
  # (a per-question MCP config that tags the server's trace with the session) are substituted.
  uv run python evals/run_evals.py --agent claude
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import time

import duckdb
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from switrs_agent.claude_auth import billing_status, claude_env  # noqa: E402
from switrs_agent.grounding import extract_numbers  # noqa: E402

GOLDEN = ROOT / "evals" / "golden.yaml"
DB = os.environ.get("SWITRS_DB", str(ROOT / "data" / "switrs.duckdb"))
MAX_REL_TOL = 0.05
EVAL_CWD = pathlib.Path("/tmp/switrs-eval-cwd")  # empty dir: the agent cannot read golden.yaml
CLAUDE_CMD = (
    "claude -p --model claude-opus-5-5 --output-format json --no-session-persistence "
    "--setting-sources project --tools '' --strict-mcp-config --mcp-config {mcp_config} "
    "--allowedTools 'mcp__switrs__*' --max-budget-usd {budget} {question}"
)


def write_mcp_config(run_dir: pathlib.Path, session: str) -> pathlib.Path:
    cfg = {"mcpServers": {"switrs": {
        "command": "uv",
        "args": ["run", "--directory", str(ROOT), "python", "-m", "switrs_agent.mcp_server"],
        "env": {"SWITRS_SESSION": session},
    }}}
    path = run_dir / f"mcp-{session}.json"
    path.write_text(json.dumps(cfg))
    return path


def expected_values(con, check: dict) -> list[float]:
    sqls = check.get("any_of") or [check["sql"]]
    return [float(con.execute(s).fetchone()[0]) for s in sqls]


UP = re.compile(r"\b(increas\w*|rose|rise|rising|higher|more|grew|growth|jump\w*|climb\w*|up)\b", re.I)
DOWN = re.compile(r"\b(decreas\w*|fell|fall|falling|lower|fewer|less|drop\w*|declin\w*|reduc\w*|down)\b", re.I)
ROAD = re.compile(r"\b(I|US|SR|CA|Route|Highway|Hwy|Interstate)[- ]?\d+\b", re.I)
COUNT_NOUN = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?\s+(crashes|collisions|accidents|people|deaths|fatalities|injuries|cases)\b", re.I)
EXPECTED = ROOT / "evals" / "expected_v1.json"


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def number_matches(answer: str, targets: list[float], near: str | None = None) -> bool:
    """Sign-aware: a positive number in a sentence that says 'fell/fewer' counts as negative, and a
    plain positive number does not match a negative target. `near` requires the entity in the sentence."""
    for sent in sentences(ROAD.sub(" ", answer)):
        if near and near.lower() not in sent.lower():
            continue
        up, down = bool(UP.search(sent)), bool(DOWN.search(sent))
        for c in extract_numbers(sent):
            shown = c.value
            effective = -shown if (shown > 0 and down and not up) else shown
            tol = 0.5 * 10 ** (-c.decimals)
            for t in targets:
                cap = MAX_REL_TOL * abs(t) if t else tol
                allowed = min(tol, cap) if c.decimals < 0 else max(tol, 1e-9)
                if abs(effective - t) <= allowed + 1e-9:
                    return True
    return False


def grade(q: dict, answer: str, con) -> dict:
    results = []
    low = answer.lower()
    for check in q["checks"]:
        name = check.get("name") or check.get("type")
        if check["type"] == "text":
            ok = any(re.search(p, low) for p in check["any"])
            results.append({"check": name, "ok": ok})
        else:
            targets = expected_values(con, check)
            results.append({"check": name, "ok": number_matches(answer, targets, check.get("near")),
                            "expected": [round(t, 3) for t in targets]})
    if q.get("forbid_counts"):
        stray = [m.group(0) for m in COUNT_NOUN.finditer(ROAD.sub(" ", answer))]
        results.append({"check": "no invented counts", "ok": not stray, "found": stray})
    return {"id": q["id"], "pass": all(r["ok"] for r in results), "checks": results}


def certified_answer(session: str) -> str | None:
    """The final_answer of the last submit_answer call that passed, from the session trace."""
    path = ROOT / "outputs" / "traces" / f"{session}.jsonl"
    if not path.exists():
        return None
    final = None
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        c = json.loads(line)
        if c["tool"] == "submit_answer" and isinstance(c["result"], dict) and c["result"].get("ok"):
            final = c["result"].get("final_answer")
    return final


def freeze(golden: list[dict], con) -> dict:
    meta = json.loads((ROOT / "data" / "dataset_meta.json").read_text())
    values = {q["id"]: {c.get("name"): [round(v, 6) for v in expected_values(con, c)]
                        for c in q["checks"] if c["type"] == "number"} for q in golden}
    return {"dataset_version": meta["dataset_version"], "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "values": values}


def check_frozen(golden: list[dict], con) -> list[str]:
    """Differences between the golden SQL's current values and the approved baseline."""
    if not EXPECTED.exists():
        return ["evals/expected_v1.json is missing; run --freeze after reviewing --expected"]
    frozen = json.loads(EXPECTED.read_text())
    now = freeze(golden, con)
    drift = []
    if frozen["dataset_version"] != now["dataset_version"]:
        drift.append(f"dataset version {frozen['dataset_version']} -> {now['dataset_version']}")
    for qid, checks in now["values"].items():
        for name, vals in checks.items():
            old = frozen["values"].get(qid, {}).get(name)
            if old is None:
                drift.append(f"{qid} / {name}: not in the approved baseline")
            elif any(abs(a - b) > 1e-6 for a, b in zip(old, vals)) or len(old) != len(vals):
                drift.append(f"{qid} / {name}: {old} -> {vals}")
    return drift


def trace_stats(session: str) -> dict:
    path = ROOT / "outputs" / "traces" / f"{session}.jsonl"
    if not path.exists():
        return {"trace": None}
    calls = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    sql_calls = [c for c in calls if c["tool"] == "run_sql"]
    verifies = [c for c in calls if c["tool"] == "submit_answer"]
    return {
        "trace": str(path),
        "tool_calls": len(calls),
        "sql_calls": len(sql_calls),
        "rejected_sql": sum(1 for c in sql_calls if c["result"].get("error")),
        "query_plan_given": sum(1 for c in sql_calls if (c["args"].get("query_plan") or "").strip()),
        "fanout_warnings": sum(len(c["result"].get("warnings", [])) for c in sql_calls),
        "submit_calls": len(verifies),
        "submit_rejections": sum(1 for v in verifies if not v["result"].get("ok")),
        "certified": bool(verifies) and bool(verifies[-1]["result"].get("ok")),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expected", action="store_true")
    ap.add_argument("--freeze", action="store_true", help="approve current golden values as the baseline")
    ap.add_argument("--answers")
    ap.add_argument("--agent-cmd")
    ap.add_argument("--agent", choices=["claude"], help="use the built-in claude -p command")
    ap.add_argument("--budget", type=float, default=2.0, help="max USD per question (claude)")
    ap.add_argument("--only", help="comma-separated question ids")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()

    golden = yaml.safe_load(GOLDEN.read_text())["questions"]
    if args.only:
        keep = set(args.only.split(","))
        golden = [q for q in golden if q["id"] in keep]
    con = duckdb.connect(DB, read_only=True)

    if args.freeze:
        EXPECTED.write_text(json.dumps(freeze(golden, con), indent=1))
        print(f"wrote {EXPECTED}")
        return
    drift = check_frozen(golden, con)
    if drift:
        print("Golden values differ from the approved baseline (evals/expected_v1.json):")
        for d in drift:
            print("  " + d)
        print("Review the change, then run --freeze to approve it.")
        sys.exit(2)

    if args.expected:
        for q in golden:
            print(f"{q['id']}: {q['question']}")
            for c in q["checks"]:
                if c["type"] == "number":
                    print(f"   {c.get('name')}: {[round(v, 3) for v in expected_values(con, c)]}")
        return

    run_dir = ROOT / "outputs" / "evals" / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    answers: dict[str, dict] = {}
    if args.answers:
        for line in pathlib.Path(args.answers).read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                answers[row["id"]] = row
    elif args.agent_cmd or args.agent:
        template = CLAUDE_CMD.replace("{budget}", str(args.budget)) if args.agent == "claude" else args.agent_cmd
        if args.agent == "claude":
            billing = billing_status()
            print(f"billing: {billing['detail']}")
            if not billing["ok"]:
                sys.exit(1)
        EVAL_CWD.mkdir(exist_ok=True)
        for q in golden:
            session = f"eval-{run_dir.name}-{q['id']}"
            mcp_config = write_mcp_config(run_dir, session)
            cmd = (template.replace("{question}", shlex.quote(q["question"]))
                   .replace("{session}", session).replace("{mcp_config}", shlex.quote(str(mcp_config))))
            env = claude_env({"SWITRS_SESSION": session})
            start = time.monotonic()
            print(f"running {q['id']} ...", flush=True)
            meta = {}
            try:
                out = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                                     timeout=args.timeout, env=env, cwd=EVAL_CWD, stdin=subprocess.DEVNULL)
                text = out.stdout.strip() or out.stderr.strip()
                try:  # claude --output-format json
                    js = json.loads(text)
                    meta = {"cost_usd": js.get("total_cost_usd"), "turns": js.get("num_turns"),
                            "is_error": js.get("is_error"), "subtype": js.get("subtype")}
                    text = js.get("result") or text
                except (json.JSONDecodeError, AttributeError):
                    pass
            except subprocess.TimeoutExpired:
                text = "[timeout]"
            answers[q["id"]] = {"id": q["id"], "answer": text, "session": session,
                                "seconds": round(time.monotonic() - start), **meta}
            print(f"   {answers[q['id']]['seconds']}s cost={meta.get('cost_usd')} turns={meta.get('turns')}", flush=True)
        (run_dir / "answers.jsonl").write_text("\n".join(json.dumps(a) for a in answers.values()) + "\n")
    else:
        ap.error("pass --expected, --answers, or --agent-cmd")

    report = []
    for q in golden:
        a = answers.get(q["id"])
        if not a:
            report.append({"id": q["id"], "pass": False, "checks": [], "note": "no answer"})
            continue
        text = a["answer"]
        if a.get("session"):
            final = certified_answer(a["session"])
            g_certified = final is not None
            text = final or a["answer"]
        else:
            g_certified = None  # external harness: certification unknown
        g = grade(q, text, con)
        g["certified"] = g_certified
        if g_certified is False:
            g["pass"] = False
            g["checks"].append({"check": "answer passed submit_answer", "ok": False})
        if a.get("session"):
            g["trace_stats"] = trace_stats(a["session"])
        g["seconds"] = a.get("seconds")
        g["cost_usd"] = a.get("cost_usd")
        g["answer"] = a["answer"]
        report.append(g)

    passed = sum(r["pass"] for r in report)
    (run_dir / "report.json").write_text(json.dumps(report, indent=1))
    for r in report:
        mark = "PASS" if r["pass"] else "FAIL"
        failed = [c["check"] for c in r["checks"] if not c["ok"]]
        print(f"{mark} {r['id']}" + (f"  failed: {failed}" if failed else ""))
    cost = sum(r.get("cost_usd") or 0 for r in report)
    print(f"\n{passed}/{len(report)} passed. Cost ${cost:.2f}. Report: {run_dir / 'report.json'}")


if __name__ == "__main__":
    main()
