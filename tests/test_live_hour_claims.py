"""Clock-hour arithmetic regressions from upstream; diagnostics cannot certify prose."""
import pytest

from switrs_agent import claims
from test_review_regressions import begin, kit, run


def hour_check(text, value=18, op='hour'):
    query = {'q1': {'columns': ['hour'], 'rows': [[value]], 'turn_id': 't1', 'data_derived': [True]}}
    claim = {'text': text, 'op': op, 'metric': 'peak hour', 'population': 'fatal collisions',
             'cells': [{'query_id': 'q1', 'row': 0, 'column': 'hour'}]}
    return claims.check_answer(text, [claim], query, 't1')


@pytest.mark.parametrize('hour', [0, 8, 18, 23])
def test_hour_arithmetic_can_check_peak_time(hour):
    result = hour_check(f'The peak hour starts at {hour:02d}:00.', hour)
    assert result['ok'], result


@pytest.mark.parametrize('text,value,op', [
    ('The peak hour starts at 19:00.', 18, 'hour'),
    ('The peak hour starts at 18:30.', 18, 'hour'),
    ('The peak hour starts at 18:00:30.', 18, 'hour'),
    ('The peak hour starts at 18:00 and had 999 crashes.', 18, 'hour'),
    ('The peak hour starts at 18:00 on 2020-01-01.', 18, 'hour'),
    ('The peak hour starts at 18:00.', 18.4, 'hour'),
    ('The peak hour starts at 24:00.', 24, 'hour'),
    ('The peak hour starts at 18:00.', 18, 'value'),
    ('The peak hour starts at 08:00 pm.', 8, 'hour'),
    ('The peak hour starts at 08:00PM.', 8, 'hour'),
    ('The peak hour starts at -18:00.', 18, 'hour'),
    ('The peak hour starts at 08:00 a.m.', 8, 'hour'),
])
def test_hour_format_does_not_exempt_unchecked_temporal_claims(text, value, op):
    assert not hour_check(text, value, op)['ok']


def test_hour_and_count_pass_arithmetic_without_certifying_arbitrary_sql(kit):
    kit.db.connection.execute("ALTER TABLE collisions ADD COLUMN collision_time TIME")
    kit.db.connection.execute("UPDATE collisions SET collision_time = CASE WHEN case_id='C' THEN '08:15:00'::TIME ELSE '18:15:00'::TIME END")
    kit.schema_cols = kit.db.table_columns()
    turn = begin(kit, 'Which hour has the most collisions?')
    q = run(kit, 'SELECT hour(collision_time) AS h, count(*) AS n FROM collisions GROUP BY 1 ORDER BY n DESC')
    assert q['rows'][0] == [18, 2]
    answer = 'The peak hour starts at 18:00. That hour had 2 collisions.'
    evidence = [{'text': text, 'op': op, 'metric': 'peak hour and count', 'population': 'collisions',
                 'cells': [{'query_id': q['query_id'], 'row': 0, 'column': column}]}
                for text, op, column in [('The peak hour starts at 18:00.', 'hour', 'h'),
                                         ('That hour had 2 collisions.', 'value', 'n')]]
    diagnostic = kit.check_calculation(turn, answer, evidence)
    assert diagnostic['ok']
    assert diagnostic['certified'] is False and 'final_answer' not in diagnostic
    assert not kit.submit_answer(turn, answer, evidence)['ok']
    literal = run(kit, 'SELECT 18 AS h FROM collisions LIMIT 1')
    evidence[0]['cells'][0]['query_id'] = literal['query_id']
    assert not kit.check_calculation(turn, answer, evidence)['ok']


def test_valid_hour_claim_cannot_cover_another_unbound_time():
    query = {'q1': {'columns': ['hour'], 'rows': [[18]], 'turn_id': 't1', 'data_derived': [True]}}
    claim = {'text': 'The peak hour starts at 18:00.', 'op': 'hour', 'metric': 'hour',
             'population': 'fatal collisions', 'cells': [{'query_id': 'q1', 'row': 0, 'column': 'hour'}]}
    result = claims.check_answer(claim['text'] + ' The quietest hour starts at 02:00.', [claim], query, 't1')
    assert not result['ok']


@pytest.mark.parametrize('time', ['08:00 pm', '-08:00', '08:00 a.m.'])
def test_hour_claim_cannot_hide_modifiers_outside_its_span(time):
    query = {'q1': {'columns': ['hour'], 'rows': [[8]], 'turn_id': 't1', 'data_derived': [True]}}
    claim = {'text': '08:00', 'op': 'hour', 'metric': 'hour', 'population': 'collisions',
             'cells': [{'query_id': 'q1', 'row': 0, 'column': 'hour'}]}
    assert not claims.check_answer(f'The peak was at {time}.', [claim], query, 't1')['ok']


