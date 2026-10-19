"""Unknown severity must not become a certified zero fatal-crash measurement."""
import pytest

from test_review_regressions import kit
from test_certified_metrics import rich_kit
from test_certification import publish


@pytest.mark.parametrize('question', [
    'How many fatal collisions?',
    'At what hour of the day do the most fatal crashes happen?',
    'How has COVID-19 changed collisions, if at all?',
])
@pytest.mark.parametrize('severity', [None, 'unrecognized'])
def test_r2_unknown_severity_blocks_complete_fatal_measure(rich_kit, question, severity):
    rich_kit.db.connection.execute('UPDATE collisions SET collision_severity=?', [severity])
    _, p, result = publish(rich_kit, question)
    assert p['status'] == 'data_quality_blocked'
    assert not any(c['metric'] in ('collision_count', 'fatal_collision_count', 'fatal_collision_2020') for c in p['claims'])
    assert 'missing or unrecognized' in result['final_answer']
    # Other measures remain useful: the records and their death counts are known.
    for q in ('How many collisions?', 'How many people were killed?'):
        _, p, _ = publish(rich_kit, q)
        assert p['status'] == 'answered' and p['claims'][0]['computed'] == 3


def test_consistent_fatal_population_still_answers(rich_kit):
    _, p, _ = publish(rich_kit, 'How many fatal collisions?')
    assert p['status'] == 'answered'
    assert p['claims'][0]['computed'] == 2


def test_conflicting_known_codes_are_disclosed_and_measure_is_explicit(rich_kit):
    rich_kit.db.connection.execute("UPDATE collisions SET killed_victims=1 WHERE case_id='C'")
    _, p, _ = publish(rich_kit, 'How many fatal collisions?')
    assert p['status'] == 'answered'
    assert 'conflict' in p['answer'] and 'coded' in p['answer']
    assert any(c['metric'] == 'conflicting_fatal_classification' and c['computed'] == 1 for c in p['claims'])
    assert any(c['metric'] == 'collision_count' and c['computed'] == 2 for c in p['claims'])


def test_unknown_severity_outside_requested_population_does_not_block(rich_kit):
    rich_kit.db.connection.execute("UPDATE collisions SET collision_severity=NULL WHERE county_location='orange'")
    _, p, _ = publish(rich_kit, 'How many fatal collisions in Los Angeles County?')
    assert p['status'] == 'answered' and p['claims'][0]['computed'] == 2
