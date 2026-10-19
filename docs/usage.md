# Usage and integration

[Project overview](../README.md) · [Certification contract](certification.md) · [Validation](validation.md)

Run commands from the repository root after completing the README setup.

## Choose a backend

| Backend | Needs | What runs |
|---|---|---|
| `api` | `ANTHROPIC_API_KEY` | Our own tool loop in `switrs_agent/agent.py` on the Claude Messages API |
| `sdk` | a logged-in `claude` CLI (a Claude subscription works) | The Claude Agent SDK with every built-in tool off. The only tools are this project's |

The default is `auto`, which uses `api` when `ANTHROPIC_API_KEY` is set and `sdk` otherwise. Override with `--backend` or `SWITRS_BACKEND`. The model is `claude-opus-5-5` unless you pass `--model` or set `SWITRS_MODEL`.

## Ask questions

```sh
uv run switrs ask "Which county has the most collisions?"
uv run switrs ask "How many fatal collisions happened in Los Angeles County in 2019?" -v
uv run switrs chat
```

Add `-v` to inspect query plans, SQL, and result rows. Each run prints tool activity, the certified answer, and a trace path. In chat, repeat the explicit county, period, and metric; ambiguous follow-ups require clarification.

A certified refusal is a successful command, but it is not a numerical answer. `switrs ask` exits nonzero when no certificate is published or the model reports an error.

## Explore without a model

The same tools the agent calls are available directly. The SQL guard applies here too.

```sh
uv run switrs context                                   # the grounding text the agent reads first
uv run switrs describe parties                          # columns, meanings, catalog categories
uv run switrs values parties vehicle_make --contains porsch
uv run switrs sql "SELECT county_location, COUNT(*) AS n FROM collisions GROUP BY 1 ORDER BY 2 DESC LIMIT 5"
uv run switrs sql "DROP TABLE collisions"               # rejected: only one SELECT is allowed
uv run switrs trace <session>                           # HTML review page for a session's trace
```

Exploratory query results cannot supply a certificate. Use the approved metric workflow to publish an answer.

## Tool reference

| Tool | What it does |
|---|---|
| `get_context()` | Tables, joins, columns with meanings and real values, metric definitions, caveats, and concepts the data does not contain |
| `begin_question(question, answerability, interpretation)` | Opens a turn. The server checks the question for concepts the data lacks and for causal wording, and returns the gaps the answer must state |
| `describe_table(table)`, `distinct_values(table, column, contains, limit)` | Look up columns and how categories are spelled |
| `run_sql(sql, purpose, query_plan)` | One guarded, read-only SELECT. Returns a query id, up to 200 rows, warnings and caveats |
| `prepare_answer(turn_id)` | Compiles the original bound question using approved metrics, executes the SQL, and returns server-rendered `answer`, `claims`, `status`, and `interpretation` |
| `submit_answer(turn_id, answer, claims)` | Accepts only the exact answer and claims from `prepare_answer`. Returns `certified: true` and the final answer with caveats and evidence |
| `get_trace()` | Every tool call in the session, including rejected queries and retries |
| `render_answer(turn_id)` | An HTML page for a certified answer: query plans, SQL, results and the trace |

## Connect a client

Claude Code, Claude Desktop, Cursor or Codex: open the folder. `.mcp.json` registers the `switrs` server for exploration. Certification requires a host request binding; the default standalone configuration does not supply one.

The CLI, web chat and built-in eval runner bind requests automatically. A custom host must either call `Toolkit.bind_question(question)` before each in-process turn, or set `SWITRS_REQUEST_FILE` to a host-controlled JSON file containing `{"id": "fresh-request-id", "question": "original user text"}`. Replace the file atomically and use a fresh ID for every question, including repeated questions. Keep this file outside the model's writable tools. The host must bind the question before `begin_question`; setting the file after opening a turn does not certify that turn. Unbound Model Context Protocol (MCP) clients can run read-only queries but `submit_answer` refuses certification.

Local web chat at http://127.0.0.1:8800. It streams each tool call as a card (query plan, SQL, rows) and keeps chats. Follow-ups resume the same Claude session, and messages to one chat run one at a time.

```sh
uv run uvicorn ui.server:app --host 127.0.0.1 --port 8800
```

The web chat and `evals/run_evals.py --agent claude` run headless Claude Code, billed to whatever the `claude` CLI is logged in to. To pin a Claude subscription for this project only, put a token from `claude setup-token` in `.env` as `CLAUDE_CODE_OAUTH_TOKEN=...`. Both refuse to run on API-key billing unless `.env` sets `SWITRS_ALLOW_API_BILLING=1`.

Hosted MCP over HTTP:

```sh
uv run python -m switrs_agent.mcp_server --http --port 8765    # streamable HTTP at /mcp
docker build -t switrs-agent . && docker run -p 8765:8765 -v $PWD/data:/app/data:ro switrs-agent
```

The HTTP mode has no authentication. Add auth before exposing it beyond localhost.

## Historical demos

The [recording](demo.mp4), [terminal animation](demo.gif), and [architecture source](architecture.drawio) describe version 0.1. They do not show the version 0.2 certification workflow. To inspect that workflow, read the [certification contract](certification.md).
