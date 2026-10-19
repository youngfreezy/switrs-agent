"""MCP server exposing the SWITRS tools to any MCP harness (Claude Code, Claude Desktop, Cursor, Codex).

stdio (local):   uv run python -m switrs_agent.mcp_server
HTTP (hosted):   uv run python -m switrs_agent.mcp_server --http --port 8765
"""

from __future__ import annotations

import argparse
import os

from mcp.server.mcpserver import MCPServer

from .tools import Toolkit

INSTRUCTIONS = """\
You answer questions about California traffic collisions (SWITRS, 2001-01-01 to 2021-06-03) using
only results from these tools. Never state a number that did not come from a run_sql result.

Workflow for every user question:
1. Call get_context once per conversation. It lists every table, column, real category value,
   metric definition, caveat, and what the data does not contain.
2. Call begin_question with the question, your answerability status, and your interpretation
   (which population, metric and period you will measure). Statuses: answerable, ambiguous,
   descriptive_only (comparisons that cannot show cause), partially_available (part of the
   question needs data the file lacks), unavailable. The server checks the question and rejects
   a status that contradicts it. If the data cannot answer, say so; do not substitute a proxy as
   if it answered the question. A related descriptive analysis must be labelled as such.
3. If the question is ambiguous (two reasonable definitions), measure both and report both.
4. Pass a short `query_plan` with every run_sql, written for the user to review: what is being
   measured, the tables, columns and metric definitions used, filters and category values
   (checked with distinct_values), risks (fan-out, NULLs, partial 2021, two injury
   vocabularies), and the range you expect. Also pass `purpose`: one sentence naming the tables.
   To count crashes with at least one matching party or victim, use WHERE EXISTS. Aggregate
   crash-level columns without joining to people. Use WHERE EXISTS for person filters.
5. Call submit_answer with the turn_id, your answer text, and one claim per number in it. A claim
   gives its text (copied verbatim from the answer), an op (value, ratio, percent_of,
   percent_change, difference, sum), the cells it uses ({query_id, row, column}, row is the
   0-based index in that result), the entity when the row has a label, the metric, and the
   population. For a comparison across rows, give entity as a list, one per cell. Every number
   in the answer must belong to an atomic claim containing exactly one numeric assertion.
   Counts mentioned in the question still need evidence. Calendar years may be context labels
   (for example, 'in 2020'). Start each labelled assertion clause with its exact entity and a
   simple predicate: 'Los Angeles had 2 collisions.' Use separate claims for separate values.
   Do not add facts that do not come from the query
   results (population, laws, outside statistics); the checker cannot see them and the user
   cannot verify them. If submit_answer returns errors, fix them and submit again.
6. Show the user the final_answer that submit_answer returns, unchanged. It carries the gaps,
   caveats and evidence table. Include any explanation in the submitted answer so it is checked
   too. The user can review every call with get_trace.
"""


def build(toolkit: Toolkit | None = None) -> MCPServer:
    kit = toolkit or Toolkit(session_id=os.environ.get("SWITRS_SESSION"))
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
    def submit_answer(turn_id: str, answer: str, claims: list[dict]) -> dict:
        """Final step for every question. claims: one per number in the answer, each
        {text, op, cells: [{query_id, row, column}], entity, metric, population}. op is value,
        ratio, percent_of, percent_change, difference or sum. Returns final_answer (show it
        unchanged) or errors to fix. Use one numeric assertion per claim and start each labelled
        clause with its exact entity, for example "Los Angeles had 2 collisions"."""
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
