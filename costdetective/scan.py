"""Orchestrates a single audit run: gather findings, rank them, summarize.

Real-mode flow:
1. Bind the live Pricing API client so detectors get current per-resource costs
   (with the hardcoded map as a silent fallback).
2. Run every read-only detector; one broken detector is logged and skipped.
3. Fetch real spend + week-over-week anomalies from Cost Explorer.
4. Group findings by owner/team tag for the report.

Synthetic mode skips the AWS calls and returns canned sample data so the
pipeline can be exercised offline.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from costdetective import pricing
from costdetective.cost_explorer import (
    CostExplorerResult,
    SpendAnomaly,
    SpendSummary,
    gather_cost_explorer_data,
)
from costdetective.detectors import (
    ebs_gp2_to_gp3,
    ebs_unattached,
    ec2_idle,
    ec2_rightsize,
    eip_unused,
    lb_idle,
    rds_overprovisioned,
    snapshots_orphaned,
)
from costdetective.models import (
    Finding,
    group_findings_by_owner,
    rank_findings,
)

log = logging.getLogger(__name__)

# The deterministic, read-only AWS detectors, run in order. Each module exposes
# a ``NAME`` and a ``detect(session, region) -> list[Finding]`` function.
DETECTORS = [
    ebs_unattached,
    ebs_gp2_to_gp3,
    snapshots_orphaned,
    ec2_idle,
    ec2_rightsize,
    eip_unused,
    lb_idle,
    rds_overprovisioned,
]


@dataclass
class AuditResult:
    """Everything a single audit produced — the input to the report."""

    region: str
    synthetic: bool
    findings: list[Finding]
    spend: SpendSummary | None = None
    anomalies: list[SpendAnomaly] = field(default_factory=list)
    spend_error: str | None = None
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def total_monthly_savings(self) -> float:
        return round(sum(f.monthly_savings for f in self.findings), 2)

    @property
    def total_annual_savings(self) -> float:
        return round(self.total_monthly_savings * 12, 2)

    @property
    def findings_by_owner(self) -> dict[str, list[Finding]]:
        return group_findings_by_owner(self.findings)

    @property
    def savings_pct_of_spend(self) -> float | None:
        """Recoverable as a percentage of last-30-day real spend (if known)."""
        if self.spend is None or self.spend.total_usd <= 0:
            return None
        return round(self.total_monthly_savings / self.spend.total_usd * 100, 1)


def run_audit(region: str = "us-east-1", synthetic: bool = False) -> AuditResult:
    """Run the audit and return a ranked :class:`AuditResult`."""
    if synthetic:
        from costdetective.sample_data.synthetic import (
            generate_anomalies,
            generate_findings,
            generate_spend,
        )

        findings = generate_findings(region=region)
        return AuditResult(
            region=region,
            synthetic=True,
            findings=rank_findings(findings),
            spend=generate_spend(),
            anomalies=generate_anomalies(),
        )

    findings, ce_result = _run_real_audit(region=region)
    return AuditResult(
        region=region,
        synthetic=False,
        findings=rank_findings(findings),
        spend=ce_result.spend,
        anomalies=ce_result.anomalies,
        spend_error=ce_result.error,
    )


def _run_real_audit(region: str) -> tuple[list[Finding], CostExplorerResult]:
    """Run real-mode: bind live pricing, run detectors, fetch Cost Explorer."""
    import boto3

    session = boto3.Session()
    _bind_live_pricing(session)
    try:
        findings = _run_real_detectors(session, region=region)
        ce_result = gather_cost_explorer_data(session)
    finally:
        pricing.bind_live_pricer(None)
    return findings, ce_result


def _bind_live_pricing(session) -> None:
    """Best-effort: wire the live Pricing API. Stays silent on failure."""
    try:
        from costdetective.pricing_live import LivePricingClient

        pricing.bind_live_pricer(LivePricingClient(session))
        log.info("live Pricing API client bound")
    except Exception as exc:  # noqa: BLE001 - fall back to the hardcoded map
        log.warning("could not bind live Pricing API client: %s — using fallback", exc)
        pricing.bind_live_pricer(None)


def _run_real_detectors(session, region: str) -> list[Finding]:
    """Run every registered read-only AWS detector in the given region.

    Each detector is isolated: if one raises (bad permissions, throttling, an
    AWS hiccup) we log a warning and keep going, so a single broken detector
    never aborts the whole audit.
    """
    findings: list[Finding] = []
    for detector in DETECTORS:
        name = getattr(detector, "NAME", detector.__name__)
        try:
            found = detector.detect(session, region)
            log.info("detector %s: %d finding(s)", name, len(found))
            findings.extend(found)
        except Exception as exc:  # noqa: BLE001 - isolate each detector
            log.warning("detector %s failed: %s", name, exc)
    return findings
