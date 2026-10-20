"""The live oracle grader must not confuse certified limitations with useful answers."""
import pytest

from evals.run_live_challenges import judge


def submission(value=7, metric='collision_count', status='answered', **extra):
    return {'result': {'status': status, 'final_answer': 'Orange County in 2019'},
            'args': {'claims': [{'computed': value, 'metric': metric}, *extra.get('claims', [])]}}


CASE = {'checks': [{'metric': 'collision_count', 'sql': 'SELECT 7'}], 'contains': ['Orange', '2019']}


@pytest.mark.parametrize('value', [6, 8, float('nan'), float('inf'), True])
def test_wrong_or_invalid_measurement_fails(value):
    assert not judge(CASE, submission(value), [[7]])


def test_exact_measurement_and_scope_pass():
    assert judge(CASE, submission(), [[7]])


def test_false_refusal_is_not_success():
    assert not judge(CASE, {'result': {'status': 'needs_clarification'}, 'args': {'claims': []}}, [[7]])


def test_unchecked_measurement_fails():
    assert not judge(CASE, submission(claims=[{'metric': 'invented', 'computed': 100000}]), [[7]])


def test_no_tolerance_on_integer_counts_above_float_precision():
    assert not judge(CASE, submission(2**53 + 1), [[2**53]])


def test_limitations_require_no_claims():
    assert judge({'limitation': True}, {'result': {'status': 'unsupported'}, 'args': {'claims': []}}, [])
    assert not judge({'limitation': True}, submission(status='unsupported'), [])
