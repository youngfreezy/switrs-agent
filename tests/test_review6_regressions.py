"""Sixth-review attacks against presentation parsing and collection aggregation."""
import pytest

from test_review_regressions import begin, kit, run, submit


@pytest.mark.parametrize('number', [
    '3&nbsp;billion', '&minus;3', '− 3', '3<!-- -->billion', '3<em>billion</em>',
])
def test_presentation_cannot_hide_magnitude_or_sign(kit, number):
    turn = begin(kit)
    query = run(kit, 'SELECT COUNT(*) AS n FROM collisions')
    assert not submit(kit, turn, query, f'The measured value was {number}.')['ok']


@pytest.mark.parametrize(('number', 'factor'), [
    ('3&nbsp;billion', 1e9), ('&minus;3', -1), ('− 3', -1),
    ('**3** billion', 1e9), ('***3*** billion', 1e9), ('`3` billion', 1e9),
    ('3&#32;billion', 1e9),
])
def test_matching_rendered_numbers_are_supported(kit, number, factor):
    turn = begin(kit)
    query = run(kit, f'SELECT COUNT(*) * {factor} AS n FROM collisions')
    assert submit(kit, turn, query, f'The measured value was {number}.')['ok']


def test_markdown_list_marker_is_not_a_negative_sign(kit):
    turn = begin(kit)
    query = run(kit, 'SELECT COUNT(*) AS n FROM collisions')
    assert submit(kit, turn, query, '- 3 collisions.')['ok']


def test_encoded_unclaimed_number_is_rejected(kit):
    turn = begin(kit)
    assert not kit.check_calculation(turn, 'There were &#51; collisions.', [])['ok']


@pytest.mark.parametrize('expression', [
    'LIST_SUM(LIST(c.killed_victims))',
    'LIST_AVG(LIST(c.killed_victims))',
    "LIST_AGGREGATE(LIST(c.killed_victims), 'sum')",
    "LIST_AGGREGATE(LIST(c.killed_victims), 'fsum')",
    'LIST_SUM(ARRAY_AGG(c.killed_victims))',
])
def test_collection_reducers_cannot_hide_collision_fanout(kit, expression):
    begin(kit)
    query = run(kit, f'SELECT {expression} AS n FROM collisions c JOIN parties p USING(case_id)')
    assert 'error' in query, query


@pytest.mark.parametrize('expression', ['LIST_SUM(n)', 'LIST_AVG(n)', "LIST_AGGREGATE(n, 'sum')"])
def test_collection_cte_cannot_erase_collision_fanout(kit, expression):
    begin(kit)
    query = run(kit, 'WITH x AS (SELECT LIST(c.killed_victims) AS n '
                     'FROM collisions c JOIN parties p USING(case_id)) '
                     f'SELECT {expression} AS n FROM x')
    assert 'error' in query, query


@pytest.mark.parametrize(('expression', 'expected'), [
    ('LIST_SUM(LIST(c.killed_victims))', 3),
    ('LIST_AVG(LIST(c.killed_victims))', 1),
    ("LIST_AGGREGATE(LIST(c.killed_victims), 'sum')", 3),
])
def test_collection_reducers_at_collision_grain_remain_citable(kit, expression, expected):
    turn = begin(kit)
    query = run(kit, f'SELECT {expression} AS n FROM collisions c WHERE EXISTS '
                     '(SELECT 1 FROM parties p WHERE p.case_id=c.case_id)')
    assert 'error' not in query, query
    assert query['rows'] == [[expected]]
    assert submit(kit, turn, query, f'The measured value was {expected}.')['ok']
