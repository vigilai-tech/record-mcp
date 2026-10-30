"""Importing this package registers every built-in check."""

from record_mcp.checks import builtin, chain, trace  # noqa: F401
from record_mcp.checks.registry import CHAIN_CHECKS, TRACE_CHECKS, Finding

__all__ = ["CHAIN_CHECKS", "TRACE_CHECKS", "Finding"]
