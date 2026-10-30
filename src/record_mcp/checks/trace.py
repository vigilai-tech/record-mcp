"""Behavioral checks evaluated against a single RECORD trace."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

from record_mcp.checks._util import (
    action_type,
    comparable,
    escalated,
    has_human_disposition,
    is_utc,
    items,
    obj,
    parse_ts,
    text,
)
from record_mcp.checks.registry import Finding, trace_check
from record_mcp.models import Rule

# ── human oversight ───────────────────────────────────────────────────────────


@trace_check("supervised_without_review")
def supervised_without_review(doc: dict[str, Any], rule: Rule) -> Iterator[Finding]:
    if doc.get("operating_domain") != "supervised" or has_human_disposition(doc):
        return
    if escalated(doc):
        return  # handed to a human; review is pending, not skipped
    yield Finding(
        message="Trace is declared `supervised` but records no human_disposition and the "
        "decision was not escalated, so the agent's action took effect without recorded "
        "human review.",
        path="/human_disposition",
        evidence={"final_action.type": action_type(doc)},
    )


@trace_check("override_without_rationale")
def override_without_rationale(doc: dict[str, Any], rule: Rule) -> Iterator[Finding]:
    hd = obj(doc, "human_disposition")
    if hd.get("action") == "override" and not text(hd.get("rationale")):
        yield Finding(
            message="A human overrode the agent's recommendation but no rationale was recorded.",
            path="/human_disposition/rationale",
            evidence={"analyst_id": hd.get("analyst_id")},
        )


@trace_check("oversight_declaration_mismatch")
def oversight_declaration_mismatch(doc: dict[str, Any], rule: Rule) -> Iterator[Finding]:
    domain = doc.get("operating_domain")
    hd_action = obj(doc, "human_disposition").get("action")
    if domain == "autonomous" and hd_action in ("override", "confirm"):
        yield Finding(
            message=f"Trace is declared `autonomous` but a human `{hd_action}` is recorded; the "
            "operating_domain misstates the oversight that actually applied.",
            path="/operating_domain",
            evidence={"operating_domain": domain, "human_disposition.action": hd_action},
        )
    elif domain == "human_only" and not hd_action:
        yield Finding(
            message="Trace is declared `human_only` but no human_disposition records the human "
            "decision.",
            path="/human_disposition",
            evidence={"operating_domain": domain},
        )


_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")
_PERSON_NAME = re.compile(r"^[A-Z][a-z]+(?:[ .'-]+[A-Z][a-z]+)+$")
_PHONE = re.compile(r"^\+?[\d\s().-]{7,}$")


@trace_check("analyst_id_plaintext_pii")
def analyst_id_plaintext_pii(doc: dict[str, Any], rule: Rule) -> Iterator[Finding]:
    analyst = text(obj(doc, "human_disposition").get("analyst_id"))
    if not analyst:
        return
    kind = (
        "an email address"
        if _EMAIL.search(analyst)
        else "a personal name"
        if _PERSON_NAME.match(analyst)
        else "a phone number"
        if _PHONE.match(analyst)
        else None
    )
    if kind:
        yield Finding(
            message=f"human_disposition.analyst_id looks like {kind}; RECORD requires a "
            "pseudonymous or hashed analyst identifier.",
            path="/human_disposition/analyst_id",
            evidence={"detected": kind},
        )


# ── escalation ────────────────────────────────────────────────────────────────


@trace_check("failed_rule_not_escalated")
def failed_rule_not_escalated(doc: dict[str, Any], rule: Rule) -> Iterator[Finding]:
    if escalated(doc) or has_human_disposition(doc):
        return
    if action_type(doc) in rule.params.get("exempt_action_types", []):
        return
    failed = [pe.get("rule_id") for _, pe in items(doc, "policy_evaluation")
              if pe.get("verdict") == "fail"]
    if failed:
        yield Finding(
            message=f"{len(failed)} policy rule(s) failed but the decision was neither escalated "
            "nor reviewed by a human.",
            path="/final_action/escalated",
            evidence={"failed_rules": failed, "final_action.type": action_type(doc)},
        )


@trace_check("escalation_without_reason")
def escalation_without_reason(doc: dict[str, Any], rule: Rule) -> Iterator[Finding]:
    if escalated(doc) and not text(obj(doc, "final_action").get("escalation_reason")):
        yield Finding(
            message="final_action.escalated is true but no escalation_reason was recorded.",
            path="/final_action/escalation_reason",
        )


@trace_check("autonomous_high_severity_not_escalated")
def autonomous_high_severity_not_escalated(doc: dict[str, Any], rule: Rule) -> Iterator[Finding]:
    score = obj(doc, "final_action").get("severity_score")
    threshold = rule.params.get("severity_threshold", 7.0)
    if (
        doc.get("operating_domain") == "autonomous"
        and isinstance(score, (int, float))
        and not isinstance(score, bool)
        and score >= threshold
        and not escalated(doc)
    ):
        yield Finding(
            message=f"Autonomous decision with severity_score {score} (threshold {threshold}) "
            "was not escalated to a human.",
            path="/final_action/escalated",
            evidence={"severity_score": score, "threshold": threshold},
        )


@trace_check("low_confidence_unresolved")
def low_confidence_unresolved(doc: dict[str, Any], rule: Rule) -> Iterator[Finding]:
    if escalated(doc) or has_human_disposition(doc):
        return
    min_conf = rule.params.get("min_confidence", 0.6)
    weak = []
    for i, pe in items(doc, "policy_evaluation"):
        conf = pe.get("confidence")
        scored = isinstance(conf, (int, float)) and pe.get("verdict") != "not_applicable"
        if pe.get("verdict") == "inconclusive" or (scored and conf < min_conf):
            weak.append({"index": i, "rule_id": pe.get("rule_id"), "verdict": pe.get("verdict"),
                         "confidence": conf})
    if weak:
        yield Finding(
            message=f"{len(weak)} policy verdict(s) are inconclusive or below confidence "
            f"{min_conf}, yet the decision was neither escalated nor reviewed by a human.",
            path="/policy_evaluation",
            evidence={"weak_verdicts": weak},
        )


@trace_check("autonomous_restricted_action")
def autonomous_restricted_action(doc: dict[str, Any], rule: Rule) -> Iterator[Finding]:
    restricted = rule.params.get("human_required_actions", [])
    if doc.get("operating_domain") == "autonomous" and action_type(doc) in restricted:
        yield Finding(
            message=f"`{action_type(doc)}` was taken autonomously; this action type requires "
            "a human decision-maker.",
            path="/final_action/type",
            evidence={"final_action.type": action_type(doc), "human_required_actions": restricted},
        )


# ── temporal ──────────────────────────────────────────────────────────────────


@trace_check("review_before_decision")
def review_before_decision(doc: dict[str, Any], rule: Rule) -> Iterator[Finding]:
    decided = comparable(parse_ts(doc.get("timestamp")))
    reviewed = comparable(parse_ts(obj(doc, "human_disposition").get("reviewed_at")))
    if decided and reviewed and reviewed < decided:
        yield Finding(
            message="human_disposition.reviewed_at is earlier than the decision timestamp; the "
            "review cannot have assessed this decision.",
            path="/human_disposition/reviewed_at",
            evidence={"timestamp": doc.get("timestamp"),
                      "reviewed_at": obj(doc, "human_disposition").get("reviewed_at")},
        )


@trace_check("tool_call_after_decision")
def tool_call_after_decision(doc: dict[str, Any], rule: Rule) -> Iterator[Finding]:
    decided = comparable(parse_ts(doc.get("timestamp")))
    if not decided:
        return
    tolerance = timedelta(milliseconds=rule.params.get("tolerance_ms", 0))
    late = []
    for i, call in items(doc, "tools_accessed"):
        called = comparable(parse_ts(call.get("timestamp")))
        if called and called > decided + tolerance:
            late.append({"index": i, "tool_name": call.get("tool_name"),
                         "seconds_after": round((called - decided).total_seconds(), 3)})
    # One finding per trace: late tool calls almost always share one root cause (the
    # decision timestamp taken at the wrong point), so per-call findings would be noise.
    if late:
        worst = max(t["seconds_after"] for t in late)
        yield Finding(
            message=f"{len(late)} tool call(s) are timestamped after the decision was finalised "
            f"(up to {worst:.3f}s later); inputs to a decision must precede it.",
            path=f"/tools_accessed/{late[0]['index']}/timestamp",
            evidence={"timestamp": doc.get("timestamp"), "late_tool_calls": late},
        )


@trace_check("timestamp_not_utc")
def timestamp_not_utc(doc: dict[str, Any], rule: Rule) -> Iterator[Finding]:
    fields: list[tuple[str, Any]] = [("/timestamp", doc.get("timestamp"))]
    hd = obj(doc, "human_disposition")
    if "reviewed_at" in hd:
        fields.append(("/human_disposition/reviewed_at", hd["reviewed_at"]))
    fields += [(f"/tools_accessed/{i}/timestamp", c.get("timestamp"))
               for i, c in items(doc, "tools_accessed")]
    for path, raw in fields:
        if raw is None:
            continue  # absence is a schema concern
        ts = parse_ts(raw)
        if ts is None:
            problem = "is not a valid ISO 8601 timestamp"
        elif ts.utcoffset() is None:
            problem = "has no UTC offset"
        elif not is_utc(ts):
            problem = f"uses offset {ts.isoformat()[-6:]} instead of UTC"
        else:
            continue
        yield Finding(message=f"{path} {problem}: {raw!r}.", path=path, evidence={"value": raw})


# ── provenance ────────────────────────────────────────────────────────────────


@trace_check("orphan_tool_result")
def orphan_tool_result(doc: dict[str, Any], rule: Rule) -> Iterator[Finding]:
    called = {c.get("tool_name") for _, c in items(doc, "tools_accessed")}
    for i, res in items(doc, "tool_results"):
        if res.get("tool_name") not in called:
            yield Finding(
                message=f"tool_results entry `{res.get('tool_name')}` has no matching "
                "tools_accessed call, so its provenance cannot be established.",
                path=f"/tool_results/{i}/tool_name",
            )


@trace_check("tool_call_without_result")
def tool_call_without_result(doc: dict[str, Any], rule: Rule) -> Iterator[Finding]:
    returned = {r.get("tool_name") for _, r in items(doc, "tool_results")}
    for i, call in items(doc, "tools_accessed"):
        if call.get("tool_name") not in returned:
            yield Finding(
                message=f"Tool `{call.get('tool_name')}` was called but no tool_results entry "
                "records what it returned.",
                path=f"/tools_accessed/{i}",
            )


_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


@trace_check("invalid_payload_hash")
def invalid_payload_hash(doc: dict[str, Any], rule: Rule) -> Iterator[Finding]:
    for i, entry in items(doc, "data_at_decision"):
        h = entry.get("payload_hash")
        if not (isinstance(h, str) and _SHA256.match(h)):
            yield Finding(
                message=f"data_at_decision[{i}] (`{entry.get('source')}`) payload_hash is not a "
                "64-character SHA-256 hex digest, so the payload cannot be verified.",
                path=f"/data_at_decision/{i}/payload_hash",
                evidence={"payload_hash": h},
            )


@trace_check("tools_without_data_provenance")
def tools_without_data_provenance(doc: dict[str, Any], rule: Rule) -> Iterator[Finding]:
    if items(doc, "tools_accessed") and not items(doc, "data_at_decision"):
        yield Finding(
            message="The agent called external tools but data_at_decision records no source "
            "hashes, so the data it acted on cannot be reconstructed.",
            path="/data_at_decision",
            evidence={"tools": [c.get("tool_name") for _, c in items(doc, "tools_accessed")]},
        )


# ── integrity ─────────────────────────────────────────────────────────────────


@trace_check("cot_step_sequence")
def cot_step_sequence(doc: dict[str, Any], rule: Rule) -> Iterator[Finding]:
    steps = [c.get("step") for _, c in items(doc, "chain_of_thought")]
    if steps and steps != list(range(1, len(steps) + 1)):
        yield Finding(
            message="chain_of_thought steps are not numbered 1..n in order; reasoning steps may "
            "be missing, duplicated, or reordered.",
            path="/chain_of_thought",
            evidence={"steps": steps},
        )


@trace_check("unknown_authority_framework")
def unknown_authority_framework(doc: dict[str, Any], rule: Rule) -> Iterator[Finding]:
    known = set(rule.params.get("known_prefixes", []))
    basis = doc.get("authority_basis")
    for i, entry in enumerate(basis if isinstance(basis, list) else []):
        prefix = entry.split(".", 1)[0] if isinstance(entry, str) else None
        if prefix not in known:
            yield Finding(
                message=f"authority_basis entry {entry!r} does not name a recognised regulatory "
                "framework.",
                path=f"/authority_basis/{i}",
                evidence={"prefix": prefix},
            )


@trace_check("outcome_not_linked")
def outcome_not_linked(doc: dict[str, Any], rule: Rule) -> Iterator[Finding]:
    filing = action_type(doc) in rule.params.get("filing_actions", [])
    if (filing or escalated(doc)) and not obj(doc, "outcome_linkage"):
        yield Finding(
            message=f"Decision `{action_type(doc)}` "
            f"{'is a filing' if filing else 'was escalated'} but outcome_linkage does not point "
            "to the downstream ticket or filing.",
            path="/outcome_linkage",
        )


@trace_check("record_id_not_uuid4")
def record_id_not_uuid4(doc: dict[str, Any], rule: Rule) -> Iterator[Finding]:
    rid = doc.get("record_id")
    if not isinstance(rid, str):
        return  # missing / wrong type is reported by schema conformance
    try:
        ok = uuid.UUID(rid).version == 4 and str(uuid.UUID(rid)) == rid.lower()
    except ValueError:
        ok = False
    if not ok:
        yield Finding(
            message=f"record_id {rid!r} is not a canonical UUIDv4.",
            path="/record_id",
        )
