# Contributing to record-mcp

record-mcp is a public catalog of violations for RECORD traces. The most valuable contributions are new rules backed by a real regulatory obligation, and fixes to citations.

## Ground rules

- **Every rule needs at least one citation** to a framework in [`rules/frameworks.yaml`](rules/frameworks.yaml), naming the article or section, e.g. `eu_ai_act` / `Art. 14(4)`. Where possible, reuse the citations in record-spec's [REGULATORY_MAPPING.md](https://github.com/vigilai-tech/record-spec/blob/main/REGULATORY_MAPPING.md). Rules that come only from the RECORD spec itself cite `record_spec`.
- **Every rule needs a test that triggers it.** CI fails if a catalog rule has no case in `TRACE_CASES` (`tests/test_trace_rules.py`) or `CHAIN_CASES` (`tests/test_chain_rules.py`).
- **Rules must be deterministic.** No network calls, no wall-clock time (chain rules measure time between traces), and no LLM judgment.
- **Avoid false positives.** The reference examples in `tests/fixtures/examples/` must not trigger a new rule unless they really violate it. If they do, say so in the PR.

## Adding a rule

1. **Pick or write a check.** If a check in `src/record_mcp/checks/trace.py` or `chain.py` already implements the logic and only needs different `params`, reuse it. Otherwise add a function:

   ```python
   @trace_check("my_check_name")            # or @chain_check(...) for cross-trace rules
   def my_check_name(doc: dict, rule: Rule) -> Iterator[Finding]:
       if <violation>:
           yield Finding(message="…", path="/json/pointer", evidence={...})
   ```

   Traces may be schema-invalid, so read fields only through the helpers in `checks/_util.py` (`obj`, `items`, `text`, `parse_ts`, …). Never assume a field's shape.

2. **Add the YAML** at `rules/<category>/<RULE-ID>.yaml`. The file name must equal the `id`. Look at an existing rule for the fields: `id`, `title`, `scope`, `severity`, `category`, `check`, `params`, `applies_to`, `citations`, `description`, `remediation`, `fields`.

3. **Add the test case.** Add an entry to `TRACE_CASES` or `CHAIN_CASES` that mutates the clean AML example so the rule fires. Add a negative test for any exemption the rule has.

4. **Regenerate the README table:** `uv run python scripts/update_readme.py`.

5. **Run everything:**

   ```bash
   uv sync
   uv run ruff check .
   uv run pytest
   uv run python scripts/update_readme.py --check
   uv run python scripts/sync_schema.py --check
   ```

## Severity guide

| Severity | Use when |
|---|---|
| `critical` | The record can't be trusted as evidence at all (e.g. duplicate IDs). |
| `high` | An obligation was likely breached: missing human oversight, a broken integrity proof, a missed deadline. |
| `medium` | The evidence is incomplete or inconsistent in a way an examiner would raise. |
| `low` | A hygiene issue that weakens the record without breaching an obligation. |
| `info` | Context only. |

## Updating RECORD

To track a new record-spec release:
1. Bump the `record-py` commit pin in `pyproject.toml`.
2. Run `uv lock && uv run python scripts/sync_schema.py`.
3. Refresh `tests/fixtures/examples/` from record-spec's `examples/`.
4. Re-run the tests and report any new findings on the reference examples.
