import asyncio
from switrs_agent.agent import ApiAgent


def test_api_agent_accepts_host_owned_client_and_token_limit():
    class Client:
        async def close(self): pass
    client = Client()
    agent = ApiAgent('hosted-test', client=client, max_tokens=2048)
    assert agent.client is client
    assert agent.max_tokens == 2048
    asyncio.run(agent.close())


def test_gateway_client_uses_runtime_token_without_build_environment(monkeypatch):
    from hosted.runtime import make_agent
    monkeypatch.delenv('AI_GATEWAY_API_KEY', raising=False)
    monkeypatch.delenv('VERCEL_OIDC_TOKEN', raising=False)
    agent = make_agent(oidc_token='runtime-identity')
    assert agent.client.api_key == 'runtime-identity'
    assert str(agent.client.base_url) == 'https://ai-gateway.vercel.sh'
    asyncio.run(agent.close())
