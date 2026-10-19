# switrs-agent

A question-answering agent for California traffic collisions (SWITRS, 2001-01-01 to 2021-06-03, about 9.4 million crashes). You ask in plain English from your terminal. The agent writes SQL, runs it against a read-only DuckDB copy of the data, and answers. Every number in the answer has to bind to a cell in a query result, or the server refuses the answer. When a question needs data the file does not have, such as vehicle color, the answer says so.

![Terminal demo: direct tool calls, a rejected DROP, a question the data cannot answer, and a follow-up conversation](docs/demo.gif)

Video version: [docs/demo.mp4](docs/demo.mp4) (2 minutes). Every command in it ran for real on 2026-10-19 with the `sdk` backend; only the typing is simulated. `docs/demo.sh` re-records it.

![How a question becomes a checked answer](docs/architecture.png)

The diagram source is [docs/architecture.drawio](docs/architecture.drawio). The PNG and SVG embed the draw.io XML, so either opens for editing at app.diagrams.net.

## Quick start

You need Python 3.12, [uv](https://docs.astral.sh/uv/), and the SWITRS SQLite file from Kaggle ([alexgude/california-traffic-collision-data-from-switrs](https://www.kaggle.com/datasets/alexgude/california-traffic-collision-data-from-switrs)). The data is not in this repo.

```sh
git clone https://github.com/youngfreezy/switrs-agent && cd switrs-agent
uv sync
uv run python scripts/build_duckdb.py --sqlite ~/Downloads/switrs.sqlite   # about 4 minutes, writes data/switrs.duckdb (1.3 GB)
```

Then pick how the agent calls Claude:

| Backend | Needs | What runs |
|---|---|---|
| `api` | `ANTHROPIC_API_KEY` | Our own tool loop in `switrs_agent/agent.py` on the Claude Messages API |
| `sdk` | a logged-in `claude` CLI (a Claude subscription works) | The Claude Agent SDK with every built-in tool off. The only tools are this project's |

The default is `auto`, which uses `api` when `ANTHROPIC_API_KEY` is set and `sdk` otherwise. Override with `--backend` or `SWITRS_BACKEND`. The model is `claude-opus-5-5` unless you pass `--model` or set `SWITRS_MODEL`.

## Ask the agent

```sh
uv run switrs ask "Which county has the most collisions?"
uv run switrs ask "What percentage of collisions involved a parked vehicle?"
uv run switrs ask "How has COVID-19 changed collisions, if at all?"
uv run switrs ask "Is there data showing police are biased against red sports cars?" -v
```

Each tool call prints as it happens, then the checked answer. `-v` also prints every query plan, the SQL and the first rows of each result. The footer shows the backend, model turns, time, estimated cost and the trace file.

For follow-up questions in one conversation:

```sh
uv run switrs chat
```

```
you> Which county has the most collisions?
you> And which of those five had the most fatal collisions?
```

## Use the tools without a model

The same tools the agent calls are available directly. The SQL guard applies here too.

```sh
uv run switrs context                                   # the grounding text the agent reads first
uv run switrs describe parties                          # columns, meanings, null share, real values
uv run switrs values parties vehicle_make --contains porsch
uv run switrs sql "SELECT county_location, COUNT(*) AS n FROM collisions GROUP BY 1 ORDER BY 2 DESC LIMIT 5"
uv run switrs sql "DROP TABLE collisions"               # rejected: only one SELECT is allowed
uv run switrs trace <session>                           # HTML review page for a session's trace
```

## How an answer is checked

The tools live in `switrs_agent/tools.py`. `switrs_agent/mcp_server.py` exposes them, with the agent's instructions, as an MCP server. The CLI loads both from that server, so the CLI, Claude Code and any other MCP client see the same tools and the same rules.

| Tool | What it does |
|---|---|
| `get_context()` | Tables, joins, columns with meanings and real values, metric definitions, caveats, and concepts the data does not contain |
| `begin_question(question, answerability, interpretation)` | Opens a turn. The server checks the question for concepts the data lacks and for causal wording, and returns the gaps the answer must state |
| `describe_table(table)`, `distinct_values(table, column, contains, limit)` | Look up columns and how categories are spelled |
| `run_sql(sql, purpose, query_plan)` | One guarded, read-only SELECT. Returns a query id, up to 200 rows, warnings and caveats |
| `submit_answer(turn_id, answer, claims)` | The only certification. Every number in the answer must be a claim bound to query result cells, with its entity and direction. On success the server renders the final answer with gaps, caveats and an evidence table, and the CLI prints exactly that |
| `get_trace()` | Every tool call in the session, including rejected queries and retries |
| `render_answer(turn_id)` | An HTML page for a certified answer: query plans, SQL, results and the trace |

A run usually goes: `get_context`, `begin_question`, one or more `run_sql`, then `submit_answer` until it is accepted. If no submission passes, the CLI prints the model's last text under a red "unverified" banner.

The database is opened read-only, with external file access off and the configuration locked. Before any SQL runs, the guard parses it with sqlglot, allows a single SELECT, blocks file-reading functions, rejects unknown tables and columns, and dry-runs the query with EXPLAIN.

Every session appends each tool call (arguments, query plans, SQL, results, rejected attempts) to `outputs/traces/<session>.jsonl`.

## Other ways to run it

Claude Code, Claude Desktop, Cursor or Codex: open the folder. `.mcp.json` registers the `switrs` server.

Local web chat (streams tool calls as cards, keeps chats):

```sh
uv run uvicorn ui.server:app --host 127.0.0.1 --port 8800
```

Hosted MCP over HTTP:

```sh
uv run python -m switrs_agent.mcp_server --http --port 8765    # streamable HTTP at /mcp
docker build -t switrs-agent . && docker run -p 8765:8765 -v $PWD/data:/app/data:ro switrs-agent
```

The HTTP mode has no authentication. Add auth before exposing it beyond localhost.

## Tests and evals

```sh
uv run pytest -q                                            # guard, claim-checking and adversarial tests
uv run python evals/run_evals.py --expected                 # recompute golden values from SQL, no model calls
uv run python evals/run_evals.py --agent claude --budget 2  # run the golden questions through the agent
```

## Known limits

- A passing `submit_answer` proves each number came from a cited result cell. It does not prove the query measured what the question asked. Read the query plans (`switrs ask -v`) for anything that matters.
- Each claim contains one numeric assertion. Labelled clauses start with the exact entity and a simple predicate ("Los Angeles had 2,851,925 crashes"). Use separate claims for separate clauses; numbers repeated from the question still require evidence. Calendar years in context such as "in 2020" remain labels.
- SQL provenance follows CTEs, subqueries, and wildcard outputs. A set-operation column is citable only if every branch is data-derived. Mixed literal/data columns cannot be cited, even for their data-derived rows.
- Joins through simple projections and filters preserve known keys, including renamed key columns. Joins through aggregates or other complex derived relations are rejected when their keys cannot be established. Use base-table joins or `WHERE EXISTS` for person filters. Use `EXISTS`/`NOT EXISTS` instead of explicit `SEMI`/`ANTI JOIN`. Fan-out state is retained through window functions and grouped derived results; the guard can conservatively reject an aggregate whose deduplication it cannot prove.
- The model can still add hedged speculation about causes in prose, as in the fatal-share answer in the demo.
- The data ends 2021-06-03, and the last months before that are only partly loaded.
- The `api` backend has not yet run against the live API (the build machine had no API key). The demo ran on the `sdk` backend.
- The HTTP MCP server has no authentication.
