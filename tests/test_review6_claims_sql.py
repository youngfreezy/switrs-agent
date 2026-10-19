"""Quantitative grounding regressions on the isolated review database."""
import asyncio
import pytest
from mcp import Client
from switrs_agent import mcp_server
from test_review_regressions import begin, run, submit, county_query, kit

def claim(query, text, op='value', rows=(0,), entity=None):
    result = {'text': text, 'op': op, 'metric': 'collisions', 'population': 'fixture collisions',
              'cells': [{'query_id': query['query_id'], 'row': i, 'column': 'n'} for i in rows]}
    if entity is not None:
        result['entity'] = entity
    return result


@pytest.mark.parametrize('text', [
    'There were one million collisions.',
    'Collisions fell by ninety percent.',
    'There were ½ million collisions.',
    'The deadliest collision occurred on 2020-05-11.',
])
def test_unbound_quantitative_answer_is_rejected(kit, text):
    turn = begin(kit)
    result = kit.submit_answer(turn, text, [])
    assert not result['ok'], result


def test_mcp_rejects_spelled_out_count_without_evidence(kit):
    kit.bind_question('How many collisions?')
    async def exercise():
        import json
        async with Client(mcp_server.build(kit)) as client:
            opened = await client.call_tool('begin_question', {
                'question': 'How many collisions?', 'answerability': 'answerable',
                'interpretation': 'Count collisions.'})
            data = json.loads(''.join(c.text for c in opened.content))
            result = await client.call_tool('submit_answer', {
                'turn_id': data['turn_id'], 'answer': 'There were one million collisions.', 'claims': []})
            data = json.loads(''.join(c.text for c in result.content))
            assert not data['ok'], data
    asyncio.run(exercise())


def test_count_cannot_be_presented_as_percentage(kit):
    turn = begin(kit)
    query = county_query(kit)
    text = 'Los Angeles had 2% of all collisions.'  # true share is 2/3, or 66.67%
    result = kit.submit_answer(turn, text, [claim(query, text, entity='los angeles')])
    assert not result['ok'], result


def test_ratio_cannot_reverse_comparison(kit):
    turn = begin(kit)
    query = county_query(kit)
    text = 'Los Angeles had 2 times fewer collisions than Orange.'
    result = kit.submit_answer(turn, text, [claim(query, text, 'ratio', (0, 1), ['los angeles', 'orange'])])
    assert not result['ok'], result


def test_unrelated_increase_word_cannot_hide_wrong_direction(kit):
    turn = begin(kit)
    query = county_query(kit)
    text = 'Los Angeles had 100% fewer collisions and more fatalities than Orange.'
    result = kit.submit_answer(turn, text, [claim(query, text, 'percent_change', (0, 1), ['los angeles', 'orange'])])
    assert not result['ok'], result


def test_literal_entity_cannot_relabel_real_measure(kit):
    turn = begin(kit)
    query = run(kit, "SELECT 'orange' AS county_location, COUNT(*) AS n FROM collisions WHERE county_location='los angeles'")
    assert query['data_derived'] == [False, True]
    result = submit(kit, turn, query, 'Orange had 2 collisions.', entity='orange')
    assert not result['ok'], result  # Orange actually has one collision.


@pytest.mark.parametrize('wrapped', [False, True])
def test_list_sum_cannot_bypass_fanout_check(kit, wrapped):
    turn = begin(kit)
    sql = 'SELECT list_sum(list(c.killed_victims)) AS n FROM collisions c JOIN parties p ON c.case_id=p.case_id'
    if wrapped:
        sql = ('WITH joined AS (SELECT c.killed_victims AS n FROM collisions c JOIN parties p USING(case_id)) '
               'SELECT list_sum(list(n)) AS n FROM joined')
    query = run(kit, sql)
    if 'error' in query:
        return
    assert query['rows'] == [[5]]  # true deaths = 2 + 1 + 0 = 3
    result = submit(kit, turn, query, 'There were 5 deaths.')
    assert not result['ok'], result


def test_percentage_with_explicit_denominator_passes(kit):
    turn = begin(kit)
    query = run(kit, "SELECT COUNT(*) FILTER (WHERE county_location='los angeles') AS n, COUNT(*) AS total FROM collisions")
    text = 'Los Angeles accounted for 66.67% of collisions.'
    evidence = claim(query, text, 'percent_of', (0, 0))
    evidence['cells'][1]['column'] = 'total'
    assert kit.submit_answer(turn, text, [evidence])['ok']


def test_collection_sum_without_join_passes(kit):
    turn = begin(kit)
    query = run(kit, 'SELECT list_sum(list(killed_victims)) AS n FROM collisions')
    assert query['rows'] == [[3]]
    assert submit(kit, turn, query, 'There were 3 deaths.')['ok']


def test_known_fanout_cannot_be_certified_as_collision_count(kit):
    turn = begin(kit)
    query = run(kit, 'SELECT COUNT(*) AS n FROM collisions c JOIN parties p ON c.case_id=p.case_id')
    if 'error' in query:
        return
    assert query['rows'] == [[4]] and query['warnings']  # only 3 collisions
    text = 'There were 4 collisions.'
    result = kit.submit_answer(turn, text, [claim(query, text)])
    assert not result['ok'], result
