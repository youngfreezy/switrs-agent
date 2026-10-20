"""Hosted usability reproductions with independent predicate decoys."""
import pytest

from test_review_regressions import kit
from test_certified_metrics import rich_kit
from test_certification import publish


@pytest.mark.parametrize('question,metric,expected', [
    ('How many traffic accidents were reported in Orange County during 2020?', 'collision_count', 1),
    ('What was the total number of crashes in Orange County in 2020?', 'collision_count', 1),
    ('How many reported fatal motorcycle collisions occurred in Los Angeles County in 2019?', 'collision_count', 1),
    ('How many crashes occurred in Orange County in the year 2020?', 'collision_count', 1),
    ('Among at-fault drivers with known cellphone status, what percentage were using a cellphone?', 'cellphone', 50),
    ('At which hour were the most fatal crashes recorded?', 'hour_of_day', 18),
])
def test_hosted_paraphrases(rich_kit, question, metric, expected):
    _, prepared, certificate = publish(rich_kit, question)
    assert prepared['status'] == 'answered'
    assert [c['computed'] for c in prepared['claims'] if c['metric'] == metric] == [expected]
    assert certificate['question'] == question


@pytest.mark.parametrize('question', [
    'How many traffic accidents were not reported in Orange County during 2020?',
    'What was the total number of crashes in Orange County in 2020 per capita?',
    'How many reported fatal motorcycle collisions occurred in Los Angeles County in 2019 excluding passengers?',
    'How many crashes occurred in Orange County in the year 2019 in 2020?',
    'How many crashes occurred in Orange County in the year 0000?',
    'How many crashes occurred in Orange County in the year 2021?',
    'Among all drivers with known cellphone status, what percentage were using a cellphone?',
    'Among at-fault drivers with known cellphone status, what percentage were using a cellphone in 2020?',
    'At which hour were the most fatal crashes recorded in Orange County?',
    'At which hour were the most deaths recorded?',
])
def test_nearby_population_changes_are_not_lost(rich_kit, question):
    _, prepared, _ = publish(rich_kit, question, 'partially_available')
    assert prepared['status'] in ('unsupported', 'needs_clarification')
    assert prepared['claims'] == []
