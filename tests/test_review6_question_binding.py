"""Bind tool turns to the request supplied by the application, not model rewrites."""
import asyncio
import io
import json

from rich.console import Console
from switrs_agent import cli
from test_review_regressions import begin, kit, run, submit


def test_bound_question_rejects_rewrite(kit):
    kit.bind_question('How many collisions involved red cars?')
    rewritten = kit.begin_question('How many collisions?', 'answerable', 'Count all collisions.')
    assert 'error' in rewritten
    original = kit.begin_question('How many collisions involved red cars?', 'partially_available', 'Color is absent.')
    assert 'vehicle_color' in original['detected']


def test_followup_revokes_old_certificate_even_for_same_question(kit):
    kit.bind_question('How many collisions?')
    turn = begin(kit)
    query = run(kit, 'SELECT COUNT(*) AS n FROM collisions')
    assert submit(kit, turn, query, 'There were 3 collisions.')['ok']
    kit.bind_question('How many collisions?')
    assert not submit(kit, turn, query, 'There were 3 collisions.')['ok']


def test_subprocess_binding_is_read_again_for_each_turn(kit, tmp_path):
    path = tmp_path / 'request.json'
    kit.request_path = path
    path.write_text(json.dumps({'id': 'first', 'question': 'How many collisions?'}))
    turn = begin(kit)
    query = run(kit, 'SELECT COUNT(*) AS n FROM collisions')
    path.write_text(json.dumps({'id': 'next', 'question': 'How many collisions involved red cars?'}))
    assert not submit(kit, turn, query, 'There were 3 collisions.')['ok']
    assert 'error' in run(kit, 'SELECT COUNT(*) AS n FROM collisions')
    assert 'error' in kit.begin_question('How many collisions?', 'answerable', 'All collisions.')


def test_missing_binding_file_fails_closed(kit, tmp_path):
    kit.request_path = tmp_path / 'missing.json'
    assert 'error' in kit.begin_question('How many collisions?', 'answerable', 'All collisions.')


def test_cli_refuses_certificate_for_rewritten_question(kit, monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(cli, 'console', Console(file=output, width=160, force_terminal=False))

    class Agent:
        name = 'fixture'
        session_id = 'fixture'
        async def ask(self, question):
            turn = begin(kit)
            query = run(kit, 'SELECT COUNT(*) AS n FROM collisions')
            result = submit(kit, turn, query, 'There were 3 collisions involving red cars.')
            yield {'kind': 'tool_call', 'id': 'submit', 'tool': 'submit_answer',
                   'input': {'turn_id': turn, 'claims': []}}
            yield {'kind': 'tool_result', 'id': 'submit', 'tool': 'submit_answer',
                   'is_error': False, 'result': result}
            yield {'kind': 'done', 'turns': 1, 'error': None}

    asyncio.run(cli.run_turn(Agent(), 'How many collisions involved red cars?', False))
    assert 'accepted,' not in output.getvalue()
    assert '3 collisions involving red cars' not in output.getvalue()


def test_unbound_mcp_can_explore_but_cannot_certify(kit):
    opened = kit.begin_question('How many collisions?', 'answerable', 'Count records.')
    query = run(kit, 'SELECT COUNT(*) AS n FROM collisions')
    assert query['rows'] == [[3]]
    result = submit(kit, opened['turn_id'], query, 'There were 3 collisions.')
    assert not result['ok']
    assert 'binding' in ' '.join(result['errors']).lower()


def test_sdk_rotates_host_request_on_followup(tmp_path):
    from switrs_agent.agent import SdkAgent
    from switrs_agent.request_binding import read_request
    requests = []
    agent = SdkAgent('fixture')
    agent.request_path = tmp_path / 'request.json'

    class Client:
        async def query(self, question):
            request = read_request(agent.request_path)
            assert request['question'] == question
            requests.append(request)
        async def receive_response(self):
            for event in []:
                yield event

    agent.client = Client()
    async def exercise():
        for question in ('How many collisions?', 'How many collisions?'):
            async for _ in agent.ask(question):
                pass
    asyncio.run(exercise())
    assert requests[0]['id'] != requests[1]['id']


def test_api_binds_question_before_model_tool_calls(kit):
    from types import SimpleNamespace
    from mcp import Client
    from switrs_agent import mcp_server
    from switrs_agent.agent import ApiAgent

    class Messages:
        async def create(self, **kwargs):
            return SimpleNamespace(stop_reason='tool_use', content=[SimpleNamespace(
                type='tool_use', id='begin', name='begin_question',
                input={'question': 'How many collisions?', 'answerability': 'answerable', 'interpretation': 'All records.'})])

    async def exercise():
        async with Client(mcp_server.build(kit)) as client:
            agent = ApiAgent.__new__(ApiAgent)
            agent.mcp, agent.toolkit = client, kit
            agent.messages, agent.system, agent.tools = [], '', []
            agent.model, agent.max_turns, agent.session_id = 'fixture', 1, 'fixture'
            agent.client = SimpleNamespace(messages=Messages())
            events = [event async for event in agent.ask('How many collisions involved red cars?')]
            result = next(e['result'] for e in events if e['kind'] == 'tool_result')
            assert 'original user question' in result['error']
    asyncio.run(exercise())
