"""Loads the YAML violation catalog (plus rules generated from record-py's GapAnalyzer)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from pathlib import Path

import yaml
from pydantic import ValidationError
from record_py.gap_analyzer import KNOWN_REGULATIONS

from record_mcp.checks import CHAIN_CHECKS, TRACE_CHECKS
from record_mcp.checks.builtin import gap_analyzer
from record_mcp.models import Citation, Framework, GapAnalysis, Rule

RULES_DIR_ENV = "RECORD_MCP_RULES_DIR"
EXTRA_RULES_ENV = "RECORD_MCP_EXTRA_RULES"
FRAMEWORKS_FILE = "frameworks.yaml"


class CatalogError(ValueError):
    pass


@dataclass
class Catalog:
    rules: dict[str, Rule]
    frameworks: dict[str, Framework]
    gap_analyses: list[GapAnalysis]
    # Raw YAML for catalog rules, keyed by rule id (served as MCP resources).
    sources: dict[str, str] = field(default_factory=dict)

    def rule(self, rule_id: str) -> Rule:
        try:
            return self.rules[rule_id]
        except KeyError:
            raise CatalogError(f"Unknown rule {rule_id!r}") from None

    def rule_yaml(self, rule_id: str) -> str:
        rule = self.rule(rule_id)
        if rule_id in self.sources:
            return self.sources[rule_id]
        return yaml.safe_dump(rule.model_dump(mode="json"), sort_keys=False, allow_unicode=True)


def default_rules_dir() -> Path:
    """$RECORD_MCP_RULES_DIR, else the copy packaged in the wheel, else the repo checkout."""
    if env := os.environ.get(RULES_DIR_ENV):
        return Path(env)
    packaged = resources.files("record_mcp") / "rules"
    if packaged.is_dir():
        return Path(str(packaged))
    return Path(__file__).resolve().parents[2] / "rules"


def extra_rules_dirs() -> list[Path]:
    raw = os.environ.get(EXTRA_RULES_ENV, "")
    return [Path(p) for p in raw.split(os.pathsep) if p]


def load_catalog(rules_dir: Path | None = None, extra_dirs: list[Path] | None = None) -> Catalog:
    rules_dir = rules_dir or default_rules_dir()
    extra_dirs = extra_rules_dirs() if extra_dirs is None else extra_dirs
    frameworks, gap_analyses = _load_frameworks(rules_dir / FRAMEWORKS_FILE)

    rules: dict[str, Rule] = {}
    sources: dict[str, str] = {}
    for directory in [rules_dir, *extra_dirs]:
        if not directory.is_dir():
            raise CatalogError(f"Rules directory not found: {directory}")
        for path in sorted(directory.rglob("*.yaml")):
            if path.name == FRAMEWORKS_FILE:
                continue
            raw = path.read_text(encoding="utf-8")
            rule = _parse_rule(path, raw)
            if rule.id in rules:
                raise CatalogError(f"{path}: duplicate rule id {rule.id!r}")
            rules[rule.id] = rule
            sources[rule.id] = raw

    for rule in _gap_rules(gap_analyses, frameworks):
        rules[rule.id] = rule

    _inject_known_prefixes(rules, frameworks)
    _validate(rules, frameworks)
    return Catalog(rules=rules, frameworks=frameworks, gap_analyses=gap_analyses, sources=sources)


@lru_cache(maxsize=1)
def default_catalog() -> Catalog:
    return load_catalog()


def _load_frameworks(path: Path) -> tuple[dict[str, Framework], list[GapAnalysis]]:
    if not path.exists():
        raise CatalogError(f"{path} not found")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    try:
        frameworks = {f["id"]: Framework.model_validate(f) for f in data.get("frameworks", [])}
        gaps = [GapAnalysis.model_validate(g) for g in data.get("gap_analyses", [])]
    except (ValidationError, KeyError, TypeError) as exc:
        raise CatalogError(f"{path}: {exc}") from exc
    return frameworks, gaps


def _parse_rule(path: Path, raw: str) -> Rule:
    try:
        rule = Rule.model_validate(yaml.safe_load(raw))
    except (ValidationError, yaml.YAMLError) as exc:
        raise CatalogError(f"{path}: {exc}") from exc
    if path.stem != rule.id:
        raise CatalogError(f"{path}: file name must match rule id {rule.id!r}")
    return rule


def _gap_rules(gap_analyses: list[GapAnalysis], frameworks: dict[str, Framework]) -> list[Rule]:
    """One rule per (regulation, field) in record-py's GapAnalyzer.

    Analyzing an empty document reports every field as missing, which enumerates the
    regulation's field table through GapAnalyzer's public API.
    """
    by_regulation = {g.regulation: g for g in gap_analyses}
    rules = []
    for regulation in KNOWN_REGULATIONS:
        gap = by_regulation.get(regulation)
        if gap is None:
            raise CatalogError(
                f"record-py regulation {regulation!r} has no gap_analyses entry in "
                f"{FRAMEWORKS_FILE}"
            )
        report = gap_analyzer(regulation).analyze({})
        for level, severity in (("required", "high"), ("recommended", "low")):
            for entry in report[level]:
                rules.append(
                    Rule(
                        id=f"GAP.{regulation}.{entry['field']}",
                        title=f"{report['regulation_name']}: {level} field "
                        f"`{entry['field']}` missing",
                        scope="trace",
                        severity=severity,
                        category="field_gap",
                        check="regulation_field_gap",
                        params={"regulation": regulation, "field": entry["field"], "level": level},
                        citations=[Citation(framework=gap.framework, ref=entry["article"],
                                            url=frameworks[gap.framework].url)],
                        description=entry["reason"] + ".",
                        remediation=f"Populate `{entry['field']}` when the trace is governed by "
                        f"{report['regulation_name']}.",
                        fields=[entry["field"]],
                        source="record-py GapAnalyzer",
                    )
                )
    return rules


def _inject_known_prefixes(rules: dict[str, Rule], frameworks: dict[str, Framework]) -> None:
    # unknown_authority_framework compares against the framework registry unless a rule
    # pins its own list.
    prefixes = sorted({p for f in frameworks.values() for p in f.authority_prefixes})
    for rule in rules.values():
        if rule.check == "unknown_authority_framework":
            rule.params.setdefault("known_prefixes", prefixes)


def _validate(rules: dict[str, Rule], frameworks: dict[str, Framework]) -> None:
    for rule in rules.values():
        registry = TRACE_CHECKS if rule.scope == "trace" else CHAIN_CHECKS
        if rule.check not in registry:
            raise CatalogError(f"{rule.id}: no {rule.scope} check named {rule.check!r}")
        for c in rule.citations:
            if c.framework not in frameworks:
                raise CatalogError(f"{rule.id}: citation framework {c.framework!r} is not in "
                                   f"{FRAMEWORKS_FILE}")
