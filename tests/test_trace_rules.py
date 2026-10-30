"""Each trace rule: one mutation of the (clean) AML example that must trigger it."""

from collections.abc import Callable

import pytest
from conftest import example, rule_ids

from record_mcp.checks import TRACE_CHECKS


def _drop(*keys: str) -> Callable[[dict], None]:
    def mutate(d: dict) -> None:
        for k in keys:
            d.pop(k, None)
    return mutate


def _unreviewed(d: dict, **final_action) -> None:
    d.pop("human_disposition", None)
    d["final_action"].update({"escalated": False, **final_action})


TRACE_CASES: dict[str, Callable[[dict], None]] = {
    "REC-SCHEMA-001": _drop("prompt"),
    "REC-HO-001": lambda d: _unreviewed(d),
    "REC-HO-002": lambda d: d["human_disposition"].update(action="override", rationale=" "),
    "REC-HO-003": lambda d: d.update(operating_domain="autonomous"),
    "REC-HO-004": lambda d: d["human_disposition"].update(analyst_id="jane.doe@bank.example"),
    "REC-ESC-001": lambda d: _unreviewed(d, type="close_alert"),
    "REC-ESC-002": lambda d: d["final_action"].update(escalation_reason=""),
    "REC-ESC-003": lambda d: (_unreviewed(d, severity_score=8.2),
                              d.update(operating_domain="autonomous")),
    "REC-ESC-004": lambda d: (_unreviewed(d, type="draft_sar"),
                              d["policy_evaluation"][2].update(verdict="inconclusive")),
    "REC-ESC-005": lambda d: (_unreviewed(d, type="file_sar"),
                              d.update(operating_domain="autonomous")),
    "REC-TIME-001": lambda d: d["human_disposition"].update(reviewed_at="2026-05-20T10:00:00Z"),
    "REC-TIME-002": lambda d: d["tools_accessed"][0].update(timestamp="2026-05-20T11:23:00Z"),
    "REC-TIME-003": lambda d: d.update(timestamp="2026-05-20T13:22:47.889+02:00"),
    "REC-PROV-001": lambda d: d["tool_results"].append({"tool_name": "ghost", "payload": {}}),
    "REC-PROV-002": lambda d: d["tools_accessed"].append(
        {"tool_name": "ghost", "timestamp": "2026-05-20T11:22:26Z", "query": "q"}),
    "REC-PROV-003": lambda d: d["data_at_decision"][0].update(payload_hash="abc123"),
    "REC-PROV-004": _drop("data_at_decision"),
    "REC-INT-001": lambda d: d["chain_of_thought"][1].update(step=7),
    "REC-INT-002": lambda d: d["authority_basis"].append("unknown.unknown"),
    "REC-INT-003": _drop("outcome_linkage"),
    "REC-INT-004": lambda d: d.update(record_id="6ba7b810-9dad-11d1-80b4-00c04fd430c8"),
    "GAP.fincen_nprm_2026.data_at_decision": _drop("data_at_decision"),
}


def test_aml_example_is_clean(engine, aml):
    report = engine.check_trace(aml)
    assert report.status == "pass", [v.rule_id for v in report.violations]
    assert report.schema_valid and report.gap_regulations == ["fincen_nprm_2026"]


@pytest.mark.parametrize("rule_id", sorted(TRACE_CASES))
def test_rule_fires(engine, aml, rule_id):
    TRACE_CASES[rule_id](aml)
    report = engine.check_trace(aml)
    assert rule_id in rule_ids(report.violations)
    assert not report.errors
    hit = next(v for v in report.violations if v.rule_id == rule_id)
    assert hit.citations and hit.remediation and hit.record_ids


def test_every_trace_rule_has_a_case(catalog):
    from test_chain_rules import CHAIN_CASES

    catalog_rules = {r.id for r in catalog.rules.values() if r.source == "catalog"}
    assert catalog_rules <= set(TRACE_CASES) | set(CHAIN_CASES)


# ── rule-specific negatives ───────────────────────────────────────────────────


def test_supervised_escalation_pending_review_is_not_a_violation(engine):
    # trade_surveillance: supervised, escalated to L2, no human_disposition yet.
    report = engine.check_trace(example("trade_surveillance_l1.json"))
    assert "REC-HO-001" not in rule_ids(report.violations)


def test_failed_rule_with_conservative_action_is_exempt(engine, aml):
    _unreviewed(aml, type="reject")
    assert "REC-ESC-001" not in rule_ids(engine.check_trace(aml).violations)


def test_tool_call_tolerance(engine, aml, catalog):
    rule = catalog.rule("REC-TIME-002").model_copy(update={"params": {"tolerance_ms": 60_000}})
    aml["tools_accessed"][0]["timestamp"] = "2026-05-20T11:23:00Z"  # 12s late
    assert not list(TRACE_CHECKS[rule.check](aml, rule))


def test_late_tool_calls_reported_once(engine):
    report = engine.check_trace(example("communication_surveillance_l1.json"))
    late = [v for v in report.violations if v.rule_id == "REC-TIME-002"]
    assert len(late) == 1 and len(late[0].evidence["late_tool_calls"]) == 5


@pytest.mark.parametrize("analyst", ["Jane Doe", "+44 20 7946 0958"])
def test_pii_variants(engine, aml, analyst):
    aml["human_disposition"]["analyst_id"] = analyst
    assert "REC-HO-004" in rule_ids(engine.check_trace(aml).violations)


def test_human_only_without_disposition(engine, aml):
    aml["operating_domain"] = "human_only"
    del aml["human_disposition"]
    assert "REC-HO-003" in rule_ids(engine.check_trace(aml).violations)


# ── engine behaviour ──────────────────────────────────────────────────────────


def test_frameworks_filter(engine, aml):
    _unreviewed(aml, type="close_alert")
    del aml["prompt"]
    report = engine.check_trace(aml, frameworks=["gdpr"])
    # Schema conformance is framework-agnostic; ESC-001 cites no GDPR article.
    assert rule_ids(report.violations) == {"REC-SCHEMA-001"}
    assert report.gap_regulations == []


def test_frameworks_filter_runs_requested_gap_analysis(engine, aml):
    report = engine.check_trace(aml, frameworks=["eu_ai_act"])
    assert report.gap_regulations == ["eu_ai_act_art86"]


def test_unknown_framework_rejected(engine, aml):
    from record_mcp.inputs import InputError

    with pytest.raises(InputError, match="Unknown framework"):
        engine.check_trace(aml, frameworks=["nope"])


def test_min_severity_and_ordering(engine, aml):
    TRACE_CASES["REC-INT-001"](aml)  # low
    TRACE_CASES["REC-PROV-003"](aml)  # high
    TRACE_CASES["REC-ESC-002"](aml)  # medium
    report = engine.check_trace(aml)
    assert [v.severity for v in report.violations] == ["high", "medium", "low"]
    assert report.status == "fail"
    assert rule_ids(engine.check_trace(aml, min_severity="medium").violations) == {
        "REC-PROV-003", "REC-ESC-002"}


def test_no_gaps(engine, aml):
    del aml["data_at_decision"]
    ids = rule_ids(engine.check_trace(aml, include_gaps=False).violations)
    assert not any(i.startswith("GAP.") for i in ids)


@pytest.mark.parametrize("garbage", [
    {},
    {"record_id": 7, "timestamp": ["x"], "final_action": [], "human_disposition": "yes"},
    {"policy_evaluation": "x", "tools_accessed": [1, None, "a"], "tool_results": {"a": 1},
     "data_at_decision": [{}], "chain_of_thought": [{"step": "one"}], "authority_basis": [3]},
])
def test_malformed_input_never_crashes_checks(engine, garbage):
    report = engine.check_trace(garbage)
    assert report.errors == []
    assert report.status == "fail" and not report.schema_valid
