"""Savings-analyst agent: prioritize by ROI and spot commitment gaps.

Two judgment calls a spreadsheet can't make well:

1. **Priority order.** Ranking pure dollars is easy (Python already does it);
   weighing a $40 one-click delete against a $140 fix that needs a maintenance
   window — factoring remediation effort and the reviewed confidence — is a
   judgment call. The agent returns a recommended action order.
2. **Commitment gaps.** Steady-state EC2/RDS spend is usually a Reserved
   Instance or Savings Plan opportunity. We can't see existing commitments from
   Cost Explorer's service totals, so the agent narrates the *opportunity*
   ("if this compute isn't already covered...") rather than asserting a number.

Facts (dollar amounts, effort labels) come from Python; the agent only orders
and narrates. Unavailable agent layer -> returns ``None`` and the report falls
back to the deterministic ranking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from costdetective.agents import base
from costdetective.cost_explorer import SpendSummary
from costdetective.models import Finding

log = logging.getLogger(__name__)


@dataclass
class SavingsAnalysis:
    """The savings-analyst's output, attached to the audit result."""

    priority_order: list[int]  # indices into the findings list, best ROI first
    strategy: str  # one or two sentences on how the order was chosen
    commitment_note: str  # RI / Savings Plan narrative from steady-state spend

    def ordered(self, findings: list[Finding]) -> list[Finding]:
        """Resolve ``priority_order`` back to findings (safe against bad indices)."""
        return [findings[i] for i in self.priority_order if 0 <= i < len(findings)]


_SYSTEM = """\
You are a FinOps savings analyst. You are given a list of validated AWS
cost-waste findings and a breakdown of the account's real monthly spend by
service.

Do two things:

1. Rank the findings into a recommended remediation order (best return on effort
   first). Weigh the dollar impact against the remediation effort (low/medium/
   high) and the confidence — a high-dollar, low-effort, high-confidence fix
   should come first; a low-dollar or shaky finding later. Return the order as a
   list of finding indices, every index included exactly once.

2. Write a short "commitment_note" (2-4 sentences) on Reserved Instance and
   Savings Plan opportunities, based on the steady-state spend (especially EC2
   and RDS). You cannot see existing commitments, so frame it as opportunity
   ("if this compute is running on on-demand rates, a 1-year Compute Savings
   Plan typically cuts 30-60%..."). Be concrete about which services, but never
   invent specific dollar figures — reference only the spend numbers provided.

Do not recompute or second-guess the dollar amounts; they are already correct.
Base everything on the facts given."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "priority_order": {"type": "array", "items": {"type": "integer"}},
        "strategy": {"type": "string"},
        "commitment_note": {"type": "string"},
    },
    "required": ["priority_order", "strategy", "commitment_note"],
    "additionalProperties": False,
}


def analyze(
    findings: list[Finding], spend: SpendSummary | None
) -> SavingsAnalysis | None:
    """Return a :class:`SavingsAnalysis`, or ``None`` if the agent is unavailable."""
    if not findings:
        return None

    user = _describe_findings(findings) + "\n\n" + _describe_spend(spend)
    result = base.call_json(_SYSTEM, user, _SCHEMA, max_tokens=1200)
    if result is None:
        log.info("savings_analyst: agent layer unavailable — no ROI analysis")
        return None

    order = _clean_order(result.get("priority_order", []), len(findings))
    return SavingsAnalysis(
        priority_order=order,
        strategy=str(result.get("strategy", "")).strip(),
        commitment_note=str(result.get("commitment_note", "")).strip(),
    )


def _describe_findings(findings: list[Finding]) -> str:
    lines = ["Findings:"]
    for i, f in enumerate(findings):
        lines.append(
            f"[{i}] ${f.monthly_savings:.2f}/mo  effort={f.effort}  "
            f"confidence={f.confidence:.2f}  {f.detector}: {f.summary}"
        )
    return "\n".join(lines)


def _describe_spend(spend: SpendSummary | None) -> str:
    if spend is None:
        return "Real spend breakdown: unavailable."
    lines = [
        f"Real spend over the last {spend.days} days: "
        f"${spend.total_usd:,.2f} total, by service:"
    ]
    for service, amount in sorted(
        spend.by_service.items(), key=lambda kv: kv[1], reverse=True
    ):
        lines.append(f"  {service}: ${amount:,.2f}")
    return "\n".join(lines)


def _clean_order(order: list, n: int) -> list[int]:
    """Keep valid, unique indices in the agent's order; append any it missed.

    Guarantees a permutation of ``range(n)`` so no finding is ever dropped from
    the report even if the model returns a partial or malformed list.
    """
    seen: set[int] = set()
    cleaned: list[int] = []
    for i in order:
        if isinstance(i, int) and 0 <= i < n and i not in seen:
            cleaned.append(i)
            seen.add(i)
    for i in range(n):
        if i not in seen:
            cleaned.append(i)
    return cleaned
