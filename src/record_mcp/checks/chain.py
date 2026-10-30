"""Checks evaluated across a chain of traces (one case / investigation thread).

The engine hands each chain check its traces sorted by decision timestamp.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

from record_mcp.checks._util import (
    action_type,
    comparable,
    escalated,
    has_human_disposition,
    obj,
    parse_ts,
    record_id,
    text,
)
from record_mcp.checks.registry import Finding, chain_check
from record_mcp.models import Rule


@chain_check("duplicate_record_id", input_wide=True)
def duplicate_record_id(traces: list[dict[str, Any]], rule: Rule) -> Iterator[Finding]:
    seen: dict[str, int] = defaultdict(int)
    for t in traces:
        if isinstance(t.get("record_id"), str):
            seen[t["record_id"]] += 1
    for rid, n in seen.items():
        if n > 1:
            yield Finding(
                message=f"record_id {rid} appears on {n} traces; every RECORD must be uniquely "
                "identifiable.",
                path="/record_id",
                evidence={"occurrences": n},
                record_ids=[rid],
            )


@chain_check("agent_version_drift")
def agent_version_drift(traces: list[dict[str, Any]], rule: Rule) -> Iterator[Finding]:
    versions: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for t in traces:
        agent, version = text(t.get("agent_id")), text(t.get("agent_version"))
        if agent and version:
            versions[agent][version].append(record_id(t))
    for agent, by_version in versions.items():
        if len(by_version) > 1:
            yield Finding(
                message=f"Agent `{agent}` ran {len(by_version)} different versions "
                f"({', '.join(by_version)}) within one case; decisions in the chain are not "
                "reproducible against a single model/code version.",
                path="/agent_version",
                evidence={"versions": {v: ids for v, ids in by_version.items()}},
                record_ids=[rid for ids in by_version.values() for rid in ids],
            )


@chain_check("deescalated_without_review")
def deescalated_without_review(traces: list[dict[str, Any]], rule: Rule) -> Iterator[Finding]:
    closing = set(rule.params.get("closing_actions", []))
    open_escalation: dict[str, Any] | None = None
    for t in traces:
        if has_human_disposition(t):
            open_escalation = None
        if open_escalation is not None and action_type(t) in closing:
            yield Finding(
                message=f"Case was escalated in {record_id(open_escalation)} and later closed "
                f"(`{action_type(t)}`) in {record_id(t)} with no human disposition recorded in "
                "between.",
                path="/final_action/type",
                evidence={"escalation_reason": obj(open_escalation, "final_action")
                          .get("escalation_reason")},
                record_ids=[record_id(open_escalation), record_id(t)],
            )
            open_escalation = None
        if escalated(t) and not has_human_disposition(t):
            open_escalation = t


@chain_check("overridden_action_reasserted")
def overridden_action_reasserted(traces: list[dict[str, Any]], rule: Rule) -> Iterator[Finding]:
    for i, overridden in enumerate(traces):
        if obj(overridden, "human_disposition").get("action") != "override":
            continue
        rejected = action_type(overridden)
        for later in traces[i + 1:]:
            if not rejected or action_type(later) != rejected:
                continue
            if obj(later, "human_disposition").get("action") == "confirm":
                break  # a human has since endorsed the action
            yield Finding(
                message=f"A human overrode `{rejected}` in {record_id(overridden)}, but the agent "
                f"proposed the same action again in {record_id(later)} without a new human "
                "confirmation.",
                path="/final_action/type",
                evidence={"override_rationale": obj(overridden, "human_disposition")
                          .get("rationale")},
                record_ids=[record_id(overridden), record_id(later)],
            )
            break


_STATUS_RANK = {"pending": 0, "in_progress": 1, "completed": 2, "rejected": 2, "cancelled": 2}


@chain_check("outcome_status_regression")
def outcome_status_regression(traces: list[dict[str, Any]], rule: Rule) -> Iterator[Finding]:
    last: dict[str, tuple[str, dict[str, Any]]] = {}
    for t in traces:
        ol = obj(t, "outcome_linkage")
        ref, status = text(ol.get("reference_id")), ol.get("status")
        if not ref or status not in _STATUS_RANK:
            continue
        if ref in last:
            prev_status, prev = last[ref]
            terminal_flip = _STATUS_RANK[prev_status] == 2 and status != prev_status
            if _STATUS_RANK[status] < _STATUS_RANK[prev_status] or terminal_flip:
                yield Finding(
                    message=f"Outcome {ref} moved from `{prev_status}` ({record_id(prev)}) to "
                    f"`{status}` ({record_id(t)}); a closed or advanced outcome was rewritten.",
                    path="/outcome_linkage/status",
                    evidence={"reference_id": ref, "from": prev_status, "to": status},
                    record_ids=[record_id(prev), record_id(t)],
                )
        last[ref] = (status, t)


@chain_check("sar_filing_deadline")
def sar_filing_deadline(traces: list[dict[str, Any]], rule: Rule) -> Iterator[Finding]:
    max_days = rule.params.get("max_days", 30)
    outcome_types = set(rule.params.get("sar_outcome_types", []))
    filing_actions = set(rule.params.get("filing_actions", []))
    decision_actions = set(rule.params.get("sar_decision_actions", []))

    dated = [(ts, t) for t in traces if (ts := comparable(parse_ts(t.get("timestamp"))))]
    if not dated:
        return
    detected_at, first = dated[0]
    deadline = detected_at + timedelta(days=max_days)

    def is_sar_outcome(t: dict[str, Any]) -> bool:
        return text(obj(t, "outcome_linkage").get("type")) in outcome_types

    filed = [
        (ts, t) for ts, t in dated
        if action_type(t) in filing_actions
        or (is_sar_outcome(t) and obj(t, "outcome_linkage").get("status") == "completed")
    ]
    if filed:
        filed_at, filing = filed[0]
        if filed_at > deadline:
            yield Finding(
                message=f"SAR filed {(filed_at - detected_at).days} days after initial detection "
                f"in {record_id(first)}; the deadline is {max_days} days.",
                path="/outcome_linkage",
                evidence={"detected_at": first.get("timestamp"),
                          "filed_at": filing.get("timestamp"), "max_days": max_days},
                record_ids=[record_id(first), record_id(filing)],
            )
        return

    sar_in_flight = [t for _, t in dated if is_sar_outcome(t) or action_type(t) in decision_actions]
    latest_at, latest = dated[-1]
    if sar_in_flight and latest_at > deadline:
        yield Finding(
            message=f"A SAR was initiated in this case but none is recorded as filed "
            f"{(latest_at - detected_at).days} days after initial detection; the deadline is "
            f"{max_days} days.",
            path="/outcome_linkage",
            evidence={"detected_at": first.get("timestamp"),
                      "latest_trace_at": latest.get("timestamp"), "max_days": max_days},
            record_ids=[record_id(first), record_id(latest)],
        )
