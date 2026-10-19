"""Evaluation regressions found during the live data review."""
import json

import pytest

from evals import run_evals
from test_review6_evaluator import GOLDEN, grade


@pytest.mark.parametrize('refusal', [
    'This data neither supports nor disproves it.',
    'This data neither proves nor disproves police bias.',
    'This data neither confirms nor establishes police bias.',
])
def test_neither_nor_refusal_is_not_a_positive_verdict(refusal):
    assert grade('q05_red_sports_car_bias',
                 'No vehicle color or traffic stops data. We cannot test police bias. ' + refusal)['pass']


@pytest.mark.parametrize('answer', [
    "No column records color. There is no traffic-stop data. This dataset can't confirm or refute the claim.",
    'No field captures vehicle colour. No traffic stops data. This cannot confirm or refute the claim.',
])
def test_missing_field_and_inconclusive_verdict_paraphrases(answer):
    assert grade('q05_red_sports_car_bias', answer)['pass']


@pytest.mark.parametrize('verdict', [
    'Neither field is present, but this proves police bias.',
    'Neither field is present, and the data proves police bias.',
    'It neither proves nor disproves bias; police are biased.',
])
def test_neither_does_not_hide_a_later_positive_verdict(verdict):
    assert not grade('q05_red_sports_car_bias',
                     'No vehicle color or traffic stops data. We cannot test police bias. ' + verdict)['pass']


@pytest.mark.parametrize('text,expected', [
    ('A larger share of crashes were deadly.', True),
    ('A higher share of crashes were fatal.', True),
    ('A smaller share of crashes were deadly.', False),
    ('Fatal crash share fell.', False),
])
def test_severity_check_recognizes_share_wording(text, expected):
    check = next(c for c in GOLDEN['q04_covid']['checks'] if c.get('name') == 'severity rose')
    assert run_evals.grade({'id': 'q04_covid', 'checks': [check]}, text, None)['pass'] is expected


def write_trace(tmp_path, monkeypatch, results):
    monkeypatch.setattr(run_evals, 'ROOT', tmp_path)
    path = tmp_path / 'outputs/traces/session.jsonl'
    path.parent.mkdir(parents=True)
    path.write_text('\n'.join(json.dumps({'tool': 'submit_answer', 'args': {'answer': 'Count: 3.'},
                                        'result': r}) for r in results))


def test_eval_publishes_certificate_and_retains_raw_drift(tmp_path, monkeypatch):
    write_trace(tmp_path, monkeypatch, [{'ok': True, 'certified': True, 'question': 'Count?', 'final_answer': 'Count: 3.\nEvidence'}])
    saved = run_evals.record_answer('session', 'Count?', 'Count: 999.\nTraffic is lighter at night.')
    assert saved['answer'] == 'Count: 3.\nEvidence'
    assert saved['raw_answer'] == 'Count: 999.\nTraffic is lighter at night.'
    assert saved['output_matches_certificate'] is False


@pytest.mark.parametrize('last', [
    {'ok': False},
    {'ok': True, 'certified': True, 'question': 'Another question', 'final_answer': 'Wrong turn'},
    {'ok': True, 'certified': True, 'question': 'Count?'},
])
def test_eval_does_not_publish_a_revoked_or_wrong_question_certificate(tmp_path, monkeypatch, last):
    write_trace(tmp_path, monkeypatch, [{'ok': True, 'certified': True, 'question': 'Count?', 'final_answer': 'Old'}, last])
    saved = run_evals.record_answer('session', 'Count?', 'Model made up an answer.')
    assert saved['answer'] == ''
    assert saved['raw_answer'] == 'Model made up an answer.'
    assert saved['output_matches_certificate'] is None


@pytest.mark.parametrize('marker', [None, False, 1, 'true'])
def test_eval_rejects_legacy_or_nonboolean_certificate_markers(tmp_path, monkeypatch, marker):
    last = {'ok': True, 'question': 'Count?', 'final_answer': 'Count: 3.\nEvidence'}
    if marker is not None:
        last['certified'] = marker
    write_trace(tmp_path, monkeypatch, [
        {'ok': True, 'certified': True, 'question': 'Count?', 'final_answer': 'Earlier valid answer'}, last])
    saved = run_evals.record_answer('session', 'Count?', 'Unverified model text.')
    assert saved['answer'] == ''
    assert saved['raw_answer'] == 'Unverified model text.'
    assert saved['output_matches_certificate'] is None
    assert run_evals.certified_submission('session') is None
    assert run_evals.certified_answer('session') is None


def test_eval_keeps_matching_server_output_and_original_submission(tmp_path, monkeypatch):
    final = 'Count: 3.\nEvidence'
    write_trace(tmp_path, monkeypatch, [
        {'ok': True, 'certified': True, 'question': 'Count?', 'final_answer': final}])
    assert run_evals.certified_submission('session')['args']['answer'] == 'Count: 3.'
    assert run_evals.certified_answer('session') == 'Count: 3.'
    assert run_evals.record_answer('session', 'Count?', final) == {
        'answer': final, 'raw_answer': final, 'output_matches_certificate': True}
