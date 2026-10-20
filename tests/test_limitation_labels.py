"""A signed refusal must not claim that a numerical metric was certified."""
import pytest
from test_review_regressions import kit
from test_certified_metrics import rich_kit
from test_certification import publish


@pytest.mark.parametrize('question', [
    'What about those?',
    'How many collisions in 2025?',
    'Which county is safest per mile?',
])
def test_limitation_footer_describes_the_actual_verdict(rich_kit, question):
    _, prepared, result = publish(rich_kit, question, 'partially_available')
    assert prepared['status'] in ('unsupported', 'needs_clarification')
    assert 'Checked limitation:' in result['final_answer']
    assert 'Certified metric and scope:' not in result['final_answer']


def test_answer_footer_still_identifies_a_certified_metric(kit):
    _, _, result = publish(kit, 'How many collisions?')
    assert 'Certified metric and scope:' in result['final_answer']
