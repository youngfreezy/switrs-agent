"""Run live Claude + MCP challenges and compare certified claims with independent SQLite SQL.

uv run python evals/run_live_challenges.py --source /path/switrs.sqlite --only count_control,count_fronted
No expected measurements are sent to the model. Raw model output is diagnostic only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import sqlite3
import subprocess
import sys
import time
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from evals.run_evals import certified_submission, record_answer, write_mcp_config
from switrs_agent.agent import MODEL
from switrs_agent.claude_auth import billing_status, claude_env


def judge(case, submission, expected):
    result = submission['result'] if submission else {}
    claims = submission.get('args', {}).get('claims', []) if submission else []
    if case.get('limitation'):
        return result.get('status') in ('unsupported', 'needs_clarification') and not claims
    if result.get('status') not in ('answered', 'no_matching_records'):
        return False
    if {c['metric'] for c in claims} != {c['metric'] for c, values in zip(case['checks'], expected, strict=True) if values}:
        return False
    for check, values in zip(case['checks'], expected, strict=True):
        actual = [c['computed'] for c in claims if c['metric'] == check['metric']]
        if len(actual) != len(values) or any(
                type(a) not in (int, float) or not math.isfinite(a)
                or abs(a - b) > check.get('tolerance', 0) for a, b in zip(actual, values, strict=True)):
            return False
    return all(text.lower() in result.get('final_answer', '').lower() for text in case.get('contains', []))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source', required=True, type=pathlib.Path)
    ap.add_argument('--only', help='Comma-separated challenge IDs')
    ap.add_argument('--budget', type=float, default=1.0)
    ap.add_argument('--timeout', type=int, default=240)
    ap.add_argument('--model', default=MODEL)
    args = ap.parse_args()
    billing = billing_status()
    if not billing['ok']:
        ap.error(billing['detail'])
    cases = json.loads((ROOT / 'evals/live_challenges.json').read_text())
    if args.only:
        chosen = set(args.only.split(','))
        if chosen - {c['id'] for c in cases}:
            ap.error('Unknown challenge ID')
        cases = [c for c in cases if c['id'] in chosen]
    source = args.source.resolve(strict=True)
    print('Hashing SQLite source before oracle queries...', flush=True)
    with source.open('rb') as stream:
        source_sha256 = hashlib.file_digest(stream, 'sha256').hexdigest()
    manifest = json.loads((ROOT / 'data/dataset_meta.json').read_text())
    if source_sha256 != manifest['source']['sha256']:
        ap.error('Oracle SQLite does not match the reconciled source hash')
    run = ROOT / 'outputs/evals' / ('live-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    run.mkdir(parents=True)
    empty = run / 'empty-cwd'
    empty.mkdir()
    report = dict(commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                  dirty=bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT)),
                  model=args.model, billing=billing['detail'], source=str(source), source_sha256=source_sha256,
                  dataset_version=manifest['dataset_version'], analytical_sha256=manifest['duckdb_file']['sha256'], results=[])
    with sqlite3.connect(source.as_uri() + '?mode=ro&immutable=1', uri=True) as con:
        for case in cases:
            print('Computing SQLite oracle: ' + case['id'], flush=True)
            expected = [[row[0] for row in con.execute(check['sql']).fetchall()] for check in case.get('checks', [])]
            session = run.name + '-' + case['id']
            config = write_mcp_config(run, session, case['question'])
            cmd = ['claude', '-p', '--model', args.model, '--output-format', 'json', '--no-session-persistence',
                   '--setting-sources', '', '--tools', '', '--strict-mcp-config', '--mcp-config', str(config),
                   '--allowedTools', 'mcp__switrs__*', '--max-budget-usd', str(args.budget), '--', case['question']]
            print('Running ' + case['id'], flush=True)
            start = time.monotonic()
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout,
                                     cwd=empty, env=claude_env(), stdin=subprocess.DEVNULL)
                raw = json.loads(out.stdout)
                error = raw.get('is_error', False) or out.returncode != 0
            except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
                raw, error = {'result': str(exc)}, True
            submission = certified_submission(session)
            published = record_answer(session, case['question'], raw.get('result', ''))
            row = dict(**case, expected=expected, session=session, certificate=submission,
                       **published, model_error=error, cost_usd=raw.get('total_cost_usd'),
                       seconds=round(time.monotonic()-start, 2))
            row['pass'] = not error and bool(published['answer']) and judge(case, submission, expected)
            row['pass'] = row['pass'] and submission['result'].get('analytical_sha256') == report['analytical_sha256']
            report['results'].append(row)
            report['passed'] = sum(r['pass'] for r in report['results'])
            report['total'] = len(report['results'])
            (run / 'report.json').write_text(json.dumps(report, indent=2))
            print(f"  {'PASS' if row['pass'] else 'FAIL'} {submission['result']['status'] if submission else 'no certificate'} {row['seconds']}s", flush=True)
    print(f"{report['passed']}/{report['total']} passed. Report: {run / 'report.json'}")
    sys.exit(0 if report['passed'] == report['total'] else 1)


if __name__ == '__main__':
    main()
