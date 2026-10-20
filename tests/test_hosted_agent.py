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
