"""Orchestrates a single audit run: gather findings, rank them, summarize.

In Stage 1 this only knows how to run synthetic mode. Stage 2 wires the real
read-only AWS detectors into :func:`_run_real_detectors`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

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
from costdetective.models import Finding, rank_findings

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
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def total_monthly_savings(self) -> float:
        return round(sum(f.monthly_savings for f in self.findings), 2)

    @property
    def total_annual_savings(self) -> float:
        return round(self.total_monthly_savings * 12, 2)


def run_audit(region: str = "us-east-1", synthetic: bool = False) -> AuditResult:
    """Run the audit and return a ranked :class:`AuditResult`."""
    if synthetic:
        from costdetective.sample_data.synthetic import generate_findings

        findings = generate_findings(region=region)
    else:
        findings = _run_real_detectors(region=region)

    return AuditResult(
        region=region,
        synthetic=synthetic,
        findings=rank_findings(findings),
    )


def _run_real_detectors(region: str) -> list[Finding]:
    """Run every registered read-only AWS detector in the given region.

    Each detector is isolated: if one raises (bad permissions, throttling, an
    AWS hiccup) we log a warning and keep going, so a single broken detector
    never aborts the whole audit.
    """
    import boto3

    session = boto3.Session()
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
