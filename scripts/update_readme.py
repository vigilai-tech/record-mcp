"""Regenerate the rule table between the rules:start / rules:end markers in README.md."""

from __future__ import annotations

import contextlib
import io
import re
import sys
from pathlib import Path

from record_mcp.cli import main as cli

README = Path(__file__).resolve().parents[1] / "README.md"
MARKERS = re.compile(r"(<!-- rules:start -->\n).*?(\n<!-- rules:end -->)", re.S)


def rule_table() -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli(["rules", "--format", "markdown", "--no-generated"])
    return buf.getvalue().strip()


def render(readme: str) -> str:
    return MARKERS.sub(lambda m: m.group(1) + rule_table() + m.group(2), readme)


if __name__ == "__main__":
    current = README.read_text(encoding="utf-8")
    updated = render(current)
    if "--check" in sys.argv:
        if updated != current:
            sys.exit("README rule table is stale; run scripts/update_readme.py")
    README.write_text(updated, encoding="utf-8")
