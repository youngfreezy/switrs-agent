"""Actual-snapshot certification smoke tests; no live model or API calls.

The golden questions are known examples, not a held-out generalization benchmark.
Independent synthetic and randomized fixtures test the metric definitions separately.
"""
from pathlib import Path
import os
import subprocess
import sys

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


def test_parked_recipe_fits_hosted_resource_limits(tmp_path):
    # A separate process is required: existing DuckDB instances share locked settings.
    code = """
from switrs_agent.tools import Toolkit
kit = Toolkit(session_id='parked-hosted-budget')
question = 'What percentage of collisions involved a parked vehicle?'
kit.bind_question(question)
turn = kit.begin_question(question, 'ambiguous', 'Report the supported definitions separately.')
prepared = kit.prepare_answer(turn['turn_id'])
assert 'error' not in prepared, prepared
assert all(value in prepared['answer'] for value in ('7.38%', '9.99%', '9.67%')), prepared
result = kit.submit_answer(turn['turn_id'], prepared['answer'], prepared['claims'])
assert result['ok'] and result['certified'], result
kit.db.con.close()
"""
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env={
        **os.environ, 'SWITRS_MEMORY_LIMIT': '1GB', 'SWITRS_TEMP_LIMIT': '0GB',
        'SWITRS_THREADS': '2', 'SWITRS_OUTPUT_DIR': str(tmp_path),
    }, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
