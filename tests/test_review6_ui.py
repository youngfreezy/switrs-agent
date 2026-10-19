"""Exercise the real HTTP streaming route with a deterministic model-process substitute."""
import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from switrs_agent.request_binding import read_request
from ui import server
from ui.store import JsonChatStore


@pytest.mark.parametrize('matches,failed_revision', [(True, False), (False, False), (True, True)])
def test_web_publishes_only_latest_matching_certificate(tmp_path, monkeypatch, matches, failed_revision):
    monkeypatch.setattr(server, 'ROOT', tmp_path)
    monkeypatch.setattr(server, 'WORK_CWD', tmp_path / 'cwd')
    monkeypatch.setattr(server, 'store', JsonChatStore(tmp_path / 'chats.json'))
    monkeypatch.setattr(server, 'slots', asyncio.Semaphore(2))
    monkeypatch.setattr(server, 'chat_locks', {})
    monkeypatch.setattr(server, 'billing_status', lambda: {'ok': True})
    monkeypatch.setattr(server, 'claude_env', lambda: {})
    question = 'How many collisions?'
    result = {'ok': True, 'final_answer': 'There were 3 collisions.',
              'question': question if matches else 'A different question'}

    def call_result(id, result):
        return [
            {'type': 'assistant', 'message': {'content': [
                {'type': 'tool_use', 'id': id, 'name': 'mcp__switrs__submit_answer', 'input': {}}]}},
            {'type': 'user', 'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': id, 'content': json.dumps(result)}]}}
        ]

    events = call_result('first', result)
    if failed_revision:
        events += call_result('second', {'ok': False, 'errors': ['Unsupported claim']})
    events += [{'type': 'result', 'is_error': False, 'num_turns': 1}]

    class Process:
        returncode = None
        async def lines(self):
            for event in events:
                yield (json.dumps(event) + '\n').encode()
        @property
        def stdout(self):
            return self.lines()
        async def wait(self):
            self.returncode = 0
        def kill(self):
            self.returncode = -9

    async def create_process(*cmd, **kwargs):
        config = json.loads(Path(cmd[cmd.index('--mcp-config') + 1]).read_text())
        env = config['mcpServers']['switrs']['env']
        assert read_request(Path(env['SWITRS_REQUEST_FILE']))['question'] == question
        return Process()

    monkeypatch.setattr(server.asyncio, 'create_subprocess_exec', create_process)
    with TestClient(server.app) as client:
        response = client.post('/api/chat', json={'message': question})
    assert response.status_code == 200
    emitted = [json.loads(line.removeprefix('data: ')) for line in response.text.splitlines() if line.startswith('data: ')]
    certified = [e for e in emitted if e['kind'] == 'certified']
    assert bool(certified) == (matches and not failed_revision)
    assert sum(e['kind'] in ('certified', 'uncertified') for e in emitted) == 1
    assert emitted[-1]['kind'] in ('certified', 'uncertified')
