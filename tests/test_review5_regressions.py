"""Fresh adversarial cases on isolated data, with positive controls."""

import pytest

from test_review_regressions import begin, county_query, kit, run, submit


@pytest.mark.parametrize('number', ['.75', '−.75', '.75%', '½ million'])
def test_unclaimed_numeric_forms_are_rejected(kit, number):
    turn = begin(kit)
    result = kit.check_calculation(turn, f'The measured value was {number}.', [])
    assert not result['ok'], result


@pytest.mark.parametrize(('number', 'value'), [('.75', .75), ('−.75', -.75), ('3 bn', 3e9)])
def test_supported_numeric_forms_remain_citable(kit, number, value):
    turn = begin(kit)
    query = run(kit, f'SELECT COUNT(*) * {value / 3} AS n FROM collisions')
    assert 'error' not in query, query
    result = submit(kit, turn, query, f'The measured value was {number}.')
    assert result['ok'], result


@pytest.mark.parametrize('number', ['3 bn', '**3** billion', '3 **billion**', '3b', '***3*** billion', '__**3**__ billion'])
def test_magnitude_cannot_hide_in_abbreviation_or_markdown(kit, number):
    turn = begin(kit)
    query = run(kit, 'SELECT COUNT(*) AS n FROM collisions')
    result = submit(kit, turn, query, f'There were {number} collisions.')
    assert not result['ok'], result


def compare(kit, turn, query, text, op, rows=(0, 1)):
    labels = {0: 'los angeles', 1: 'orange'}
    claim = dict(text=text, op=op, entity=[labels[row] for row in rows],
                 metric='collisions', population='fixture collisions',
                 cells=[dict(query_id=query['query_id'], row=row, column='n') for row in rows])
    return kit.check_calculation(turn, text, [claim])


@pytest.mark.parametrize(('op', 'text', 'rows'), [
    ('percent_change', 'Los Angeles decreased by 100% more than Orange.', (0, 1)),
    ('percent_change', 'Orange increased by -50% relative to Los Angeles.', (1, 0)),
    ('ratio', 'Los Angeles had 2 times fewer collisions than Orange.', (0, 1)),
    ('percent_of', 'Los Angeles had 200% more collisions than Orange.', (0, 1)),
    ('difference', 'Orange had -1 more collision than Los Angeles.', (1, 0)),
])
def test_contradictory_comparison_wording_is_rejected(kit, op, text, rows):
    turn = begin(kit)
    result = compare(kit, turn, county_query(kit), text, op, rows)
    assert not result['ok'], result


@pytest.mark.parametrize(('op', 'text', 'rows'), [
    ('percent_change', 'Los Angeles increased by 100% relative to Orange.', (0, 1)),
    ('percent_change', 'Orange decreased by 50% relative to Los Angeles.', (1, 0)),
    ('percent_change', 'Orange changed by -50% relative to Los Angeles.', (1, 0)),
    ('ratio', 'Los Angeles had 2 times as many collisions as Orange.', (0, 1)),
    ('ratio', 'Orange had .5 times as many collisions as Los Angeles.', (1, 0)),
    ('percent_of', 'Los Angeles had 200% as many collisions as Orange.', (0, 1)),
    ('difference', 'Orange had 1 fewer collision than Los Angeles.', (1, 0)),
])
def test_supported_comparison_wording_remains_citable(kit, op, text, rows):
    turn = begin(kit)
    result = compare(kit, turn, county_query(kit), text, op, rows)
    assert result['ok'], result


@pytest.mark.parametrize('aggregate', ['FSUM', 'KAHAN_SUM', 'SUMKAHAN', 'FAVG', 'MEAN'])
@pytest.mark.parametrize('wrapped', [False, True])
def test_aggregate_aliases_cannot_bypass_collision_fanout(kit, aggregate, wrapped):
    begin(kit)
    if wrapped:
        sql = ('WITH joined AS (SELECT c.killed_victims AS n FROM collisions c '
               f'JOIN parties p USING(case_id)) SELECT {aggregate}(n) AS n FROM joined')
    else:
        sql = (f'SELECT {aggregate}(c.killed_victims) AS n FROM collisions c '
               'JOIN parties p USING(case_id)')
    # The fixture totals 3 deaths; person multiplication produces 5 (mean 1.25 vs 1).
    assert 'error' in run(kit, sql)


@pytest.mark.parametrize(('aggregate', 'expected'), [
    ('FSUM', 3), ('KAHAN_SUM', 3), ('SUMKAHAN', 3), ('FAVG', 1), ('MEAN', 1),
])
def test_aggregate_aliases_with_exists_remain_citable(kit, aggregate, expected):
    turn = begin(kit)
    query = run(kit, f'SELECT {aggregate}(c.killed_victims) AS n FROM collisions c '
                     'WHERE EXISTS (SELECT 1 FROM parties p WHERE p.case_id=c.case_id)')
    assert 'error' not in query, query
    assert query['rows'] == [[expected]]
    assert submit(kit, turn, query, f'The measured value was {expected}.')['ok']


def test_verbose_result_displays_sql_markup_as_literal_text(monkeypatch):
    import asyncio
    import io
    from rich.console import Console
    from switrs_agent import cli

    class FakeAgent:
        name = 'fixture'
        session_id = 'fixture'

        async def ask(self, question):
            yield {'kind': 'tool_call', 'id': '1', 'tool': 'run_sql',
                   'input': {'sql': 'SELECT county_location AS "[/bad]" FROM collisions', 'purpose': 'fixture'}}
            yield {'kind': 'tool_result', 'id': '1', 'tool': 'run_sql', 'is_error': False,
                   'result': {'query_id': 'q1', 'columns': ['[/bad]'], 'rows': [['[bold]literal[/bold]']],
                              'row_count': 1, 'elapsed_ms': 0}}
            yield {'kind': 'done', 'turns': 0, 'error': None}

    output = io.StringIO()
    monkeypatch.setattr(cli, 'console', Console(file=output, force_terminal=False, width=120))
    asyncio.run(cli.run_turn(FakeAgent(), 'fixture', True))
    assert '[/bad]' in output.getvalue()
    assert '[bold]literal[/bold]' in output.getvalue()


def test_cli_sql_guard_error_is_rendered_as_literal_text(monkeypatch):
    import io
    from types import SimpleNamespace
    from rich.console import Console
    from switrs_agent import cli, tools

    class RejectedToolkit:
        def begin_question(self, *args):
            return {'turn_id': 't1'}

        def run_sql(self, *args):
            return {'error': "Unknown column '[/bad]'"}

    output = io.StringIO()
    monkeypatch.setattr(cli, 'console', Console(file=output, force_terminal=False, width=120))
    monkeypatch.setattr(tools, 'Toolkit', lambda **kw: RejectedToolkit())
    args = SimpleNamespace(session='fixture', purpose='fixture', sql='fixture', plan='', json=False)
    assert cli.cmd_sql(args) == 1
    assert "Unknown column '[/bad]'" in output.getvalue()


@pytest.mark.parametrize('number', ['−**3**', '**3**e6', '−***3***'])
def test_emphasis_cannot_hide_sign_or_exponent(kit, number):
    turn = begin(kit)
    query = run(kit, 'SELECT COUNT(*) AS n FROM collisions')
    assert not submit(kit, turn, query, f'The measured value was {number}.')['ok']


@pytest.mark.parametrize(('number', 'factor'), [('−**3**', -1), ('**3** billion', 1e9), ('***3*** billion', 1e9)])
def test_matching_emphasized_numbers_remain_citable(kit, number, factor):
    turn = begin(kit)
    query = run(kit, f'SELECT COUNT(*) * {factor} AS n FROM collisions')
    assert submit(kit, turn, query, f'The measured value was {number}.')['ok']
