"""Core data types shared across detectors, scanning, and reporting.

These are deterministic, plain-Python structures. The Anthropic API layer
(Stage 5) reasons *over* `Finding`s but never invents their factual fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum


class Severity(str, Enum):
    """How urgent a finding is. Inherits from ``str`` so it serializes cleanly."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        """Numeric weight for sorting (higher = more severe)."""
        return _SEVERITY_RANK[self]


_SEVERITY_RANK = {
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


@dataclass
class Finding:
    """A single piece of detected waste, with its dollar impact.

    Factual fields (resource, region, cost) come from deterministic Python.
    ``confidence`` and the written reasoning may later be adjusted by the agent
    layer, but the math is never delegated to an LLM.
    """

    detector: str               # which check produced this (e.g. "ebs_unattached")
    resource_id: str            # the AWS resource identifier
    resource_type: str          # human label, e.g. "EBS volume", "EC2 instance"
    region: str
    severity: Severity
    monthly_savings: float      # estimated USD/month recoverable if remediated
    summary: str                # one line: what's wrong
    recommendation: str         # one line: what to do about it
    confidence: float = 1.0     # 0..1; the agent layer may lower this later
    effort: str = "low"         # rough remediation effort: low | medium | high
    details: dict = field(default_factory=dict)  # extra facts (size, tags, metrics)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


def severity_from_savings(monthly_savings: float) -> Severity:
    """Map a dollar impact to a severity band (deterministic, no judgment)."""
    if monthly_savings >= 500:
        return Severity.CRITICAL
    if monthly_savings >= 100:
        return Severity.HIGH
    if monthly_savings >= 25:
        return Severity.MEDIUM
    return Severity.LOW


def rank_findings(findings: list[Finding]) -> list[Finding]:
    """Order findings the way a human triages: biggest, most severe waste first."""
    return sorted(
        findings,
        key=lambda f: (f.monthly_savings, f.severity.rank, f.confidence),
        reverse=True,
    )


# Tag keys to look at for owner/team grouping, in priority order. The first
# one that produces a non-empty value wins for a given finding.
DEFAULT_OWNER_TAG_KEYS = ("Owner", "owner", "Team", "team", "Project", "project")
UNTAGGED_BUCKET = "(untagged)"


def group_findings_by_owner(
    findings: list[Finding],
    tag_keys: tuple[str, ...] = DEFAULT_OWNER_TAG_KEYS,
) -> dict[str, list[Finding]]:
    """Group findings by the first matching owner/team/project tag.

    Findings whose ``details.tags`` is empty or holds none of the requested
    keys land in ``UNTAGGED_BUCKET`` — usually the largest and most actionable
    group, because untagged spend has no one to ask.
    """
    groups: dict[str, list[Finding]] = {}
    for f in findings:
        tags = f.details.get("tags") if isinstance(f.details, dict) else None
        owner = _first_tag_value(tags, tag_keys) if tags else None
        bucket = owner or UNTAGGED_BUCKET
        groups.setdefault(bucket, []).append(f)
    return groups


def _first_tag_value(tags: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = tags.get(key)
        if value:
            return str(value)
    return None
