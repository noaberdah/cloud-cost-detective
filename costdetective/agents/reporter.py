"""Reporter agent: write the executive-summary prose for the report.

The one place we want natural language, not a table. It reads the finished
picture — reviewed findings, the recoverable total, real spend, any week-over-
week spikes, and the savings analyst's notes — and writes a few tight paragraphs
for the top of the report, aimed at an engineering manager or FinOps lead.

It writes prose only. Every number it cites already exists in the result; it
must not invent figures. Unavailable agent layer -> returns ``None`` and the
report simply omits the written summary.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from costdetective.agents import base

if TYPE_CHECKING:  # avoid a circular import with scan.py at runtime
    from costdetective.scan import AuditResult

log = logging.getLogger(__name__)

_SYSTEM = """\
You are a FinOps lead writing the executive summary at the top of an AWS
cost-audit report. Your reader is an engineering manager who wants the point
fast.

Write 2-3 short paragraphs of plain prose. Output PLAIN TEXT ONLY: no Markdown
syntax at all — no headings (#), no bold (**), no bullet or numbered lists.
Separate paragraphs with a single blank line. Content:
- Lead with the headline: how much is recoverable per month and per year, and
  from how many findings.
- Call out the biggest one or two opportunities by name, and note if any large
  finding carries a caveat (low confidence / needs human review).
- Mention any spend spike and the Reserved Instance / Savings Plan opportunity
  if relevant.
- End with a one-line next step.

Every number you cite is provided below — use only those figures, never invent
or recompute values. Be direct and concrete; no filler, no hedging boilerplate."""


def write_summary(result: "AuditResult") -> str | None:
    """Return executive-summary prose, or ``None`` if the agent is unavailable."""
    if not result.findings:
        return None
    return base.call_text(_SYSTEM, _digest(result), max_tokens=700)


def _digest(result: "AuditResult") -> str:
    """Assemble the facts the summary may draw on — nothing it can't cite."""
    lines = [
        f"Recoverable: ${result.total_monthly_savings:,.2f}/month "
        f"(${result.total_annual_savings:,.2f}/year) across "
        f"{len(result.findings)} finding(s).",
    ]
    if result.spend is not None:
        lines.append(
            f"Real spend last {result.spend.days} days: "
            f"${result.spend.total_usd:,.2f}."
        )
        pct = result.savings_pct_of_spend
        if pct is not None:
            lines.append(f"Recoverable is {pct}% of recent spend.")

    lines.append("\nFindings (highest dollar first):")
    for f in sorted(result.findings, key=lambda x: x.monthly_savings, reverse=True):
        note = f.details.get("ai_confidence_reason", "")
        lines.append(
            f"- ${f.monthly_savings:,.2f}/mo, {f.detector}, effort={f.effort}, "
            f"confidence={f.confidence:.2f}: {f.summary}"
            + (f" [caveat: {note}]" if note else "")
        )

    if result.anomalies:
        lines.append("\nSpend spikes (week over week):")
        for a in result.anomalies:
            lines.append(
                f"- {a.service}: ${a.previous_week_usd:,.2f} -> "
                f"${a.this_week_usd:,.2f} ({a.pct_change:+.0%})"
            )

    analysis = getattr(result, "savings_analysis", None)
    if analysis is not None:
        if analysis.strategy:
            lines.append(f"\nPrioritization strategy: {analysis.strategy}")
        if analysis.commitment_note:
            lines.append(f"Commitment opportunity: {analysis.commitment_note}")

    return "\n".join(lines)
