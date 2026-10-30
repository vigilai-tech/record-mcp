"""Chain rules: small synthetic case histories built from the AML example."""

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import pytest
from conftest import example, rule_ids

T0 = datetime(2026, 5, 20, 11, 0, tzinfo=timezone.utc)


def step(day: float = 0, *, reviewed: bool = False, **changes) -> dict:
    """An AML-case trace `day` days after T0, unreviewed unless *reviewed*."""
    doc = example("aml_triage.json")
    ts = T0 + timedelta(days=day)
    doc["record_id"] = str(uuid.uuid4())
    doc["timestamp"] = ts.isoformat().replace("+00:00", "Z")
    for call in doc["tools_accessed"]:
        call["timestamp"] = (ts - timedelta(seconds=5)).isoformat().replace("+00:00", "Z")
    if reviewed:
        doc["human_disposition"]["reviewed_at"] = (ts + timedelta(hours=1)).isoformat()
    else:
        doc.pop("human_disposition")
    for key, value in changes.items():
        target, _, field = key.partition("__")
        if field:
            doc.setdefault(target, {})[field] = value
        else:
            doc[key] = value
    return doc


def _duplicate_ids() -> list[dict]:
    a, b = step(0), step(1, decision_lineage__case_id="OTHER-CASE")
    b["record_id"] = a["record_id"]
    return [a, b]


CHAIN_CASES: dict[str, Callable[[], list[dict]]] = {
    "REC-CHAIN-001": _duplicate_ids,
    "REC-CHAIN-002": lambda: [step(0), step(1, agent_version="2.2.0")],
    "REC-CHAIN-003": lambda: [
        step(0),  # escalated, awaiting review
        step(1, final_action__type="close_alert", final_action__escalated=False),
    ],
    "REC-CHAIN-004": lambda: [
        step(0, reviewed=True, human_disposition__action="override"),
        step(2),  # proposes draft_sar again
    ],
    "REC-CHAIN-005": lambda: [
        step(0, outcome_linkage__status="completed"),
        step(1, outcome_linkage__status="pending"),
    ],
    "REC-CHAIN-006": lambda: [
        step(0),
        step(45, reviewed=True, outcome_linkage__status="completed"),
    ],
}


@pytest.mark.parametrize("rule_id", sorted(CHAIN_CASES))
def test_chain_rule_fires(engine, rule_id):
    report = engine.check_chain(CHAIN_CASES[rule_id]())
    assert rule_id in rule_ids(report.chain_violations)
    assert not report.errors
    hit = next(v for v in report.chain_violations if v.rule_id == rule_id)
    assert hit.citations and hit.record_ids


def test_clean_case_history(engine):
    report = engine.check_chain([step(0), step(3, reviewed=True)])
    assert report.chain_violations == []
    assert [c.key for c in report.chains] == ["case_id:AML-2026-WR-019847"]


def test_duplicate_detected_across_cases(engine):
    report = engine.check_chain(_duplicate_ids())
    assert len(report.chains) == 2
    dup = [v for v in report.chain_violations if v.rule_id == "REC-CHAIN-001"]
    assert len(dup) == 1 and dup[0].chain_key is None


def test_review_between_escalation_and_close_is_fine(engine):
    chain = [
        step(0),
        step(1, reviewed=True, final_action__type="request_info"),
        step(2, final_action__type="close_alert", final_action__escalated=False),
    ]
    assert "REC-CHAIN-003" not in rule_ids(engine.check_chain(chain).chain_violations)


def test_reasserted_action_after_confirmation_is_fine(engine):
    chain = [
        step(0, reviewed=True, human_disposition__action="override"),
        step(1, reviewed=True, human_disposition__action="confirm"),
        step(2),
    ]
    assert "REC-CHAIN-004" not in rule_ids(engine.check_chain(chain).chain_violations)


def test_sar_within_deadline(engine):
    chain = [step(0), step(20, reviewed=True, outcome_linkage__status="completed")]
    assert "REC-CHAIN-006" not in rule_ids(engine.check_chain(chain).chain_violations)


def test_sar_still_unfiled_past_deadline(engine):
    chain = [step(0), step(40, final_action__type="update_case")]  # outcome still in_progress
    violations = engine.check_chain(chain).chain_violations
    assert "REC-CHAIN-006" in rule_ids(violations)


def test_chain_is_ordered_by_timestamp_not_input_order(engine):
    first, later = CHAIN_CASES["REC-CHAIN-003"]()
    report = engine.check_chain([later, first])
    assert "REC-CHAIN-003" in rule_ids(report.chain_violations)


def test_group_by_none_merges_cases(engine):
    a, b = step(0), step(1, agent_version="9.9.9", decision_lineage__case_id="OTHER")
    assert "REC-CHAIN-002" not in rule_ids(engine.check_chain([a, b]).chain_violations)
    merged = engine.check_chain([a, b], group_by="none")
    assert [c.key for c in merged.chains] == ["input"]
    assert "REC-CHAIN-002" in rule_ids(merged.chain_violations)


def test_chain_report_totals(engine):
    chain = CHAIN_CASES["REC-CHAIN-003"]()
    report = engine.check_chain(chain)
    per_trace = sum(t.counts.total for t in report.traces)
    assert report.counts.total == per_trace + len(report.chain_violations)
    assert report.status == "fail"
