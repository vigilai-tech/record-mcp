FROM ghcr.io/astral-sh/uv:0.10.9 AS uv

FROM python:3.13-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
# git is needed to install record-py from the pinned record-spec commit.
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY rules ./rules
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.13-slim
RUN useradd --system --uid 10001 record
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" RECORD_MCP_HOST=0.0.0.0 RECORD_MCP_PORT=8000
USER record
EXPOSE 8000
ENTRYPOINT ["record-mcp", "serve", "--transport", "streamable-http"]
