"""Pydantic models shared by the rule catalog, the engine, and the MCP tool outputs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["critical", "high", "medium", "low", "info"]
Scope = Literal["trace", "chain"]
Status = Literal["pass", "warn", "fail"]
OperatingDomain = Literal["autonomous", "supervised", "human_only"]

SEVERITY_RANK: dict[str, int] = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def at_least(severity: str, threshold: str) -> bool:
    return SEVERITY_RANK[severity] >= SEVERITY_RANK[threshold]


# ── catalog ───────────────────────────────────────────────────────────────────


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    framework: str = Field(description="Framework id from frameworks.yaml, e.g. 'eu_ai_act'.")
    ref: str = Field(description="Article / section, e.g. 'Art. 14(4)' or '31 CFR 1010.320'.")
    url: str | None = None


class AppliesTo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operating_domains: list[OperatingDomain] = Field(
        default_factory=list, description="Only evaluate traces in these domains (empty = all)."
    )
    authority_basis_prefixes: list[str] = Field(
        default_factory=list,
        description="Only evaluate traces whose authority_basis has an entry with one of these "
        "prefixes (empty = all).",
    )


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Z][A-Za-z0-9_.-]+$")
    title: str
    scope: Scope
    severity: Severity
    category: str
    check: str = Field(description="Name of the registered Python check that implements the rule.")
    params: dict[str, Any] = Field(default_factory=dict)
    applies_to: AppliesTo = Field(default_factory=AppliesTo)
    framework_agnostic: bool = Field(
        default=False, description="Evaluate even when the caller filters by framework."
    )
    citations: list[Citation] = Field(min_length=1)
    description: str
    remediation: str
    fields: list[str] = Field(default_factory=list)
    since: str = "0.1.0"
    source: str = Field(default="catalog", description="'catalog' (YAML) or the generator name.")


class RuleSummary(BaseModel):
    id: str
    title: str
    scope: Scope
    severity: Severity
    category: str
    frameworks: list[str]
    source: str


class Framework(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    citation: str
    url: str | None = None
    authority_prefixes: list[str] = Field(default_factory=list)


class GapAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    regulation: str = Field(description="record-py GapAnalyzer regulation id.")
    framework: str
    triggers: list[str] = Field(description="fnmatch patterns matched against authority_basis.")


# ── results ───────────────────────────────────────────────────────────────────


class Violation(BaseModel):
    rule_id: str
    title: str
    severity: Severity
    category: str
    scope: Scope
    message: str
    record_ids: list[str] = Field(default_factory=list)
    chain_key: str | None = None
    path: str | None = Field(default=None, description="JSON pointer into the offending trace.")
    evidence: dict[str, Any] = Field(default_factory=dict)
    citations: list[Citation]
    remediation: str


class SeverityCounts(BaseModel):
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    info: int = 0
    total: int = 0

    @classmethod
    def of(cls, violations: list[Violation]) -> SeverityCounts:
        counts = cls()
        for v in violations:
            setattr(counts, v.severity, getattr(counts, v.severity) + 1)
        counts.total = len(violations)
        return counts


def status_for(violations: list[Violation]) -> Status:
    """fail on any high/critical, warn on anything lower, pass when clean."""
    if any(at_least(v.severity, "high") for v in violations):
        return "fail"
    return "warn" if violations else "pass"


class TraceReport(BaseModel):
    index: int = Field(description="Position of the trace in the input.")
    record_id: str | None
    status: Status
    schema_valid: bool
    gap_regulations: list[str] = Field(
        default_factory=list, description="record-py GapAnalyzer regulations evaluated."
    )
    counts: SeverityCounts
    violations: list[Violation]
    errors: list[str] = Field(
        default_factory=list, description="Checks that crashed on this input (not violations)."
    )


class ChainGroup(BaseModel):
    key: str
    group_by: str
    record_ids: list[str]


class ChainReport(BaseModel):
    status: Status
    trace_count: int
    chains: list[ChainGroup]
    counts: SeverityCounts = Field(description="Totals across per-trace and chain violations.")
    chain_violations: list[Violation]
    traces: list[TraceReport]
    errors: list[str] = Field(default_factory=list)


class RuleList(BaseModel):
    count: int
    rules: list[RuleSummary]


class FrameworkList(BaseModel):
    frameworks: list[Framework]
    gap_analyses: list[GapAnalysis]
