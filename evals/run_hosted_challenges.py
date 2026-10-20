"""Exercise hosted SSE, its publication policy and signed receipts against SQLite.

SWITRS_ACCESS_KEY=... uv run python evals/run_hosted_challenges.py \
    --url https://your-project.vercel.app --source /path/switrs.sqlite --only count_fronted,injection
The access key is read from the environment and is never written to the report.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.run_live_challenges import judge
from switrs_agent.publication import latest_certificate


def submission_from(events):
    calls = {e['id']: e for e in events if e.get('kind') == 'tool_call'}
    latest = next((e for e in reversed(events) if e.get('kind') == 'tool_call'
                   and e.get('tool') == 'submit_answer'), None)
    if latest is None:
        return None
    result = next((e for e in reversed(events) if e.get('kind') == 'tool_result'
                   and e.get('id') == latest['id']), None)
    return {'args': calls[latest['id']]['input'], 'result': result['result']} if result else None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--url', required=True)
    ap.add_argument('--source', required=True, type=Path)
    ap.add_argument('--only')
    ap.add_argument('--cases', type=Path, default=ROOT / 'evals/live_challenges.json')
    ap.add_argument('--output', type=Path, default=ROOT / 'outputs/hosted-challenges.json')
    args = ap.parse_args()
    cases = json.loads(args.cases.read_text())
    if args.only:
        ids = set(args.only.split(','))
        if ids - {c['id'] for c in cases}:
            ap.error('Unknown challenge ID')
        cases = [c for c in cases if c['id'] in ids]
    manifest = json.loads((ROOT / 'hosted/dataset_meta.json').read_text())
    with args.source.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    if digest != manifest['source']['sha256']:
        ap.error('SQLite does not match the reconciled snapshot')
    report = {'url': args.url, 'source_sha256': digest, 'results': []}
    with sqlite3.connect(args.source.resolve().as_uri() + '?mode=ro&immutable=1', uri=True) as con, httpx.Client(timeout=300) as client:
        for case in cases:
            print(case['id'], flush=True)
            expected = [[r[0] for r in con.execute(check['sql']).fetchall()] for check in case.get('checks', [])]
            start = time.monotonic()
            response = client.post(args.url.rstrip('/') + '/api/chat', json={'message': case['question']},
                                   headers={'Authorization': 'Bearer ' + os.environ['SWITRS_ACCESS_KEY']})
            response.raise_for_status()
            events = [json.loads(chunk[6:]) for chunk in response.text.split('\n\n') if chunk.startswith('data: ')]
            final = events[-1] if events else {}
            submission = submission_from(events)
            answer = latest_certificate(events, case['question'])
            passed = (final.get('kind') == 'certified' and final.get('final_answer') == answer
                      and bool(answer) and judge(case, submission, expected)
                      and submission['result'].get('analytical_sha256') == manifest['duckdb_file']['sha256'])
            if passed:
                receipt = final['receipt']
                passed = (client.post(args.url.rstrip('/') + '/api/verify', json=receipt).json().get('valid') is True)
            report['results'].append({'id': case['id'], 'question': case['question'], 'expected': expected,
                                      'pass': bool(passed), 'seconds': round(time.monotonic()-start, 2), 'events': events})
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2))
            print('PASS' if passed else 'FAIL', flush=True)
    passed = sum(r['pass'] for r in report['results'])
    print(f'{passed}/{len(cases)} passed. {args.output}')
    raise SystemExit(0 if passed == len(cases) else 1)


if __name__ == '__main__':
    main()
