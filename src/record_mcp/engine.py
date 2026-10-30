"""Runs catalog rules against a trace or a chain of traces and builds reports."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from typing import Any, Literal

from record_mcp.catalog import Catalog
from record_mcp.checks import CHAIN_CHECKS, TRACE_CHECKS, Finding
from record_mcp.checks._util import comparable, obj, parse_ts, record_id, text
from record_mcp.checks.builtin import validator
from record_mcp.inputs import InputError
from record_mcp.models import (
    SEVERITY_RANK,
    ChainGroup,
    ChainReport,
    Rule,
    Scope,
    Severity,
    SeverityCounts,
    TraceReport,
    Violation,
    at_least,
    status_for,
)

log = logging.getLogger(__name__)

GroupBy = Literal["auto", "case_id", "investigation_thread_id", "none"]

_EARLIEST = datetime.min.replace(tzinfo=timezone.utc)


class Engine:
    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog

    # ── public API ────────────────────────────────────────────────────────────

    def check_trace(
        self,
        doc: dict[str, Any],
        *,
        index: int = 0,
        frameworks: list[str] | None = None,
        min_severity: Severity = "info",
        include_gaps: bool = True,
    ) -> TraceReport:
        fw = self._frameworks(frameworks)
        gap_regs = self._gap_regulations(doc, fw) if include_gaps else set()
        violations: list[Violation] = []
        errors: list[str] = []
        for rule in self._selected("trace", fw):
            if rule.check == "regulation_field_gap" and rule.params["regulation"] not in gap_regs:
                continue
            if not self._applies(rule, [doc]):
                continue
            findings = self._run(rule, TRACE_CHECKS[rule.check], doc, errors)
            violations += [self._violation(rule, f, [record_id(doc)]) for f in findings]

        violations = _filter_sort(violations, min_severity)
        rid = doc.get("record_id")
        return TraceReport(
            index=index,
            record_id=rid if isinstance(rid, str) else None,
            status=status_for(violations),
            schema_valid=validator().validate(doc)["valid"],
            gap_regulations=sorted(gap_regs),
            counts=SeverityCounts.of(violations),
            violations=violations,
            errors=errors,
        )

    def check_chain(
        self,
        docs: list[dict[str, Any]],
        *,
        group_by: GroupBy = "auto",
        frameworks: list[str] | None = None,
        min_severity: Severity = "info",
        include_gaps: bool = True,
    ) -> ChainReport:
        fw = self._frameworks(frameworks)
        traces = [
            self.check_trace(d, index=i, frameworks=frameworks, min_severity=min_severity,
                             include_gaps=include_gaps)
            for i, d in enumerate(docs)
        ]
        groups = _group(docs, group_by)
        chain_violations: list[Violation] = []
        errors: list[str] = []

        for rule in self._selected("chain", fw):
            spec = CHAIN_CHECKS[rule.check]
            targets = (
                [(None, _by_time(docs))] if spec.input_wide
                else [(key, members) for key, _, members in groups if len(members) > 1]
            )
            for key, members in targets:
                if not self._applies(rule, members):
                    continue
                findings = self._run(rule, spec.fn, members, errors)
                chain_violations += [
                    self._violation(rule, f, [record_id(t) for t in members], chain_key=key)
                    for f in findings
                ]

        chain_violations = _filter_sort(chain_violations, min_severity)
        everything = chain_violations + [v for t in traces for v in t.violations]
        return ChainReport(
            status=status_for(everything),
            trace_count=len(docs),
            chains=[
                ChainGroup(key=key, group_by=used, record_ids=[record_id(t) for t in members])
                for key, used, members in groups
            ],
            counts=SeverityCounts.of(everything),
            chain_violations=chain_violations,
            traces=traces,
            errors=errors,
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    def _frameworks(self, frameworks: list[str] | None) -> set[str] | None:
        if not frameworks:
            return None
        unknown = sorted(set(frameworks) - set(self.catalog.frameworks))
        if unknown:
            raise InputError(
                f"Unknown framework(s): {', '.join(unknown)}. "
                f"Known: {', '.join(sorted(self.catalog.frameworks))}"
            )
        return set(frameworks)

    def _selected(self, scope: Scope, fw: set[str] | None) -> Iterable[Rule]:
        for rule in self.catalog.rules.values():
            if rule.scope != scope:
                continue
            if (fw is None or rule.framework_agnostic
                    or any(c.framework in fw for c in rule.citations)):
                yield rule

    def _gap_regulations(self, doc: dict[str, Any], fw: set[str] | None) -> set[str]:
        if fw is not None:
            return {g.regulation for g in self.catalog.gap_analyses if g.framework in fw}
        basis = [b for b in doc.get("authority_basis") or [] if isinstance(b, str)]
        return {
            g.regulation
            for g in self.catalog.gap_analyses
            if any(fnmatchcase(b, pattern) for b in basis for pattern in g.triggers)
        }

    @staticmethod
    def _applies(rule: Rule, docs: list[dict[str, Any]]) -> bool:
        domains = rule.applies_to.operating_domains
        if domains and not any(d.get("operating_domain") in domains for d in docs):
            return False
        prefixes = tuple(rule.applies_to.authority_basis_prefixes)
        if prefixes and not any(
            isinstance(b, str) and b.startswith(prefixes)
            for d in docs
            if isinstance(d.get("authority_basis"), list)
            for b in d["authority_basis"]
        ):
            return False
        return True

    @staticmethod
    def _run(rule: Rule, check: Any, subject: Any, errors: list[str]) -> list[Finding]:
        # A check that trips over malformed input must not sink the whole report.
        try:
            return list(check(subject, rule))
        except Exception as exc:  # noqa: BLE001
            log.exception("check %s failed", rule.check)
            errors.append(f"{rule.id}: check {rule.check!r} raised {type(exc).__name__}: {exc}")
            return []

    @staticmethod
    def _violation(
        rule: Rule, finding: Finding, default_ids: list[str], chain_key: str | None = None
    ) -> Violation:
        return Violation(
            rule_id=rule.id,
            title=rule.title,
            severity=rule.severity,
            category=rule.category,
            scope=rule.scope,
            message=finding.message,
            record_ids=finding.record_ids or default_ids,
            chain_key=chain_key,
            path=finding.path,
            evidence=finding.evidence,
            citations=rule.citations,
            remediation=rule.remediation,
        )


def _filter_sort(violations: list[Violation], min_severity: Severity) -> list[Violation]:
    kept = [v for v in violations if at_least(v.severity, min_severity)]
    return sorted(kept, key=lambda v: (-SEVERITY_RANK[v.severity], v.rule_id))


def _by_time(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable sort by decision timestamp; traces without a usable timestamp go last."""
    def key(item: tuple[int, dict[str, Any]]) -> tuple[bool, datetime, int]:
        ts = comparable(parse_ts(item[1].get("timestamp")))
        return (ts is None, ts or _EARLIEST, item[0])

    return [d for _, d in sorted(enumerate(docs), key=key)]


def _group(docs: list[dict[str, Any]], group_by: GroupBy) -> list[tuple[str, str, list]]:
    if group_by == "none":
        return [("input", "none", _by_time(docs))]
    fields = ["case_id", "investigation_thread_id"] if group_by == "auto" else [group_by]
    groups: dict[str, tuple[str, list[dict[str, Any]]]] = {}
    for doc in docs:
        lineage = obj(doc, "decision_lineage")
        used = next((f for f in fields if text(lineage.get(f))), None)
        key = f"{used}:{lineage[used]}" if used else f"record_id:{record_id(doc)}"
        groups.setdefault(key, (used or "record_id", []))[1].append(doc)
    return [(key, used, _by_time(members)) for key, (used, members) in groups.items()]
