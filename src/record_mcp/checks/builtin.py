"""Checks that delegate to record-py: schema conformance and per-regulation field gaps."""

from __future__ import annotations

from collections.abc import Iterator
from functools import cache, lru_cache
from pathlib import Path
from typing import Any

from record_py.gap_analyzer import GapAnalyzer
from record_py.validator import RecordValidator

from record_mcp.checks.registry import Finding, trace_check
from record_mcp.models import Rule

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "data" / "schema.json"


@lru_cache(maxsize=1)
def validator() -> RecordValidator:
    # RecordValidator's default path is the record-spec repo root, which does not exist
    # once record-py is pip-installed, so always point it at the vendored copy.
    return RecordValidator(schema_path=SCHEMA_PATH)


@cache
def gap_analyzer(regulation: str) -> GapAnalyzer:
    return GapAnalyzer(regulation)


def _pointer(dotted: str) -> str:
    """RecordValidator reports 'a.b.0' (or '<root>'); convert to a JSON pointer."""
    if dotted == "<root>":
        return ""
    return "/" + "/".join(p.replace("~", "~0").replace("/", "~1") for p in dotted.split("."))


@trace_check("schema_conformance")
def schema_conformance(doc: dict[str, Any], rule: Rule) -> Iterator[Finding]:
    for err in validator().validate(doc)["errors"]:
        where, _, message = err.partition(": ")
        yield Finding(
            message=f"Schema violation at {where}: {message}",
            path=_pointer(where),
            evidence={"validator_error": err},
        )


@trace_check("regulation_field_gap")
def regulation_field_gap(doc: dict[str, Any], rule: Rule) -> Iterator[Finding]:
    regulation, field, level = rule.params["regulation"], rule.params["field"], rule.params["level"]
    report = gap_analyzer(regulation).analyze(doc)
    for entry in report[level]:
        if entry["field"] == field and not entry["present"]:
            yield Finding(
                message=f"{report['regulation_name']} {level} field `{field}` is missing or "
                f"empty ({entry['article']}): {entry['reason']}.",
                path=f"/{field}",
                evidence={"regulation": regulation, "level": level},
            )
