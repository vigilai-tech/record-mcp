"""Registry that binds rule YAML ``check:`` names to Python functions."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from record_mcp.models import Rule


@dataclass
class Finding:
    """One occurrence of a rule violation, before the engine attaches rule metadata."""

    message: str
    path: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    # Chain checks name the traces involved; trace checks leave this empty.
    record_ids: list[str] = field(default_factory=list)


TraceCheck = Callable[[dict[str, Any], Rule], Iterable[Finding]]
ChainCheck = Callable[[list[dict[str, Any]], Rule], Iterable[Finding]]


@dataclass(frozen=True)
class ChainCheckSpec:
    fn: ChainCheck
    # Input-wide checks run once over every trace passed in, not per case/thread group.
    input_wide: bool = False


TRACE_CHECKS: dict[str, TraceCheck] = {}
CHAIN_CHECKS: dict[str, ChainCheckSpec] = {}


def trace_check(name: str) -> Callable[[TraceCheck], TraceCheck]:
    def register(fn: TraceCheck) -> TraceCheck:
        if name in TRACE_CHECKS or name in CHAIN_CHECKS:
            raise ValueError(f"check {name!r} registered twice")
        TRACE_CHECKS[name] = fn
        return fn

    return register


def chain_check(name: str, *, input_wide: bool = False) -> Callable[[ChainCheck], ChainCheck]:
    def register(fn: ChainCheck) -> ChainCheck:
        if name in TRACE_CHECKS or name in CHAIN_CHECKS:
            raise ValueError(f"check {name!r} registered twice")
        CHAIN_CHECKS[name] = ChainCheckSpec(fn, input_wide)
        return fn

    return register
