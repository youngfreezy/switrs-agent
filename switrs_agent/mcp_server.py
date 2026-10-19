"""MCP server exposing the SWITRS tools to any MCP harness (Claude Code, Claude Desktop, Cursor, Codex).

stdio (local):   uv run python -m switrs_agent.mcp_server
HTTP (hosted):   uv run python -m switrs_agent.mcp_server --http --port 8765
"""

from __future__ import annotations

import argparse
import os
import pathlib

from mcp.server.mcpserver import MCPServer

from .tools import Toolkit

INSTRUCTIONS = """\
You answer questions about California SWITRS collision records using the tools.
For each question:
1. Call get_context for the schema and limitations.
2. Call begin_question with the ORIGINAL user question unchanged. Put any proposed
   interpretation in interpretation; the server does not trust it for certification.
3. Call prepare_answer(turn_id). The server recognizes supported question forms,
   compiles approved metrics, runs queries, and returns answer, claims, and status.
   Unsupported conditions or follow-ups produce a clarification/refusal. Do not
   remove conditions or substitute a proxy to get an answer.
4. Call submit_answer with the EXACT answer and claims returned by prepare_answer.
   Never add, rewrite, or remove prose or claims. Render final_answer unchanged.
If execution fails, report the failure; do not turn it into a zero or invented answer.
run_sql, describe_table, and distinct_values are exploration tools. Arbitrary SQL
results cannot certify factual assertions. Never present exploratory text as the
certified answer. An unsupported question must remain unsupported until clarified.
"""


def build(toolkit: Toolkit | None = None) -> MCPServer:
    request_path = os.environ.get("SWITRS_REQUEST_FILE")
    kit = toolkit or Toolkit(session_id=os.environ.get("SWITRS_SESSION"),
                             request_path=pathlib.Path(request_path) if request_path else None)
    server = MCPServer(name="switrs", instructions=INSTRUCTIONS)

    @server.tool()
    def get_context() -> str:
        """Grounding context for the SWITRS database: tables, joins, columns with meanings and
        real values, metric definitions, caveats, and concepts the data does not contain."""
        return kit.get_context()

    @server.tool()
    def begin_question(question: str, answerability: str, interpretation: str) -> dict:
        """Open a turn for one user question. answerability: answerable, ambiguous,
        descriptive_only, partially_available or unavailable. interpretation: the population,
        metric and period you will measure. Returns turn_id, detected gaps and required caveats,
        or an error when the status contradicts the question."""
        return kit.begin_question(question, answerability, interpretation)

    @server.tool()
    def prepare_answer(turn_id: str) -> dict:
        """Compile the original bound question with approved metrics and render its answer.
        Returns answer, claims, status, and interpretation. Submit answer and claims unchanged.
        Does not accept model SQL or replacement question text. Unsupported forms ask for clarification.
        """
        return kit.prepare_answer(turn_id)

    @server.tool()
    def submit_answer(turn_id: str, answer: str, claims: list[dict]) -> dict:
        """Publish the exact answer and claims returned by prepare_answer for this turn.
        Model-written or edited prose, claims, and arbitrary SQL evidence are rejected.
        Show final_answer unchanged. Only a result with certified=true is a certificate.
        """
        return kit.submit_answer(turn_id, answer, claims)

    @server.tool()
    def describe_table(table: str) -> dict:
        """List the columns of one table (collisions, parties, victims, case_ids) with types,
        meanings, null share, and real values."""
        return kit.describe_table(table)

    @server.tool()
    def distinct_values(table: str, column: str, contains: str | None = None, limit: int = 50) -> dict:
        """Most common values of a column with counts. `contains` filters case-insensitively."""
        return kit.distinct_values(table, column, contains, limit)

    @server.tool()
    def run_sql(sql: str, purpose: str, query_plan: str) -> dict:
        """Run one read-only DuckDB SELECT against the SWITRS tables. Returns a query_id,
        columns, up to 200 rows, warnings (such as fan-out counting), and caveats that apply.
        `purpose`: one sentence naming the tables joined and why.
        `query_plan`: plan for the user to review (what is measured, columns and definitions
        used, filters, risks, expected range). Logged in the trace."""
        return kit.run_sql(sql, purpose, query_plan)

    @server.tool()
    def get_trace() -> dict:
        """Every tool call made in this session, in order: arguments, query plans, SQL, results,
        rejected queries and retries."""
        return kit.get_trace()

    @server.tool()
    def render_answer(turn_id: str) -> dict:
        """Write a certified answer (one that passed submit_answer) as a self-contained HTML page with
        query plans, SQL, results and the full trace. Returns the file path."""
        return kit.render_answer(turn_id)

    return server


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", action="store_true")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    server = build()
    if args.http:
        server.run("streamable-http", host=args.host, port=args.port)
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
