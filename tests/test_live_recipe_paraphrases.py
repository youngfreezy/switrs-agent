"""Live false refusals for equivalent descriptive-analysis questions."""
import pytest

from test_review_regressions import kit
from test_certified_metrics import rich_kit
from test_certification import publish


@pytest.mark.parametrize('question,metrics,expected', [
    ('What percentage of at-fault drivers with known cellphone status were using a cellphone?',
     ['cellphone', 'unknown_cellphone_status'], [50, 1]),
    ('What percent of at fault drivers used a cell phone?', ['cellphone', 'unknown_cellphone_status'], [50, 1]),
    ('How many crashes involved at least one drinking driver?', ['collisions_with_drinking_driver'], [1]),
    ('Which hour had the most fatal collisions?', ['hour_of_day', 'fatal_collision_count'], [18, 2]),
    ('What hour has the most fatal crashes?', ['hour_of_day', 'fatal_collision_count'], [18, 2]),
])
def test_equivalent_recipe_wording_remains_useful(rich_kit, question, metrics, expected):
    _, p, _ = publish(rich_kit, question)
    assert p['status'] == 'answered'
    assert [c['metric'] for c in p['claims']] == metrics
    assert [c['computed'] for c in p['claims']] == expected


@pytest.mark.parametrize('question', [
    'What percentage of at-fault drivers with known cellphone status were using a cellphone in 2019?',
    'What percent of at fault drivers used a cell phone excluding unknown values and teenagers?',
    'What percentage of all drivers were using a cellphone?',
    'How many crashes involved at least one drinking driver in Orange County?',
    'How many crashes were caused by at least one drinking driver?',
    'How many crashes involved at least one drunk driver?',
    'Which hour had the most fatal collisions in 2020?',
    'Which hour had the most fatalities?',
    'Which hour had the most fatal collisions per mile?',
])
def test_recipe_paraphrases_do_not_change_population_or_causality(rich_kit, question):
    _, p, _ = publish(rich_kit, question, 'partially_available')
    assert p['status'] in ('needs_clarification', 'unsupported')
    assert p['claims'] == []
