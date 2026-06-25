"""Detector: running EC2 instances that look idle by CPU.

We pull 14 days of CloudWatch ``CPUUtilization`` and flag instances whose *peak*
CPU never crosses a low threshold — a strong signal the box is doing nothing.
CPU alone can't see memory/network/disk pressure, so this is a candidate, not a
verdict; the agent layer (Stage 5) refines confidence. All read-only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from costdetective.models import Finding, severity_from_savings
from costdetective.pricing import ec2_monthly_cost

NAME = "ec2_idle"

LOOKBACK_DAYS = 14
PERIOD_SECONDS = 3600                # one datapoint per hour
IDLE_PEAK_CPU_PCT = 10.0            # flag if peak CPU stays below this
MIN_DATAPOINTS = 72                  # need ~3 days of data to trust the signal


def _tags_to_dict(tag_list) -> dict:
    return {t["Key"]: t["Value"] for t in (tag_list or [])}


def detect(session, region: str) -> list[Finding]:
    ec2 = session.client("ec2", region_name=region)
    cw = session.client("cloudwatch", region_name=region)
    findings: list[Finding] = []

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)

    paginator = ec2.get_paginator("describe_instances")
    pages = paginator.paginate(
        Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
    )
    for page in pages:
        for reservation in page.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                finding = _evaluate_instance(cw, inst, region, start, end)
                if finding is not None:
                    findings.append(finding)
    return findings


def _evaluate_instance(cw, inst, region, start, end) -> Finding | None:
    instance_id = inst["InstanceId"]
    instance_type = inst.get("InstanceType", "unknown")
    tags = _tags_to_dict(inst.get("Tags"))

    stats = cw.get_metric_statistics(
        Namespace="AWS/EC2",
        MetricName="CPUUtilization",
        Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
        StartTime=start,
        EndTime=end,
        Period=PERIOD_SECONDS,
        Statistics=["Maximum", "Average"],
    )
    points = stats.get("Datapoints", [])
    if len(points) < MIN_DATAPOINTS:
        return None  # too new / not enough data to judge

    peak = max(p["Maximum"] for p in points)
    avg = sum(p["Average"] for p in points) / len(points)
    if peak >= IDLE_PEAK_CPU_PCT:
        return None  # the instance gets real use

    monthly = ec2_monthly_cost(instance_type)
    price_known = monthly is not None
    monthly = monthly if price_known else 0.0

    name = tags.get("Name", instance_id)
    summary = (
        f"{instance_type} '{name}' running 24/7 with {peak:.1f}% peak CPU "
        f"over the last {LOOKBACK_DAYS} days"
    )
    if not price_known:
        summary += " (price for this type not in fallback map)"

    return Finding(
        detector=NAME,
        resource_id=instance_id,
        resource_type="EC2 instance",
        region=region,
        severity=severity_from_savings(monthly),
        monthly_savings=monthly,
        summary=summary,
        recommendation="Stop or downsize the instance; nothing meaningful is running on it.",
        confidence=0.8,  # CPU-only signal; agent layer may adjust
        effort="medium",
        details={
            "instance_type": instance_type,
            "peak_cpu_pct": round(peak, 2),
            "avg_cpu_pct": round(avg, 2),
            "lookback_days": LOOKBACK_DAYS,
            "datapoints": len(points),
            "price_known": price_known,
            "tags": tags,
        },
    )
