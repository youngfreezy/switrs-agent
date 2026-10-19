"""Every certified coverage statement must describe the active snapshot."""
import pytest

from test_certified_metrics import rich_kit
from test_review_regressions import kit
from test_certification import publish


@pytest.mark.parametrize('question', ['How many collisions in 2021?', 'How many collisions today?'])
def test_r4_refusal_and_footer_use_same_snapshot(rich_kit, question):
    rich_kit.dataset['coverage'] = {'min_collision_date': '2019-01-01', 'max_collision_date': '2020-12-31'}
    _, _, result = publish(rich_kit, question, 'partially_available')
    assert result['status'] == 'unsupported'
    assert '2001-01-01' not in result['final_answer']
    assert '2021-06-03' not in result['final_answer']
    assert '2020-12-31' in result['final_answer']


def test_extended_snapshot_can_answer_year_after_static_catalog(rich_kit):
    rich_kit.dataset['coverage'] = {'min_collision_date': '2001-01-01', 'max_collision_date': '2023-12-31'}
    rich_kit.db.connection.execute("UPDATE collisions SET collision_date=DATE '2022-06-01' WHERE case_id='A'")
    _, p, result = publish(rich_kit, 'How many collisions in 2022?')
    assert result['certified'] and p['status'] == 'answered'
    assert p['claims'][0]['computed'] == 1
    assert '2021-06-03' not in result['final_answer']


def test_grounding_context_uses_active_coverage(rich_kit):
    rich_kit.dataset['coverage'] = {'min_collision_date': '2019-01-01', 'max_collision_date': '2020-12-31'}
    context = rich_kit.get_context()
    assert '2020-12-31' in context
    assert '2021-06-03' not in context
