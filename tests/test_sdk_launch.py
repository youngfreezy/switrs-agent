"""The isolated SDK server must be able to import a source checkout."""
import asyncio
import os
import subprocess

from switrs_agent import agent


def test_sdk_server_finds_checkout_without_editable_install(monkeypatch):
    import claude_agent_sdk

    async def spec():
        return "fixture", []

    class Client:
        def __init__(self, options):
            self.options = options

        async def connect(self):
            server = self.options.mcp_servers["switrs"]
            env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
            env.update(server["env"])
            # -S excludes installed .pth files, as in a source-only checkout.
            # Module discovery needs no third-party imports or model access.
            result = subprocess.run(
                [server["command"], "-S", "-c",
                 "import importlib.util; assert importlib.util.find_spec('switrs_agent.mcp_server')"],
                cwd=self.options.cwd, env=env, capture_output=True, text=True, timeout=10,
            )
            assert result.returncode == 0, result.stderr

        async def disconnect(self):
            pass

    monkeypatch.setattr(agent, "server_spec", spec)
    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", Client)

    async def exercise():
        sdk = agent.SdkAgent("source-checkout")
        try:
            await sdk._connect()
        finally:
            await sdk.close()

    asyncio.run(exercise())
