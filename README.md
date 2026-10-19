# switrs-agent

Ask questions about California traffic collisions using the Statewide Integrated Traffic Records System (SWITRS) dataset. Answers include query evidence and dataset limitations.

**The server controls what can be claimed.** It matches supported questions to approved SQL and renders the answer itself. Model-written additions are rejected. Unsupported questions receive a clarification or refusal.

## Quick start

You need Python 3.12+, [uv](https://docs.astral.sh/uv/), and the extracted SQLite file from the [Kaggle SWITRS dataset](https://www.kaggle.com/datasets/alexgude/california-traffic-collision-data-from-switrs). The dataset is not included.

```sh
git clone https://github.com/youngfreezy/switrs-agent
cd switrs-agent
uv sync
uv run python scripts/build_duckdb.py --sqlite ~/Downloads/switrs.sqlite
uv run python scripts/reconcile.py --sqlite ~/Downloads/switrs.sqlite
```

Replace `~/Downloads/switrs.sqlite` with your dataset path. Reconciliation must pass before the agent runs.

Use a logged-in `claude` CLI, or set `ANTHROPIC_API_KEY` for the API backend. The agent selects the API backend when that variable is set; otherwise, it uses the Claude Agent SDK.

```sh
uv run switrs ask "Which county has the most collisions?"
uv run switrs ask "How many collisions in Orange County in 2019?" -v
uv run switrs chat
```

Add `-v` to inspect the SQL and query results. For backend options and local web chat, read the [usage guide](docs/usage.md).

## Supported questions

| Ask about | Example |
|---|---|
| Counts by county and year | How many fatal collisions happened in Los Angeles County in 2019? |
| County rankings | Which county has the most collisions? |
| Parked-vehicle definitions | What percentage of collisions involved a parked vehicle? |
| Recorded equipment outcomes | What are recorded victim death shares by lap/shoulder belt use? |
| Period comparisons | How has COVID-19 changed collisions, if at all? |

Certification uses defined question forms. Extra filters, arbitrary paraphrases, and ambiguous follow-ups can require clarification. The data cannot establish causal effects or police bias, and it lacks vehicle color and traffic-exposure data.

## Evidence and limits

- **Read-only SQL:** guarded queries run against a reconciled DuckDB copy of the source dataset.
- **Controlled answers:** the server owns the metric, query, wording, and evidence. The model must submit the prepared answer unchanged.
- **Visible evidence:** answers include result-cell references and caveats; traces retain SQL and returned rows.
- **Bounded coverage:** the snapshot spans 2001-01-01 through 2021-06-03, with incomplete recent months. It contains police-reported collisions.

This is a local research tool. Public multi-user hosting still needs authentication and process isolation. Approved metric implementations can contain bugs; certification does not guarantee universal correctness.

## Validation

Version 0.2 passed **479 tests** and **11/11 live example questions** on the full reconciled snapshot on 2026-10-19. These are known examples, not a generalization benchmark.

```sh
uv run pytest -q
uv build
```

For dataset requirements, evaluation commands, and review results, read [validation and development](docs/validation.md).

## Documentation

- [Usage and integration](docs/usage.md): backends, web chat, direct SQL, and Model Context Protocol (MCP) clients.
- [Certification contract](docs/certification.md): supported metrics, client requirements, and how to add question forms.
- [Changelog](CHANGELOG.md): version 0.2 behavior changes.
