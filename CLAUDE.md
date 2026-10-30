# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## What this repo is

record-mcp is a catalog of regulatory and policy violations for RECORD traces (https://github.com/vigilai-tech/record-spec), served as an MCP server built on the official Python SDK (`mcp` 2.x, spec revision 2026-07-28). It has two parts:
- the rule catalog in `rules/`
- the engine and server in `src/record_mcp/`

## Commands

```bash
uv sync                                         # install (Python >= 3.10)
uv run pytest                                   # tests
uv run ruff check .                             # lint
uv run record-mcp serve                         # MCP over stdio
uv run record-mcp serve --transport streamable-http --port 8000
uv run record-mcp check --chain examples/aml_case_closed_without_review.jsonl
uv run python scripts/update_readme.py          # regenerate README rule table
uv run python scripts/sync_schema.py --check    # vendored schema == pinned record-spec commit
```

## Layout

- **`rules/<category>/<RULE-ID>.yaml`:** one rule per file, and the file name must equal the `id`. `rules/frameworks.yaml` registers citation frameworks, `authority_basis` prefixes, and which record-py gap analyses each trace triggers.
- **`src/record_mcp/checks/`:** Python checks registered with `@trace_check` / `@chain_check`, plus `builtin.py`, which wraps record-py's `RecordValidator` and `GapAnalyzer`.
- **`catalog.py`:** loads the YAML. It also generates the `GAP.<regulation>.<field>` rules by calling `GapAnalyzer(reg).analyze({})`.
- **`engine.py`:** selects rules (framework filter, `applies_to`, active gap regulations) and groups chains by case_id, then investigation_thread_id. It sorts each chain by timestamp and builds the pydantic reports defined in `models.py`.
- **`server.py`:** the MCPServer tools, resources, and prompt.
- **`cli.py`:** the `serve`, `check`, and `rules` commands.

## Conventions

- Every rule cites at least one framework from `frameworks.yaml`. Don't add a rule without a regulatory citation (same policy as record-spec's REGULATORY_MAPPING.md).
- Every catalog rule has a triggering case in `TRACE_CASES` or `CHAIN_CASES`, which `test_every_trace_rule_has_a_case` enforces.
- Checks must tolerate schema-invalid input. Use the `checks/_util.py` accessors, and never index raw fields.
- Checks are deterministic: no network, no wall clock, no LLM calls.
- `path` (file) input is allowed only over stdio. `build_server(allow_file_input=False)` is used for HTTP.
- Logging goes to stderr. stdout is the MCP channel under stdio.
- `src/record_mcp/data/schema.json` is vendored from the record-spec commit pinned for record-py in `pyproject.toml`. Update the two together.
- `tests/fixtures/examples/` are unmodified copies of record-spec's examples. Tests mutate deep copies.
