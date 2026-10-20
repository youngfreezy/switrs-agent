import json
from fastapi.testclient import TestClient
from hosted.app import create_app
from hosted import receipts

AUTH = {'Authorization': 'Bearer test-access'}


def client(completion='How many fatal motorcycle crashes in Alameda County in 2018?'):
    calls = []
    async def generate(**kwargs):
        calls.append(kwargs)
        if isinstance(completion, Exception):
            raise completion
        return completion
    app = TestClient(create_app(completion_fn=generate, access_key='test-access', signing_key='sign'))
    return app, calls


def test_completion_requires_access_before_calling_model():
    app, calls = client()
    assert app.post('/api/completions', json={'prefix': 'How many fatal moto'}).status_code == 401
    assert calls == []


def test_arbitrary_supported_prefix_gets_a_question_not_a_certificate():
    app, calls = client()
    prefix = 'How many fatal motorcycle cra'
    result = app.post('/api/completions', headers={**AUTH, 'x-vercel-oidc-token': 'request-identity'}, json={'prefix': prefix})
    assert result.status_code == 200
    assert result.json() == {'prefix': prefix, 'completion': 'How many fatal motorcycle crashes in Alameda County in 2018?'}
    assert calls[0]['oidc_token'] == 'request-identity'
    assert calls[0]['context']['previous'] is None
    assert calls[0]['context']['coverage']['max_collision_date'] == '2021-06-03'
    assert 'alameda' in calls[0]['context']['counties']
    assert "script-src 'self'" in result.headers['content-security-policy']


def test_context_is_authenticated_and_reduced_before_generation():
    previous = receipts.sign('And in 2018?', 'Checked: 42.', 'sign',
                             resolved_question='How many collisions in Alameda County in 2018?', status='answered')
    app, calls = client('And in 2019?')
    result = app.post('/api/completions', headers=AUTH, json={'prefix': 'And in', 'previous': previous})
    assert result.json()['completion'] == 'And in 2019?'
    assert calls[0]['context']['previous'] == {
        'question': 'How many collisions in Alameda County in 2018?', 'answer': 'Checked: 42.'}
    previous['answer'] = 'forged'
    assert app.post('/api/completions', headers=AUTH, json={'prefix': 'And in', 'previous': previous}).status_code == 422
    assert len(calls) == 1


def test_limitations_are_not_used_as_followup_context():
    previous = receipts.sign('Why?', 'Unsupported.', 'sign', resolved_question='Why?', status='unsupported')
    app, calls = client('And in 2019?')
    assert app.post('/api/completions', headers=AUTH, json={'prefix': 'And in', 'previous': previous}).json()['completion'] == ''
    assert calls[0]['context']['previous'] is None


def test_malformed_rewritten_or_unsupported_outputs_are_discarded():
    for completion in [None, {'text': 'oops'}, 'How many fatalities?',
                       'How many collisions? There were 999.',
                       'How many collisions in Atlantis County in 2018?',
                       'How many collisions in 2021?', 'How many collisions in 2026?',
                       'How many collisions involving blue cars?',
                       'How many collisions\n in 2018?', '<script>alert(1)</script>',
                       'How many collisions' + ' ' * 300 + '?']:
        app, _ = client(completion)
        result = app.post('/api/completions', headers=AUTH, json={'prefix': 'How many collisions'})
        assert result.json() == {'prefix': 'How many collisions', 'completion': ''}, completion


def test_request_bounds_are_enforced_before_model_usage():
    app, calls = client()
    for body in [{'prefix': 'abc'}, {'prefix': '    '}, {'prefix': 'x' * 1001},
                 {'prefix': 'How many', 'history': ['untrusted']}, {'prefix': 'How many\n'}]:
        assert app.post('/api/completions', headers=AUTH, json=body).status_code == 422
    assert calls == []


def test_provider_errors_are_optional_and_do_not_leak_details():
    app, _ = client(RuntimeError('secret-provider-detail'))
    result = app.post('/api/completions', headers=AUTH, json={'prefix': 'How many'})
    assert result.status_code == 200
    assert result.json() == {'prefix': 'How many', 'completion': ''}
    assert 'secret-provider-detail' not in result.text


def test_full_question_and_whitespace_are_not_rewritten():
    app, calls = client('  how  many collisions in Alameda County in 2018?')
    prefix = '  how  many collisions in Alameda'
    assert app.post('/api/completions', headers=AUTH, json={'prefix': prefix}).json()['completion'].startswith(prefix)
    assert app.post('/api/completions', headers=AUTH, json={'prefix': 'How many collisions?'}).json()['completion'] == ''
    assert len(calls) == 1


def test_model_adapter_supplies_dataset_and_answer_context_and_closes(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from hosted import completions
    seen = {}
    class Provider:
        messages = None
        def __init__(self): self.messages = self
        async def __aenter__(self): return self
        async def __aexit__(self, *args): seen['closed'] = True
        async def create(self, **kwargs):
            seen.update(kwargs)
            return SimpleNamespace(stop_reason='tool_use', content=[SimpleNamespace(
                type='tool_use', name='complete_question', input={'suffix': 'rcycle crashes?'})])
    monkeypatch.setattr(completions, 'gateway_client', lambda **kwargs: Provider())
    context = completions.context_for(None)
    result = asyncio.run(completions.generate_completion(prefix='How many fatal moto', context=context, oidc_token='identity'))
    assert result == 'How many fatal motorcycle crashes?'
    assert json.loads(seen['messages'][0]['content'])['context'] == context
    assert seen['max_tokens'] == 256
    assert seen['closed']
    assert 'never answer' in seen['system']


def test_disconnected_autocomplete_cancels_provider_work():
    import asyncio
    from hosted.completions import while_connected
    async def scenario():
        closed = []
        class Request:
            async def is_disconnected(self): return True
        async def generate():
            try:
                await asyncio.sleep(60)
            finally:
                closed.append(True)
        assert await while_connected(Request(), generate()) == ''
        assert closed == [True]
    asyncio.run(scenario())


def test_completion_capacity_does_not_queue_or_block_chat():
    import asyncio
    import httpx
    async def scenario():
        release = asyncio.Event()
        started = asyncio.Event()
        calls = []
        async def generate(**kwargs):
            calls.append(kwargs)
            if len(calls) == 2: started.set()
            await release.wait()
            return 'How many collisions?'
        class Agent:
            async def ask(self, question): yield {'kind': 'done', 'error': None}
            async def close(self): pass
        app = create_app(completion_fn=generate, agent_factory=lambda **kw: Agent(),
                         access_key='test-access', signing_key='sign')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as http:
            first = [asyncio.create_task(http.post('/api/completions', headers=AUTH,
                      json={'prefix': 'How many'})) for _ in range(2)]
            await asyncio.wait_for(started.wait(), 1)
            third = await http.post('/api/completions', headers=AUTH, json={'prefix': 'How many'})
            assert third.json()['completion'] == ''
            assert len(calls) == 2
            chat = await http.post('/api/chat', headers=AUTH, json={'message': 'How many collisions?'})
            assert chat.status_code == 200
            release.set()
            assert all(r.json()['completion'] == 'How many collisions?' for r in await asyncio.gather(*first))
    asyncio.run(scenario())
