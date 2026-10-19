# Hosted MCP server (streamable HTTP). Mount the DuckDB file read-only:
#   docker build -t switrs-agent .
#   docker run -p 8765:8765 -v $PWD/data:/app/data:ro switrs-agent
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY switrs_agent ./switrs_agent
ENV SWITRS_DB=/app/data/switrs.duckdb
EXPOSE 8765
CMD ["uv", "run", "--no-dev", "python", "-m", "switrs_agent.mcp_server", "--http", "--host", "0.0.0.0", "--port", "8765"]
