"""Call one tool on a running switrs MCP server over HTTP and print the result.

Lets a shell-only agent (a subagent with Bash, a cron job) use the same tools as an MCP harness.
Start the server first:  uv run python -m switrs_agent.mcp_server --http --port 8765

usage:
  uv run python scripts/mcp_call.py --port 8765 --instructions
  uv run python scripts/mcp_call.py --port 8765 get_context
  uv run python scripts/mcp_call.py --port 8765 run_sql --args-file call.json
  echo '{"table": "parties"}' | uv run python scripts/mcp_call.py --port 8765 describe_table --args -
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

from mcp import Client


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tool", nargs="?")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--args", help="JSON object, or - to read it from stdin")
    ap.add_argument("--args-file", type=pathlib.Path)
    ap.add_argument("--instructions", action="store_true", help="print the server instructions")
    a = ap.parse_args()

    raw = "{}"
    if a.args_file:
        raw = a.args_file.read_text()
    elif a.args == "-":
        raw = sys.stdin.read()
    elif a.args:
        raw = a.args
    arguments = json.loads(raw)

    async with Client(f"http://{a.host}:{a.port}/mcp") as client:
        if a.instructions or not a.tool:
            print(client.instructions or "")
            return
        result = await client.call_tool(a.tool, arguments)
        for block in result.content:
            text = getattr(block, "text", None)
            print(text if text is not None else block)
        if result.is_error:
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
