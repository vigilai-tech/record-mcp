"""record-mcp CLI: run the MCP server, or check trace files directly (e.g. in CI)."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from record_mcp import __version__
from record_mcp.catalog import CatalogError, load_catalog
from record_mcp.engine import Engine
from record_mcp.inputs import InputError, load_traces
from record_mcp.models import SEVERITY_RANK, ChainReport, TraceReport, Violation, at_least

SEVERITIES = list(SEVERITY_RANK)[::-1]  # critical … info


def main(argv: list[str] | None = None) -> None:
    parser = _parser()
    args = parser.parse_args(argv)
    # stdout is the MCP channel under stdio, so all logging goes to stderr.
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        catalog = load_catalog(extra_dirs=_extra_dirs(getattr(args, "rules_dir", None)))
    except CatalogError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)

    if args.command in (None, "serve"):
        _serve(args, catalog)
    elif args.command == "check":
        sys.exit(_check(args, catalog))
    elif args.command == "rules":
        _rules(args, catalog)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="record-mcp",
        description="Find regulatory and policy violations in RECORD audit traces.",
    )
    p.add_argument("--version", action="version", version=f"record-mcp {__version__}")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    serve = sub.add_parser("serve", help="Run the MCP server (default command).")
    serve.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    serve.add_argument("--host", default=os.environ.get("RECORD_MCP_HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("RECORD_MCP_PORT", "8000")))
    serve.add_argument("--path", default="/mcp", help="Streamable HTTP endpoint path.")
    serve.add_argument(
        "--allowed-host", action="append", dest="allowed_hosts",
        help="Host header value to accept (repeatable; enables DNS-rebinding protection). "
        "Defaults to $RECORD_MCP_ALLOWED_HOSTS (comma-separated).",
    )
    serve.add_argument("--max-body-mb", type=int, default=16,
                       help="Maximum Streamable HTTP request body size in MiB.")
    _add_rules_dir(serve)

    check = sub.add_parser("check", help="Check trace files and print a report.")
    check.add_argument("files", nargs="+", help=".json (object or array) or .jsonl files")
    check.add_argument("--chain", action="store_true",
                       help="Treat all traces from all files as one input and run chain rules.")
    check.add_argument("--group-by", default="auto",
                       choices=["auto", "case_id", "investigation_thread_id", "none"])
    check.add_argument("--input-format", default="record", choices=["record", "openinference"])
    check.add_argument("--framework", action="append", dest="frameworks",
                       help="Only rules citing this framework (repeatable).")
    check.add_argument("--min-severity", default="info", choices=SEVERITIES)
    check.add_argument("--no-gaps", action="store_true", help="Skip GAP.* field-gap rules.")
    check.add_argument("--fail-on", default="high", choices=SEVERITIES,
                       help="Exit 1 when any violation is at or above this severity.")
    check.add_argument("--format", default="text", choices=["text", "json"])
    _add_rules_dir(check)

    rules = sub.add_parser("rules", help="List the rule catalog.")
    rules.add_argument("--format", default="text", choices=["text", "json", "markdown"])
    rules.add_argument("--no-generated", action="store_true", help="Hide GAP.* rules.")
    _add_rules_dir(rules)
    return p


def _add_rules_dir(p: argparse.ArgumentParser) -> None:
    p.add_argument("--rules-dir", action="append", type=Path,
                   help="Extra rule-pack directory (repeatable). Also $RECORD_MCP_EXTRA_RULES.")


def _extra_dirs(cli_dirs: list[Path] | None) -> list[Path] | None:
    if not cli_dirs:
        return None  # fall back to $RECORD_MCP_EXTRA_RULES
    env = os.environ.get("RECORD_MCP_EXTRA_RULES", "")
    return [*(Path(p) for p in env.split(os.pathsep) if p), *cli_dirs]


# ── serve ─────────────────────────────────────────────────────────────────────


def _serve(args: argparse.Namespace, catalog) -> None:
    from record_mcp.server import build_server

    transport = getattr(args, "transport", "stdio")
    if transport == "stdio":
        build_server(allow_file_input=True, catalog=catalog).run("stdio")
        return

    from mcp.server.transport_security import TransportSecuritySettings

    allowed = args.allowed_hosts or [
        h.strip() for h in os.environ.get("RECORD_MCP_ALLOWED_HOSTS", "").split(",") if h.strip()
    ]
    security = (
        TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=allowed)
        if allowed else None
    )
    build_server(allow_file_input=False, catalog=catalog).run(
        "streamable-http",
        host=args.host,
        port=args.port,
        streamable_http_path=args.path,
        json_response=True,
        stateless_http=True,
        max_request_body_size=args.max_body_mb * 1024 * 1024,
        transport_security=security,
    )


# ── check ─────────────────────────────────────────────────────────────────────


def _check(args: argparse.Namespace, catalog) -> int:
    engine = Engine(catalog)
    opts = dict(frameworks=args.frameworks, min_severity=args.min_severity,
                include_gaps=not args.no_gaps)
    try:
        per_file = [
            (f, load_traces(path=f, input_format=args.input_format, allow_files=True))
            for f in args.files
        ]
        if args.chain:
            docs = [d for _, ds in per_file for d in ds]
            reports: list[tuple[str, TraceReport | ChainReport]] = [
                ("+".join(Path(f).name for f in args.files),
                 engine.check_chain(docs, group_by=args.group_by, **opts))
            ]
        else:
            reports = [
                (f"{Path(f).name}" + (f"[{i}]" if len(ds) > 1 else ""),
                 engine.check_trace(d, index=i, **opts))
                for f, ds in per_file
                for i, d in enumerate(ds)
            ]
    except InputError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        payload = [{"source": name, **r.model_dump(mode="json")} for name, r in reports]
        print(json.dumps(payload if len(payload) > 1 else payload[0], indent=2))
    else:
        for name, report in reports:
            _print_report(name, report)

    worst = [v for _, r in reports for v in _all_violations(r)]
    return 1 if any(at_least(v.severity, args.fail_on) for v in worst) else 0


def _all_violations(report: TraceReport | ChainReport) -> list[Violation]:
    if isinstance(report, ChainReport):
        return report.chain_violations + [v for t in report.traces for v in t.violations]
    return report.violations


def _print_report(name: str, report: TraceReport | ChainReport) -> None:
    print(f"\nRECORD violation check — {name}")
    print("═" * 60)
    c = report.counts
    print(f"Status: {report.status.upper()}   critical {c.critical} · high {c.high} · "
          f"medium {c.medium} · low {c.low} · info {c.info}")
    if isinstance(report, ChainReport):
        print(f"Chains: {', '.join(g.key for g in report.chains)}")
        _print_violations("Chain violations", report.chain_violations)
        for t in report.traces:
            _print_violations(f"Trace {t.index} ({t.record_id})", t.violations)
    else:
        print(f"Record: {report.record_id}   schema valid: {report.schema_valid}   "
              f"gap analyses: {', '.join(report.gap_regulations) or '—'}")
        _print_violations("Violations", report.violations)
    for err in report.errors:
        print(f"  ! {err}")


def _print_violations(heading: str, violations: list[Violation]) -> None:
    if not violations:
        return
    print(f"\n{heading}")
    for v in violations:
        cites = "; ".join(f"{c.framework} {c.ref}" for c in v.citations)
        print(f"  [{v.severity.upper():8}] {v.rule_id}  {v.path or ''}")
        print(f"             {v.message}")
        print(f"             ↳ {cites}")


# ── rules ─────────────────────────────────────────────────────────────────────


def _rules(args: argparse.Namespace, catalog) -> None:
    rules = [r for r in catalog.rules.values() if not (args.no_generated and r.source != "catalog")]
    if args.format == "json":
        print(json.dumps([r.model_dump(mode="json") for r in rules], indent=2))
    elif args.format == "markdown":
        print("| Rule | Scope | Severity | Title | Citations |")
        print("|---|---|---|---|---|")
        def md(s: str) -> str:
            return s.replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")

        for r in rules:
            cites = "; ".join(f"{c.framework} {c.ref}" for c in r.citations)
            print(f"| `{r.id}` | {r.scope} | {r.severity} | {md(r.title)} | {md(cites)} |")
    else:
        for r in rules:
            print(f"{r.id:<42} {r.scope:<6} {r.severity:<9} {r.title}")
