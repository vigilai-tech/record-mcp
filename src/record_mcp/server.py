"""The record-mcp MCP server: tools, resources, and a prompt over the violation catalog."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from record_mcp import __version__
from record_mcp.catalog import Catalog, CatalogError, default_catalog
from record_mcp.checks.builtin import SCHEMA_PATH
from record_mcp.engine import Engine, GroupBy
from record_mcp.inputs import InputError, InputFormat, load_traces
from record_mcp.models import (
    ChainReport,
    FrameworkList,
    Rule,
    RuleList,
    RuleSummary,
    Scope,
    Severity,
    TraceReport,
)

INSTRUCTIONS = """\
record-mcp checks RECORD audit traces (https://github.com/vigilai-tech/record-spec) for
regulatory and policy violations. Use `check_trace` for one decision and `check_trace_chain` for
several traces from the same case or investigation thread; chain rules (duplicate IDs, closed
escalations without review, SAR deadlines, ...) only run in `check_trace_chain`. Every violation
cites the regulation that motivates it. Use `list_rules` / `get_rule` to explain a rule, and
report violations exactly as returned; do not invent additional findings.
"""

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)

TraceInput = Annotated[
    dict[str, Any] | str | None,
    Field(description="A RECORD trace as a JSON object (or JSON string). Omit when using `path`."),
]
TracesInput = Annotated[
    list[dict[str, Any]] | str | None,
    Field(description="RECORD traces as a JSON array (or a JSON/JSONL string). Omit when using "
          "`path`."),
]
PathInput = Annotated[
    str | None,
    Field(description="Path to a .json (object or array) or .jsonl file. Only available when the "
          "server runs over stdio."),
]
FormatInput = Annotated[
    InputFormat,
    Field(description="'record' for RECORD documents, 'openinference' for OpenInference span "
          "dicts (converted with record-py's adapt_span)."),
]
FrameworksInput = Annotated[
    list[str] | None,
    Field(description="Only evaluate rules citing these framework ids (see list_frameworks), and "
          "run their field-gap analyses. Default: all rules; gap analyses chosen from each "
          "trace's authority_basis."),
]
MinSeverityInput = Annotated[
    Severity, Field(description="Drop violations below this severity.")
]
GapsInput = Annotated[
    bool, Field(description="Include per-regulation missing-field (GAP.*) violations.")
]


def build_server(*, allow_file_input: bool, catalog: Catalog | None = None) -> MCPServer:
    """Build the server. *allow_file_input* must be False for network transports."""
    catalog = catalog or default_catalog()
    engine = Engine(catalog)
    mcp = MCPServer(
        "record-mcp",
        title="RECORD Violation Checker",
        description="Find regulatory and policy violations in RECORD audit traces.",
        instructions=INSTRUCTIONS,
        website_url="https://github.com/vigilai-tech/record-mcp",
        version=__version__,
    )

    def traces_from(inline: Any, path: str | None, input_format: InputFormat) -> list[dict]:
        try:
            return load_traces(inline, path, input_format=input_format,
                               allow_files=allow_file_input)
        except InputError as exc:
            raise ToolError(str(exc)) from exc

    # ── tools ─────────────────────────────────────────────────────────────────

    @mcp.tool(annotations=READ_ONLY)
    def check_trace(
        trace: TraceInput = None,
        path: PathInput = None,
        input_format: FormatInput = "record",
        frameworks: FrameworksInput = None,
        min_severity: MinSeverityInput = "info",
        include_gaps: GapsInput = True,
    ) -> TraceReport:
        """Check one RECORD trace for violations.

        Runs schema conformance, behavioral rules (human oversight, escalation, temporal,
        provenance, integrity) and per-regulation field-gap analysis. Returns status
        (pass / warn / fail), counts by severity, and every violation with its JSON-pointer path,
        evidence, regulatory citations, and remediation. Use check_trace_chain for several traces.
        """
        docs = traces_from(trace, path, input_format)
        if len(docs) != 1:
            raise ToolError(f"Input contains {len(docs)} traces; use check_trace_chain.")
        try:
            return engine.check_trace(docs[0], frameworks=frameworks, min_severity=min_severity,
                                      include_gaps=include_gaps)
        except InputError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(annotations=READ_ONLY)
    def check_trace_chain(
        traces: TracesInput = None,
        path: PathInput = None,
        group_by: Annotated[
            GroupBy,
            Field(description="How to split traces into chains. 'auto' groups by "
                  "decision_lineage.case_id, falling back to investigation_thread_id; 'none' "
                  "treats the whole input as one chain."),
        ] = "auto",
        input_format: FormatInput = "record",
        frameworks: FrameworksInput = None,
        min_severity: MinSeverityInput = "info",
        include_gaps: GapsInput = True,
    ) -> ChainReport:
        """Check a chain of RECORD traces: every per-trace rule plus cross-trace rules.

        Cross-trace rules include duplicate record_ids, agent version drift within a case,
        escalations closed without human review, human overrides the agent reasserted, outcome
        status regressions, and SAR filing deadlines. Returns the chains found, per-trace
        reports, chain violations, and overall status.
        """
        docs = traces_from(traces, path, input_format)
        try:
            return engine.check_chain(docs, group_by=group_by, frameworks=frameworks,
                                      min_severity=min_severity, include_gaps=include_gaps)
        except InputError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(annotations=READ_ONLY)
    def list_rules(
        framework: Annotated[str | None, Field(description="Only rules citing this framework.")]
        = None,
        scope: Annotated[Scope | None, Field(description="'trace' or 'chain'.")] = None,
        category: Annotated[str | None, Field(description="e.g. human_oversight, escalation, "
                                              "temporal, provenance, integrity, field_gap.")]
        = None,
        include_generated: Annotated[
            bool, Field(description="Include GAP.* rules generated from record-py.")
        ] = True,
    ) -> RuleList:
        """List violation rules in the catalog, optionally filtered."""
        rules = [
            RuleSummary(
                id=r.id, title=r.title, scope=r.scope, severity=r.severity, category=r.category,
                frameworks=sorted({c.framework for c in r.citations}), source=r.source,
            )
            for r in catalog.rules.values()
            if (framework is None or any(c.framework == framework for c in r.citations))
            and (scope is None or r.scope == scope)
            and (category is None or r.category == category)
            and (include_generated or r.source == "catalog")
        ]
        return RuleList(count=len(rules), rules=rules)

    @mcp.tool(annotations=READ_ONLY)
    def get_rule(rule_id: Annotated[str, Field(description="e.g. REC-HO-001")]) -> Rule:
        """Full definition of one rule: description, citations, params, remediation."""
        try:
            return catalog.rule(rule_id)
        except CatalogError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(annotations=READ_ONLY)
    def list_frameworks() -> FrameworkList:
        """Regulatory frameworks known to the catalog, their authority_basis prefixes, and which
        record-py gap analyses each one triggers."""
        return FrameworkList(
            frameworks=list(catalog.frameworks.values()), gap_analyses=catalog.gap_analyses
        )

    # ── resources ─────────────────────────────────────────────────────────────

    @mcp.resource("record://schema", name="record-schema", mime_type="application/json",
                  description="The RECORD JSON Schema (draft-07) traces are validated against.")
    def schema() -> str:
        return SCHEMA_PATH.read_text(encoding="utf-8")

    @mcp.resource("record://rules", name="rule-catalog", mime_type="application/json",
                  description="Index of every violation rule.")
    def rule_index() -> str:
        return json.dumps(
            [{"id": r.id, "title": r.title, "scope": r.scope, "severity": r.severity,
              "uri": f"record://rules/{r.id}"} for r in catalog.rules.values()],
            indent=2,
        )

    @mcp.resource("record://rules/{rule_id}", name="rule", mime_type="application/yaml",
                  description="One rule's YAML definition.")
    def rule_yaml(rule_id: str) -> str:
        return catalog.rule_yaml(rule_id)

    # ── prompts ───────────────────────────────────────────────────────────────

    @mcp.prompt(title="Audit RECORD trace(s)")
    def audit_trace(
        source: str = "",
        chain: Literal["yes", "no"] = "no",
    ) -> str:
        """Audit RECORD trace(s) and write an examiner-ready violation summary."""
        tool = "check_trace_chain" if chain == "yes" else "check_trace"
        where = f" for {source}" if source else ""
        return (
            f"Run the record-mcp `{tool}` tool{where}. Then write an examiner-ready summary:\n"
            "1. Overall status and counts by severity.\n"
            "2. Violations grouped by severity (critical → info). For each, give the rule id, "
            "what is wrong (with the JSON path and evidence), and the cited regulations.\n"
            "3. Separate schema problems, behavioral violations, chain violations, and "
            "missing-field gaps (GAP.*).\n"
            "4. A prioritised remediation list using each rule's remediation text.\n"
            "Report only violations the tool returned. If the tool reports errors, list them "
            "separately as tooling issues, not violations."
        )

    return mcp
