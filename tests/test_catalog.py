"""Catalog lint: what CI enforces for every contributed rule."""

import json
from pathlib import Path

import pytest
from record_py.gap_analyzer import KNOWN_REGULATIONS

from record_mcp.catalog import CatalogError, default_rules_dir, load_catalog
from record_mcp.checks import CHAIN_CHECKS, TRACE_CHECKS
from record_mcp.checks.builtin import SCHEMA_PATH

REPO = Path(__file__).resolve().parents[1]
UPSTREAM_SCHEMA = REPO.parent / "record-spec" / "schema.json"


def test_rules_dir_is_repo_catalog():
    assert default_rules_dir().resolve() == (REPO / "rules").resolve()


def test_every_rule_is_cited_and_documented(catalog):
    for rule in catalog.rules.values():
        assert rule.citations, rule.id
        assert rule.description.strip() and rule.remediation.strip(), rule.id


def test_every_check_is_used(catalog):
    used = {r.check for r in catalog.rules.values()}
    assert set(TRACE_CHECKS) | set(CHAIN_CHECKS) == used


def test_gap_rules_cover_every_record_py_regulation(catalog):
    generated = {r.params["regulation"] for r in catalog.rules.values()
                 if r.check == "regulation_field_gap"}
    assert generated == set(KNOWN_REGULATIONS)


def test_known_prefixes_injected(catalog):
    prefixes = catalog.rule("REC-INT-002").params["known_prefixes"]
    assert {"eu_ai_act", "mifid2", "fincen_aml_nprm_2026", "sox"} <= set(prefixes)


def test_examples_only_use_known_frameworks(engine):
    for name in ("aml_triage.json", "trade_surveillance_l1.json",
                 "communication_surveillance_l1.json"):
        from conftest import example
        ids = {v.rule_id for v in engine.check_trace(example(name)).violations}
        assert "REC-INT-002" not in ids, name


@pytest.mark.skipif(not UPSTREAM_SCHEMA.exists(), reason="record-spec checkout not alongside")
def test_vendored_schema_matches_sibling_record_spec():
    assert json.loads(SCHEMA_PATH.read_text()) == json.loads(UPSTREAM_SCHEMA.read_text())


# ── extra rule packs ──────────────────────────────────────────────────────────

PACK_RULE = """\
id: ACME-ESC-003
title: High-severity autonomous decision not escalated (ACME threshold)
scope: trace
severity: critical
category: escalation
check: autonomous_high_severity_not_escalated
params: {severity_threshold: 5.0}
applies_to: {operating_domains: [autonomous]}
citations: [{framework: eu_ai_act, ref: "Art. 14(4)(e)"}]
description: ACME escalates at 5.0.
remediation: Escalate.
"""


def test_extra_rule_pack_reuses_builtin_check(tmp_path, aml):
    (tmp_path / "ACME-ESC-003.yaml").write_text(PACK_RULE)
    from record_mcp.engine import Engine

    engine = Engine(load_catalog(extra_dirs=[tmp_path]))
    aml.update(operating_domain="autonomous")
    aml.pop("human_disposition")
    aml["final_action"].update(escalated=False, severity_score=6.0)
    ids = {v.rule_id for v in engine.check_trace(aml).violations}
    assert "ACME-ESC-003" in ids and "REC-ESC-003" not in ids


@pytest.mark.parametrize("mutate, message", [
    (lambda s: s.replace("ACME-ESC-003", "REC-ESC-003"), "duplicate rule id"),
    (lambda s: s.replace("check: autonomous_high", "check: nope_high"), "no trace check"),
    (lambda s: s.replace("framework: eu_ai_act", "framework: made_up"), "citation framework"),
    (lambda s: s.replace("citations: [{framework: eu_ai_act, ref: \"Art. 14(4)(e)\"}]",
                         "citations: []"), "citations"),
])
def test_bad_rule_pack_rejected(tmp_path, mutate, message):
    text = mutate(PACK_RULE)
    rule_id = "REC-ESC-003" if "id: REC-ESC-003" in text else "ACME-ESC-003"
    (tmp_path / f"{rule_id}.yaml").write_text(text)
    with pytest.raises(CatalogError, match=message):
        load_catalog(extra_dirs=[tmp_path])


def test_file_name_must_match_id(tmp_path):
    (tmp_path / "WRONG.yaml").write_text(PACK_RULE)
    with pytest.raises(CatalogError, match="file name"):
        load_catalog(extra_dirs=[tmp_path])
