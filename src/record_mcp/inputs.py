"""Turns tool / CLI input (inline JSON, a .json file, or a .jsonl file) into RECORD dicts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from record_py.openinference_adapter import adapt_span

InputFormat = Literal["record", "openinference"]

MAX_FILE_BYTES = 64 * 1024 * 1024


class InputError(ValueError):
    pass


def load_traces(
    inline: Any = None,
    path: str | None = None,
    *,
    input_format: InputFormat = "record",
    allow_files: bool,
) -> list[dict[str, Any]]:
    """Return the traces described by exactly one of *inline* or *path*."""
    if (inline is None) == (path is None):
        raise InputError("Provide exactly one of an inline trace/traces or a file path.")
    if path is not None:
        if not allow_files:
            raise InputError(
                "File paths are only accepted when record-mcp runs over stdio; pass the trace "
                "inline instead."
            )
        docs = _read_file(Path(path).expanduser())
    else:
        docs = _from_inline(inline)

    if not docs:
        raise InputError("No traces found in input.")
    for i, doc in enumerate(docs):
        if not isinstance(doc, dict):
            raise InputError(f"Trace {i} is a JSON {type(doc).__name__}, expected an object.")
    if input_format == "openinference":
        return [adapt_span(span) for span in docs]
    return docs


def _from_inline(value: Any) -> list[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            # Fall back to JSONL for string input with one object per line.
            lines = [ln for ln in value.splitlines() if ln.strip()]
            if len(lines) < 2:
                raise InputError(f"Trace is not valid JSON: {exc}") from exc
            return _parse_jsonl(lines, "inline input")
    return value if isinstance(value, list) else [value]


def _read_file(path: Path) -> list[Any]:
    if not path.is_file():
        raise InputError(f"File not found: {path}")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise InputError(f"{path} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MiB.")
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".jsonl", ".ndjson"):
        return _parse_jsonl(raw.splitlines(), str(path))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InputError(f"{path} is not valid JSON: {exc}") from exc
    return data if isinstance(data, list) else [data]


def _parse_jsonl(lines: list[str], where: str) -> list[Any]:
    docs = []
    for n, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            docs.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise InputError(f"{where} line {n} is not valid JSON: {exc}") from exc
    return docs
