"""Vendor RECORD's schema.json from record-spec, or check that the vendored copy is current.

By default the source is the record-spec commit pinned for record-py in pyproject.toml, so the
schema and the record-py code that validates against it always come from the same commit.

    python scripts/sync_schema.py            # update src/record_mcp/data/schema.json
    python scripts/sync_schema.py --check    # exit 1 if it differs from the pinned commit
    python scripts/sync_schema.py --ref main --check   # is upstream ahead of us?
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VENDORED = REPO / "src" / "record_mcp" / "data" / "schema.json"
RAW_URL = "https://raw.githubusercontent.com/vigilai-tech/record-spec/{ref}/schema.json"


def pinned_ref() -> str:
    match = re.search(r"record-spec\.git@([0-9a-f]{7,40})", (REPO / "pyproject.toml").read_text())
    if not match:
        sys.exit("Could not find the record-py pin in pyproject.toml")
    return match.group(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ref", help="record-spec git ref (default: the pinned commit)")
    parser.add_argument("--from", dest="source", type=Path, help="Copy from a local schema.json")
    parser.add_argument("--check", action="store_true", help="Only compare; exit 1 on drift")
    args = parser.parse_args()

    if args.source:
        upstream, origin = args.source.read_text(encoding="utf-8"), str(args.source)
    else:
        ref = args.ref or pinned_ref()
        origin = RAW_URL.format(ref=ref)
        with urllib.request.urlopen(origin, timeout=30) as resp:  # noqa: S310 (fixed https URL)
            upstream = resp.read().decode("utf-8")

    same = json.loads(upstream) == json.loads(VENDORED.read_text(encoding="utf-8"))
    if args.check:
        print(f"{'up to date with' if same else 'DIFFERS from'} {origin}")
        sys.exit(0 if same else 1)
    if not same:
        VENDORED.write_text(upstream, encoding="utf-8")
    print(f"{'unchanged' if same else 'updated'} from {origin}")


if __name__ == "__main__":
    main()
