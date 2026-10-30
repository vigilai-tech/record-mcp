# record-mcp

[![CI](https://github.com/vigilai-tech/record-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/vigilai-tech/record-mcp/actions/workflows/ci.yml)
![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)
![MCP 2026-07-28](https://img.shields.io/badge/MCP-2026--07--28-purple.svg)
![RECORD v0.1.0](https://img.shields.io/badge/RECORD-v0.1.0-green.svg)

**A catalog of regulatory and policy violations for [RECORD](https://github.com/vigilai-tech/record-spec) audit traces, served as an MCP server.**

[RECORD](https://github.com/vigilai-tech/record-spec) defines what a financial AI agent's audit trace must contain. Schema validation tells you whether a trace is well-formed. record-mcp answers the question an examiner asks next: **given this trace, or this chain of traces for one case, which obligations did the decision violate, and why?**

Give any MCP client (Claude Code, Claude Desktop, an agent framework, or your CI) a trace, and it gets back structured violations. Each violation carries the JSON path, the evidence, the regulation it breaches, and how to fix it.

```text
$ record-mcp check --chain examples/aml_case_closed_without_review.jsonl

Status: FAIL   critical 0 · high 3 · medium 1 · low 3 · info 0
Chains: case_id:AML-2026-WR-019847

Chain violations
  [HIGH    ] REC-CHAIN-003  /final_action/type
             Case was escalated in 0b6c3f4e-… and later closed (`close_alert`) in 7f2a1d8b-…
             with no human disposition recorded in between.
             ↳ eu_ai_act Art. 14(4); finra Rule 3110; eu_mar Art. 16
  [LOW     ] REC-CHAIN-002  /agent_version
             Agent `aml-triage-agent` ran 2 different versions (2.1.4, 2.2.0) within one case; …

Trace 1 (7f2a1d8b-5c3e-4b6a-9d07-e4c1a2b3f59c)
  [HIGH    ] REC-ESC-001  /final_action/escalated
             2 policy rule(s) failed but the decision was neither escalated nor reviewed by a human.
             ↳ eu_ai_act Art. 14(4); eu_mar Art. 16(2); bsa_fincen 31 CFR 1010.320; finra Rule 3110
  …
```

[`examples/aml_case_closed_without_review.jsonl`](examples/aml_case_closed_without_review.jsonl) is a two-trace AML case. The agent escalates a $47k wire for a SAR, and then a newer agent version closes the alert itself three days later.

## What it checks

| Kind | Scope | Source |
|---|---|---|
| **Schema conformance** | one trace | RECORD `schema.json`, via record-py's `RecordValidator` |
| **Behavioral rules** | one trace | human oversight, escalation, temporal ordering, data provenance, integrity |
| **Chain rules** | traces sharing a `case_id` / `investigation_thread_id` | duplicate IDs, escalations closed without review, human overrides reasserted, outcome regressions, SAR deadlines |
| **Regulation field gaps** | one trace | required/recommended fields per regulation, generated from record-py's `GapAnalyzer` (FinCEN NPRM 2026, EU AI Act Art. 86, MiFID II RTS 6) |

Every rule cites at least one regulatory article. Gap analyses run automatically for the regulations named in a trace's `authority_basis`, or for the frameworks you ask for.

## Install

Requires Python ≥ 3.10. Until it is published, install from GitHub:

```bash
uv tool install git+https://github.com/vigilai-tech/record-mcp
```

Or from a checkout:

```bash
git clone https://github.com/vigilai-tech/record-mcp && cd record-mcp && uv sync
```

## Use it from an MCP client

**Claude Code**

```bash
claude mcp add record-mcp -- record-mcp serve
```

**Claude Desktop**: add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "record-mcp": { "command": "record-mcp", "args": ["serve"] }
  }
}
```

From a checkout, use `"command": "uv", "args": ["--directory", "/path/to/record-mcp", "run", "record-mcp", "serve"]`.

Then ask, for example: *"Audit examples/aml_triage.json with record-mcp"* or *"Check every trace in case.jsonl as a chain and summarise the violations for the examiner."*

### Hosted (Streamable HTTP)

```bash
record-mcp serve --transport streamable-http --host 0.0.0.0 --port 8000 \
  --allowed-host record-mcp.internal.example:8000
```

- **Protocols:** serves MCP 2026-07-28 (stateless) clients and 2025-era clients on the same `/mcp` endpoint.
- **Scaling:** the server keeps no session state, so any replica can answer any request.
- **No file paths:** over HTTP, traces must be passed inline. The `path` argument is only available over stdio.
- **`--allowed-host`** (or `$RECORD_MCP_ALLOWED_HOSTS`, comma-separated) turns on DNS-rebinding protection for real hostnames.
- **No built-in auth** in v0.1. Put it behind your API gateway or auth proxy.

```bash
docker build -t record-mcp .
docker run -p 8000:8000 -e RECORD_MCP_ALLOWED_HOSTS=localhost:8000 record-mcp
```

## Tools

All tools are read-only and idempotent, and they return structured output (`outputSchema`).

| Tool | Purpose |
|---|---|
| `check_trace` | Check one trace (inline JSON or `path`). Returns `status` (`pass` / `warn` / `fail`), counts by severity, and `violations[]`. |
| `check_trace_chain` | Check many traces: every per-trace rule plus the chain rules. `group_by`: `auto` (case_id, then investigation_thread_id), `case_id`, `investigation_thread_id`, or `none`. |
| `list_rules` | Browse the catalog by `framework`, `scope`, or `category`. |
| `get_rule` | Full rule definition: description, params, citations, remediation. |
| `list_frameworks` | Framework registry, `authority_basis` prefixes, and gap-analysis triggers. |

Common arguments:
- **`input_format`:** `record`, or `openinference` (spans are converted with record-py's `adapt_span`).
- **`frameworks`:** keep only rules citing these frameworks, e.g. `["eu_ai_act"]`.
- **`min_severity`:** drop violations below this severity.
- **`include_gaps`:** set to `false` to leave out the field-gap rules.

**Status** is `fail` when any violation is high or critical, `warn` when only lower-severity violations remain, and `pass` when there are none.

**Resources:** `record://schema`, `record://rules`, `record://rules/{rule_id}` (rule YAML). **Prompt:** `audit_trace` produces an examiner-ready summary.

A violation looks like this:

```json
{
  "rule_id": "REC-PROV-003",
  "title": "Data payload hash is not a SHA-256 digest",
  "severity": "high",
  "category": "provenance",
  "scope": "trace",
  "message": "data_at_decision[0] (`ofac-sdn-api`) payload_hash is not a 64-character SHA-256 hex digest, so the payload cannot be verified.",
  "record_ids": ["b9d4e7f2-3c1a-4b8e-9f5d-6a2e0c3b7d1f"],
  "path": "/data_at_decision/0/payload_hash",
  "evidence": { "payload_hash": "abc123" },
  "citations": [
    { "framework": "sec", "ref": "17a-4(f)" },
    { "framework": "eu_ai_act", "ref": "Art. 12(2)(b)" },
    { "framework": "mifid2", "ref": "RTS 6 Art. 28" }
  ],
  "remediation": "Hash the raw response bytes with SHA-256 and store the lowercase hex digest."
}
```

## Use it in CI

The same engine is available without MCP:

```bash
record-mcp check traces/*.json                    # one report per trace
record-mcp check --chain case-1234.jsonl          # chain rules across all traces given
record-mcp check --format json --fail-on medium traces/*.jsonl
record-mcp rules --format markdown                # the catalog
```

The exit code is `1` when any violation reaches `--fail-on` (default `high`), and `2` on unreadable input.

## The catalog

Rules live in [`rules/`](rules/), one YAML file per rule, grouped by category. Each file names the Python check that implements it (in [`src/record_mcp/checks/`](src/record_mcp/checks/)). Thresholds and action lists are `params`, so they can be tuned without code. [`rules/frameworks.yaml`](rules/frameworks.yaml) registers the frameworks that citations and `authority_basis` prefixes refer to.

<!-- rules:start -->
| Rule | Scope | Severity | Title | Citations |
|---|---|---|---|---|
| `REC-CHAIN-001` | chain | critical | Duplicate record_id across traces | eu_ai_act Art. 12(1); sec 17a-4(a); finra Rule 4511; bsa_fincen 31 CFR 1010.430 |
| `REC-CHAIN-002` | chain | low | Agent version changed mid-case | eu_ai_act Art. 13(3)(c); mifid2 RTS 6 Art. 11 |
| `REC-CHAIN-003` | chain | high | Escalated case closed without human review | eu_ai_act Art. 14(4); finra Rule 3110; eu_mar Art. 16 |
| `REC-CHAIN-004` | chain | medium | Agent reasserted an action a human overrode | eu_ai_act Art. 14(4)(d); finra Rule 3110 |
| `REC-CHAIN-005` | chain | medium | Downstream outcome status moved backwards | sec 17a-4(f); finra Rule 4511; eu_ai_act Art. 12(1) |
| `REC-CHAIN-006` | chain | high | SAR filed (or still unfiled) past the 30-day deadline | bsa_fincen 31 CFR 1020.320(b)(3); bsa_fincen 31 CFR 1010.320 |
| `REC-ESC-001` | trace | high | Failed policy rule was not escalated | eu_ai_act Art. 14(4); eu_mar Art. 16(2); bsa_fincen 31 CFR 1010.320; finra Rule 3110 |
| `REC-ESC-002` | trace | medium | Escalation recorded without a reason | eu_ai_act Art. 14(4); eu_mar Art. 16; bsa_fincen 31 CFR 1010.320 |
| `REC-ESC-003` | trace | high | High-severity autonomous decision not escalated | eu_ai_act Art. 14(4)(e); mifid2 RTS 6 Art. 15 |
| `REC-ESC-004` | trace | medium | Inconclusive or low-confidence verdict left unresolved | eu_ai_act Art. 13(3)(d); eu_ai_act Art. 14(4); bsa_fincen 31 CFR 1010.320 |
| `REC-ESC-005` | trace | high | Human-required action taken autonomously | eu_ai_act Art. 14(1); eu_ai_act Art. 26(2); bsa_fincen 31 CFR 1010.320 |
| `REC-HO-001` | trace | high | Supervised decision took effect without recorded human review | eu_ai_act Art. 14(4); eu_ai_act Art. 26(2); finra Rule 3110; bsa_fincen 31 CFR 1010.320 |
| `REC-HO-002` | trace | medium | Human override recorded without a rationale | eu_ai_act Art. 14(4)(d); finra Rule 3110 |
| `REC-HO-003` | trace | medium | Declared operating domain contradicts recorded oversight | eu_ai_act Art. 14; eu_ai_act Art. 26(5); mifid2 RTS 6 Art. 1 |
| `REC-HO-004` | trace | medium | Analyst identifier stored as plaintext PII | record_spec human_disposition.analyst_id; gdpr Art. 5(1)(c); gdpr Art. 25(1) |
| `REC-INT-001` | trace | low | Chain-of-thought steps out of sequence | eu_ai_act Art. 13(3)(d); eu_ai_act Art. 12(1) |
| `REC-INT-002` | trace | low | Authority basis names an unrecognised framework | record_spec authority_basis (&lt;framework&gt;.&lt;article_or_section&gt;); eu_ai_act Art. 86(1) |
| `REC-INT-003` | trace | medium | Escalation or filing has no downstream outcome link | eu_mar Art. 16; bsa_fincen 31 CFR 1010.320; finra Rule 4511 |
| `REC-INT-004` | trace | medium | record_id is not a canonical UUIDv4 | eu_ai_act Art. 12(1); sec 17a-4(a); finra Rule 4511; record_spec record_id (UUIDv4) |
| `REC-PROV-001` | trace | medium | Tool result without a matching tool call | eu_ai_act Art. 12(2)(b); mifid2 RTS 6 Art. 11; record_spec tool_results.tool_name (join key to tools_accessed) |
| `REC-PROV-002` | trace | low | Tool call without a recorded result | eu_ai_act Art. 12(2)(b); bsa_fincen 31 CFR 1010.210 |
| `REC-PROV-003` | trace | high | Data payload hash is not a SHA-256 digest | sec 17a-4(f); eu_ai_act Art. 12(2)(b); mifid2 RTS 6 Art. 28 |
| `REC-PROV-004` | trace | medium | External tools used without data provenance | eu_ai_act Art. 12(2)(b); mifid2 RTS 6 Art. 11; bsa_fincen 31 CFR 1010.210 |
| `REC-SCHEMA-001` | trace | high | Trace does not conform to the RECORD schema | record_spec schema.json (JSON Schema draft-07); eu_ai_act Art. 12(1) |
| `REC-TIME-001` | trace | high | Human review timestamped before the decision | mifid2 RTS 6 Art. 16; sec 17a-4(a); finra Rule 4511 |
| `REC-TIME-002` | trace | medium | Tool call timestamped after the decision was finalised | eu_ai_act Art. 12(2)(a); mifid2 RTS 25; sec 17a-4(a) |
| `REC-TIME-003` | trace | medium | Timestamp is not valid ISO 8601 UTC | mifid2 RTS 25 Art. 1; eu_ai_act Art. 12(2)(a); record_spec timestamp (ISO 8601 UTC) |
<!-- rules:end -->

In addition, a `GAP.<regulation>.<field>` rule is generated for every field in record-py's `GapAnalyzer` tables (required fields → `high`, recommended → `low`). Run `record-mcp rules` to list them.

### Firm rule packs

Point `--rules-dir` (repeatable) or `$RECORD_MCP_EXTRA_RULES` (path-separated) at a directory of YAML rules. A pack can reuse any built-in check with its own params, for example a stricter escalation threshold:

```yaml
# acme-rules/ACME-ESC-003.yaml
id: ACME-ESC-003
title: High-severity autonomous decision not escalated (ACME threshold 5.0)
scope: trace
severity: critical
category: escalation
check: autonomous_high_severity_not_escalated
params: { severity_threshold: 5.0 }
applies_to: { operating_domains: [autonomous] }
citations: [{ framework: eu_ai_act, ref: "Art. 14(4)(e)" }]
description: ACME policy escalates autonomous decisions at severity 5.0 and above.
remediation: Escalate, or run the agent in supervised mode.
```

Rule ids must be unique across the built-in catalog and all packs.

## Relationship to record-spec

- [`src/record_mcp/data/schema.json`](src/record_mcp/data/schema.json) is a vendored copy of RECORD's schema. It comes from the same record-spec commit that `record-py` is pinned to in [`pyproject.toml`](pyproject.toml). `python scripts/sync_schema.py --check` verifies this in CI.
- To move to a newer RECORD version, bump the pin, run `python scripts/sync_schema.py`, and update the test fixtures under [`tests/fixtures/examples/`](tests/fixtures/examples/).
- record-py is installed from GitHub because it is not on PyPI yet. For the same reason, record-mcp cannot be published to PyPI yet.

## Disclaimer

Citations reflect the maintainers' reading of each framework, aligned with record-spec's [REGULATORY_MAPPING.md](https://github.com/vigilai-tech/record-spec/blob/main/REGULATORY_MAPPING.md). A violation report is evidence for a compliance review. It is not legal advice, and it does not determine whether a firm is compliant.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Every new rule needs a regulatory citation and a test that triggers it.

## License

Apache 2.0. See [LICENSE](LICENSE).
