"""Command-line interface: ``python -m costdetective audit [...]``."""

from __future__ import annotations

import argparse
import logging

from costdetective import storage
from costdetective.report import write_report
from costdetective.scan import AuditResult, run_audit


def _load_env() -> None:
    """Load ``.env`` (e.g. ``ANTHROPIC_API_KEY``) if python-dotenv is present.

    Optional by design: a missing package or file leaves real environment
    variables untouched, so the tool still runs without it.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()

DEFAULT_REGION = "us-east-1"
DEFAULT_OUTPUT = "report.html"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="costdetective",
        description="Read-only AWS FinOps auditor: find wasted spend and price it.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    audit = sub.add_parser("audit", help="Scan for waste and write an HTML report.")
    audit.add_argument(
        "--region",
        default=DEFAULT_REGION,
        help=f"AWS region to scan (default: {DEFAULT_REGION}).",
    )
    audit.add_argument(
        "--synthetic",
        action="store_true",
        help="Use built-in sample findings; makes no AWS calls.",
    )
    audit.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"Where to write the HTML report (default: {DEFAULT_OUTPUT}).",
    )
    audit.set_defaults(func=_cmd_audit)
    return parser


def _print_table(result: AuditResult) -> None:
    """Print a ranked, aligned table of findings to the console."""
    findings = result.findings
    if not findings:
        if result.synthetic:
            print("No findings in the synthetic sample.")
        else:
            print(
                f"No waste found in region {result.region}. "
                "(Try --synthetic for a sample report, or --region for another region.)"
            )
        return

    headers = ("#", "$/MONTH", "SEV", "DETECTOR", "RESOURCE", "SUMMARY")
    rows: list[tuple[str, ...]] = []
    for i, f in enumerate(findings, start=1):
        rows.append(
            (
                str(i),
                f"${f.monthly_savings:,.2f}",
                f.severity.value.upper(),
                f.detector,
                f.resource_id,
                _truncate(f.summary, 60),
            )
        )

    widths = [
        max(len(headers[c]), *(len(r[c]) for r in rows)) for c in range(len(headers))
    ]
    line = "  ".join(h.ljust(widths[c]) for c, h in enumerate(headers))
    print(line)
    print("  ".join("-" * widths[c] for c in range(len(headers))))
    for r in rows:
        print("  ".join(r[c].ljust(widths[c]) for c in range(len(headers))))


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _cmd_audit(args: argparse.Namespace) -> int:
    mode = "synthetic" if args.synthetic else "live AWS"
    print(f"Running audit ({mode}) in region {args.region}...\n")

    result = run_audit(region=args.region, synthetic=args.synthetic)
    _print_table(result)

    print(
        f"\nTotal recoverable: ${result.total_monthly_savings:,.2f}/month "
        f"(${result.total_annual_savings:,.2f}/year) "
        f"across {len(result.findings)} finding(s)."
    )

    # Persist this run, then load history of the same mode (real vs. synthetic)
    # so the report can chart a trend without a demo polluting real history.
    # Both calls are internally defensive: failures log and never raise.
    storage.save_run(result)
    history = storage.load_recent_runs(synthetic=result.synthetic)

    path = write_report(result, args.output, history=history)
    print(f"Report written to {path.resolve()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    _load_env()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
