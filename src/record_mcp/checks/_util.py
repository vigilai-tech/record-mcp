"""Defensive accessors: checks run on schema-invalid traces too, so never assume shape."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


def obj(doc: Any, key: str) -> dict[str, Any]:
    val = doc.get(key) if isinstance(doc, dict) else None
    return val if isinstance(val, dict) else {}


def items(doc: Any, key: str) -> list[tuple[int, dict[str, Any]]]:
    """(index, item) pairs for the dict entries of a list field."""
    val = doc.get(key) if isinstance(doc, dict) else None
    if not isinstance(val, list):
        return []
    return [(i, it) for i, it in enumerate(val) if isinstance(it, dict)]


def text(val: Any) -> str:
    return val.strip() if isinstance(val, str) else ""


def record_id(doc: dict[str, Any]) -> str:
    rid = doc.get("record_id")
    return rid if isinstance(rid, str) and rid else "<missing record_id>"


def parse_ts(val: Any) -> datetime | None:
    """Parse an ISO 8601 timestamp; naive results are returned as-is (callers decide)."""
    if not isinstance(val, str) or not val:
        return None
    s = val.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def is_utc(ts: datetime) -> bool:
    return ts.utcoffset() == timedelta(0)


def comparable(ts: datetime | None) -> datetime | None:
    """Only offset-aware timestamps can be ordered safely against each other."""
    return ts if ts is not None and ts.utcoffset() is not None else None


def escalated(doc: dict[str, Any]) -> bool:
    return obj(doc, "final_action").get("escalated") is True


def action_type(doc: dict[str, Any]) -> str:
    return text(obj(doc, "final_action").get("type"))


def has_human_disposition(doc: dict[str, Any]) -> bool:
    return bool(text(obj(doc, "human_disposition").get("action")))
