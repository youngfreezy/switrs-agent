import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from hosted import receipts, suggestions
from hosted.app import create_app
from switrs_agent.certification import normalize, parse
from switrs_agent.metric_recipes import recipe_for

AUTH = {'Authorization': 'Bearer test-access'}
PRIOR = 'How many collisions in Orange County in 2019?'


def score(p=0.9):
    return {'type': 'score', 'score': 2*p, 'probabilities': {'0': 1-p, '1': 0, '2': p}}


def client(result=None):
    calls = []
    async def rank(**kwargs):
        calls.append(kwargs)
        if isinstance(result, Exception):
            raise result
        return result if result is not None else {'answers': {str(i): score() for i in range(len(kwargs['candidates']))}}
    return TestClient(create_app(ranking_fn=rank, access_key='test-access', signing_key='sign')), calls


def test_candidates_are_supported_explicit_and_exclude_the_previous_question():
    candidates = suggestions.candidates_for('', PRIOR)
    assert 'How many fatalities in Orange County in 2019?' in candidates
    assert 'How many collisions in Orange County in 2020?' in candidates
    assert PRIOR not in candidates
    assert len(candidates) <= 16 and len(candidates) == len(set(candidates))
    assert all(parse(q) or recipe_for(normalize(q)) for q in candidates)


def test_draft_scope_overrides_prior_without_inventing_a_county_or_year():
    candidates = suggestions.candidates_for('Deaths in Alameda County in 2018', PRIOR)
    assert 'How many fatalities in Alameda County in 2018?' in candidates
    assert not any('Orange' in q or '2019' in q for q in candidates)
    assert 'How many fatalities in California?' in suggestions.candidates_for('fatalities', None)
    assert suggestions.candidates_for('', None) == []


@pytest.mark.parametrize('draft', ['collisions in 2021', 'collisions in 2026',
    'compare 2018 and 2019', 'collisions in Orange County and Alameda County', 'crashes in Atlantis County'])
def test_ambiguous_or_uncovered_scope_never_becomes_an_approximate_suggestion(draft):
    assert suggestions.candidates_for(draft, PRIOR) == []


def test_ranker_can_only_reorder_server_candidates_and_drops_uncertainty():
    candidates = ['First?', 'Second?', 'Third?']
    result = {'answers': {'0': score(0.7), '1': score(0.95), '2': score(0.5),
                          '999': score(1), '<script>bad</script>': score(1)}}
    assert suggestions.select_ranked(candidates, result) == ['Second?', 'First?']
    for bad in [None, [], {'answers': []}, {'answers': {'0': {'type': 'score', 'score': 2}}},
                {'answers': {'0': {'type': 'score', 'score': float('nan'), 'probabilities': {'0': 0, '1': 0, '2': 1}}}}]:
        assert suggestions.select_ranked(candidates, bad) == []


def test_endpoint_authenticates_context_and_does_not_send_answers_or_receipts_to_jev():
    app, calls = client()
    assert app.post('/api/suggestions', json={'draft': 'fatalities'}).status_code == 401
    assert not calls
    prior = receipts.sign(PRIOR, 'Private answer', 'sign', resolved_question=PRIOR, status='answered')
    response = app.post('/api/suggestions', headers={**AUTH, 'x-vercel-oidc-token':'identity'}, json={'draft':'fatalities', 'previous':prior})
    assert response.status_code == 200
    assert response.json()['source'] == 'jev' and len(response.json()['suggestions']) == 3
    assert calls[0]['previous_question'] == PRIOR and calls[0]['oidc_token'] == 'identity'
    assert 'Private answer' not in json.dumps(calls) and prior['signature'] not in json.dumps(calls)
    prior['question'] = 'forged'
    assert app.post('/api/suggestions', headers=AUTH, json={'draft':'fatalities','previous':prior}).status_code == 422
    assert len(calls) == 1


def test_optional_failures_and_low_confidence_leave_starters_available():
    for output in [RuntimeError('secret-provider-body'), {'answers': {'0': score(0.4)}}]:
        app, _ = client(output)
        response = app.post('/api/suggestions', headers=AUTH, json={'draft':'fatalities'})
        assert response.json() == {'draft':'fatalities','suggestions':[], 'source':'fallback'}
        assert 'secret' not in response.text


def test_no_work_for_empty_invalid_or_unverified_context():
    app, calls = client()
    prior = receipts.sign('Why?', 'Unsupported', 'sign', status='unsupported')
    assert app.post('/api/suggestions', headers=AUTH, json={'draft':'','previous':prior}).json()['suggestions'] == []
    for body in [{'draft':'x'*1001}, {'draft':'a\nb'}, {'draft':'fatalities','candidates':['malicious?']}]:
        assert app.post('/api/suggestions', headers=AUTH, json=body).status_code == 422
    assert not calls


def test_gateway_adapter_uses_existing_identity_and_one_bounded_decision_call(monkeypatch):
    seen = []
    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={'answers':{'0':score()}})
    original = httpx.AsyncClient
    monkeypatch.setattr(suggestions.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    monkeypatch.delenv('AI_GATEWAY_API_KEY', raising=False)
    result = asyncio.run(suggestions.rank_candidates(draft='fatalities', previous_question=PRIOR,
                        candidates=['How many fatalities in Orange County in 2019?'], oidc_token='identity'))
    assert result['answers']['0']['type'] == 'score'
    assert len(seen) == 1 and str(seen[0].url) == 'https://ai-gateway.vercel.sh/v1/evaluate'
    assert seen[0].headers['authorization'] == 'Bearer identity'
    body = json.loads(seen[0].content)
    assert body['model'] == 'typesafe-ai/jev-1.14.0'
    assert body['state'] == {'draft':'fatalities','previous_question':PRIOR, 'scope_resolved_draft':'fatalities'}
    assert body['questions']['0']['type'] == 'score'
    assert seen[0].extensions['timeout']['read'] <= 4


def test_ranking_capacity_does_not_queue_or_block_chat():
    async def scenario():
        release, started = asyncio.Event(), asyncio.Event()
        calls = []
        async def rank(**kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                started.set()
            await release.wait()
            return {'answers': {'0': score()}}
        class Agent:
            async def ask(self, question):
                yield {'kind':'done', 'error':None}
            async def close(self):
                pass
        app = create_app(ranking_fn=rank, agent_factory=lambda **kw: Agent(),
                         access_key='test-access', signing_key='sign')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as http:
            first = [asyncio.create_task(http.post('/api/suggestions', headers=AUTH,
                      json={'draft':'fatalities'})) for _ in range(2)]
            await asyncio.wait_for(started.wait(), 1)
            third = await http.post('/api/suggestions', headers=AUTH, json={'draft':'fatalities'})
            assert third.json()['source'] == 'fallback' and len(calls) == 2
            assert (await http.post('/api/chat', headers=AUTH, json={'message':'How many collisions?'})).status_code == 200
            release.set()
            assert all(r.json()['source'] == 'jev' for r in await asyncio.gather(*first))
    asyncio.run(scenario())


def test_every_candidate_across_counties_and_boundary_years_compiles():
    for county in ['Orange', 'Los Angeles', 'San Francisco', 'Del Norte']:
        for year in [2001, 2019, 2020]:
            for metric in ['collisions', 'fatalities', 'fatal motorcycle collisions']:
                previous = f'How many {metric} in {county} County in {year}?'
                candidates = suggestions.candidates_for('', previous)
                assert candidates
                for q in candidates:
                    parsed = parse(q)
                    assert parsed and parsed.county == county.lower()
                    assert 2001 <= parsed.year <= 2020
                    assert normalize(q) != normalize(previous)
