import json
from fastapi.testclient import TestClient
from hosted.app import create_app


class Agent:
    closed = False
    async def ask(self, question):
        yield {'kind':'text','text':'Unverified claim: 999 collisions'}
        yield {'kind':'tool_call','tool':'submit_answer','id':'call'}
        yield {'kind':'tool_result','id':'call','result':{
            'ok':True,'certified':True,'question':question,'final_answer':'Checked: 42 collisions.'}}
        yield {'kind':'done','error':None,'turns':1}
    async def close(self): self.closed = True


def client(agent=None):
    return TestClient(create_app(agent_factory=lambda: agent or Agent(), access_key='test-access', signing_key='test-signing'))


def events(response):
    return [json.loads(x[6:]) for x in response.text.split('\n\n') if x.startswith('data: ')]


def test_hosted_chat_requires_access_before_running_agent():
    assert client().post('/api/chat', json={'message':'How many collisions?'}).status_code == 401


def test_certificate_is_published_and_resources_closed():
    agent = Agent()
    response = client(agent).post('/api/chat', headers={'Authorization':'Bearer test-access'},
                                  json={'message':'How many collisions?'})
    assert response.status_code == 200
    result = events(response)
    assert result[-1]['kind'] == 'certified'
    assert result[-1]['final_answer'] == 'Checked: 42 collisions.'
    assert agent.closed
    receipt = result[-1]['receipt']
    app = client()
    assert app.post('/api/verify', json=receipt).json()['valid'] is True
    receipt['answer'] = 'Checked: 999 collisions.'
    assert app.post('/api/verify', json=receipt).json()['valid'] is False


def test_failed_new_submission_revokes_old_certificate():
    class Failed(Agent):
        async def ask(self, question):
            async for event in super().ask(question): yield event
            yield {'kind':'tool_call','tool':'submit_answer','id':'new'}
    result = events(client(Failed()).post('/api/chat',headers={'Authorization':'Bearer test-access'},
                                         json={'message':'How many collisions?'}))
    assert result[-1]['kind'] == 'uncertified'


def test_server_errors_do_not_leak_provider_secrets():
    class Broken(Agent):
        async def ask(self, question):
            raise RuntimeError('secret-key-example')
            yield
    response = client(Broken()).post('/api/chat', headers={'Authorization':'Bearer test-access'},
                                     json={'message':'How many collisions?'})
    assert 'secret-key-example' not in response.text
    assert events(response)[-1]['kind'] == 'uncertified'


def test_question_and_unknown_fields_are_bounded():
    app = client()
    auth = {'Authorization':'Bearer test-access'}
    for payload in [{'message':' '}, {'message':'x'*4001}, {'message':'count','certificate':True}]:
        assert app.post('/api/chat',headers=auth,json=payload).status_code == 422


def test_no_shared_history_or_trace_routes():
    assert client().get('/api/chats').status_code == 404
    assert client().get('/api/chats/other/trace').status_code == 404


def test_provider_failure_after_submission_prevents_publication():
    class Failed(Agent):
        async def ask(self, question):
            async for event in super().ask(question): yield event
            yield {'kind':'done','error':'provider-secret'}
    response = client(Failed()).post('/api/chat', headers={'Authorization':'Bearer test-access'},
                                     json={'message':'How many collisions?'})
    assert 'provider-secret' not in response.text
    assert events(response)[-1]['kind'] == 'uncertified'
