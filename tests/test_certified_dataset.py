"""Actual-snapshot certification smoke tests; no live model or API calls.

The golden questions are known examples, not a held-out generalization benchmark.
Independent synthetic and randomized fixtures test the metric definitions separately.
"""
from pathlib import Path

import pytest
import yaml

from evals.run_evals import grade
from switrs_agent.tools import Toolkit

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = yaml.safe_load((ROOT / 'evals/golden.yaml').read_text())['questions']
pytestmark = pytest.mark.skipif(not (ROOT / 'data/switrs.duckdb').exists(), reason='dataset not built')


@pytest.fixture(scope='module')
def dataset_kit():
    return Toolkit(session_id='pytest-certified-dataset')


@pytest.mark.parametrize('question', QUESTIONS, ids=[q['id'] for q in QUESTIONS])
def test_original_examples_compute_and_certify_on_real_snapshot(dataset_kit, question):
    kit = dataset_kit
    kit.bind_question(question['question'])
    turn = kit.begin_question(question['question'], 'partially_available', 'Server determines the metric and limitations.')
    assert 'turn_id' in turn, turn
    prepared = kit.prepare_answer(turn['turn_id'])
    assert 'error' not in prepared, prepared
    assert prepared['status'] in ('answered', 'unsupported'), prepared
    result = kit.submit_answer(turn['turn_id'], prepared['answer'], prepared['claims'])
    assert result['ok'] and result['certified'], result
    graded = grade(question, prepared['answer'], kit.db.con, rendered=False)
    assert graded['pass'], graded
