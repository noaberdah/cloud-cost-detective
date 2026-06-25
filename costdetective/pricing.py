"""Deterministic cost math with a hardcoded fallback price map.

Stage 4 replaces these tables with live AWS Pricing API lookups. Until then we
use representative **us-east-1 on-demand** prices so the dollar math is plausible
and fully offline. All arithmetic lives here in plain Python — never in the API.

Prices are approximate (USD) and will drift; they are good enough for ranking
waste by impact, which is the point.
"""

from __future__ import annotations

HOURS_PER_MONTH = 730  # AWS billing convention

# EBS storage, USD per GB-month (us-east-1).
EBS_GB_MONTH: dict[str, float] = {
    "gp3": 0.08,
    "gp2": 0.10,
    "io1": 0.125,
    "io2": 0.125,
    "st1": 0.045,
    "sc1": 0.015,
    "standard": 0.05,  # magnetic
}
_EBS_DEFAULT_GB_MONTH = 0.10

# EBS snapshot storage, USD per GB-month (us-east-1).
EBS_SNAPSHOT_GB_MONTH = 0.05

# EC2 on-demand compute, USD per hour (us-east-1, Linux). Common types only;
# unknown types fall back to None so callers can flag the cost as unverified.
EC2_HOURLY: dict[str, float] = {
    "t2.nano": 0.0058, "t2.micro": 0.0116, "t2.small": 0.023,
    "t2.medium": 0.0464, "t2.large": 0.0928, "t2.xlarge": 0.1856,
    "t3.nano": 0.0052, "t3.micro": 0.0104, "t3.small": 0.0208,
    "t3.medium": 0.0416, "t3.large": 0.0832, "t3.xlarge": 0.1664,
    "t3.2xlarge": 0.3328,
    "t3a.micro": 0.0094, "t3a.small": 0.0188, "t3a.medium": 0.0376,
    "t3a.large": 0.0752, "t3a.xlarge": 0.1504,
    "m5.large": 0.096, "m5.xlarge": 0.192, "m5.2xlarge": 0.384,
    "m5.4xlarge": 0.768, "m5.8xlarge": 1.536,
    "m6i.large": 0.096, "m6i.xlarge": 0.192, "m6i.2xlarge": 0.384,
    "c5.large": 0.085, "c5.xlarge": 0.17, "c5.2xlarge": 0.34,
    "c5.4xlarge": 0.68,
    "c6i.large": 0.085, "c6i.xlarge": 0.17, "c6i.2xlarge": 0.34,
    "r5.large": 0.126, "r5.xlarge": 0.252, "r5.2xlarge": 0.504,
    "r6i.large": 0.126, "r6i.xlarge": 0.252,
}


def ebs_monthly_cost(volume_type: str, size_gb: float) -> float:
    """Monthly cost of an EBS volume from its type and size."""
    rate = EBS_GB_MONTH.get(volume_type, _EBS_DEFAULT_GB_MONTH)
    return round(rate * size_gb, 2)


def ebs_snapshot_monthly_cost(size_gb: float) -> float:
    """Monthly cost of an EBS snapshot's stored data."""
    return round(EBS_SNAPSHOT_GB_MONTH * size_gb, 2)


def ec2_monthly_cost(instance_type: str) -> float | None:
    """Monthly on-demand cost of an EC2 instance type, or None if unknown."""
    hourly = EC2_HOURLY.get(instance_type)
    if hourly is None:
        return None
    return round(hourly * HOURS_PER_MONTH, 2)
