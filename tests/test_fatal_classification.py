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


@pytest.mark.parametrize('question', [
    'How many fatal collisions?',
    'At what hour of the day do the most fatal crashes happen?',
    'How has COVID-19 changed collisions, if at all?',
])
@pytest.mark.parametrize('severity', [None, 'N'])
def test_observed_fatal_codes_remain_useful_with_explicit_unknown_exclusions(rich_kit, question, severity):
    rich_kit.db.connection.execute("UPDATE collisions SET collision_severity=? WHERE case_id='C'", [severity])
    _, p, result = publish(rich_kit, question)
    assert p['status'] == 'answered' and result['certified']
    assert 'coded fatal' in p['answer']
    assert 'exclude' in p['answer'] and 'not a complete count' in p['answer']
    assert any(c['metric'] == 'unknown_fatal_classification' and c['computed'] == 1 for c in p['claims'])
    assert any(c['metric'] in ('collision_count', 'fatal_collision_count', 'fatal_collision_2020') for c in p['claims'])


def test_unknown_severity_with_no_observed_fatal_code_cannot_become_zero(rich_kit):
    rich_kit.db.connection.execute("UPDATE collisions SET collision_severity='severe injury'")
    rich_kit.db.connection.execute("UPDATE collisions SET collision_severity=NULL WHERE case_id='C'")
    _, p, _ = publish(rich_kit, 'How many fatal collisions?')
    assert p['status'] == 'data_quality_blocked'
    assert not any(c['metric'] == 'collision_count' for c in p['claims'])


@pytest.mark.parametrize("severity", [None, "N"])
def test_unknown_severity_is_not_a_known_code_conflict(rich_kit, severity):
    rich_kit.db.connection.execute("UPDATE collisions SET collision_severity=?,killed_victims=1 WHERE case_id='C'", [severity])
    _, p, _ = publish(rich_kit, 'How many fatal collisions?')
    assert p['status'] == 'answered'
    assert any(c['metric'] == 'unknown_fatal_classification' and c['computed'] == 1 for c in p['claims'])
    assert not any(c['metric'] == 'conflicting_fatal_classification' for c in p['claims'])
