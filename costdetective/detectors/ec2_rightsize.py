"""Detector: running EC2 instances that look oversized by CPU.

Sister of ``ec2_idle``: that one flags boxes doing essentially nothing (peak
< 10%). This one flags boxes that are *used* but where peak CPU is comfortably
under what the current size offers — a candidate to drop one step (e.g.
m5.xlarge -> m5.large) and halve the bill.

Memory pressure is intentionally NOT checked: standard CloudWatch has no
memory metric for EC2 (needs the CloudWatch agent). We flag that limitation in
each finding so the human reviewer knows to verify before resizing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from costdetective.awsclients import client
from costdetective.models import Finding, severity_from_savings
from costdetective.pricing import ec2_monthly_cost, next_smaller_ec2_type

NAME = "ec2_rightsize"

LOOKBACK_DAYS = 14
PERIOD_SECONDS = 3600
# A safe rightsizing window: clearly idle (< IDLE_PEAK) is handled by ec2_idle;
# here we want "used but not stressed" — peak CPU comfortably under capacity.
IDLE_PEAK_FLOOR = 10.0
RIGHTSIZE_PEAK_CEILING = 40.0
MIN_DATAPOINTS = 72


def _tags_to_dict(tag_list) -> dict:
    return {t["Key"]: t["Value"] for t in (tag_list or [])}


def detect(session, region: str) -> list[Finding]:
    ec2 = client(session, "ec2", region)
    cw = client(session, "cloudwatch", region)
    findings: list[Finding] = []

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)

    pages = ec2.get_paginator("describe_instances").paginate(
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

    smaller = next_smaller_ec2_type(instance_type)
    if smaller is None:
        return None  # already smallest in its family, or unknown family

    current_cost = ec2_monthly_cost(instance_type)
    target_cost = ec2_monthly_cost(smaller)
    if current_cost is None or target_cost is None:
        return None  # no pricing -> can't make a credible recommendation

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
        return None

    peak = max(p["Maximum"] for p in points)
    avg = sum(p["Average"] for p in points) / len(points)

    # Skip pure-idle boxes — ec2_idle owns those — and any box that actually
    # used meaningful CPU at some point.
    if peak < IDLE_PEAK_FLOOR or peak >= RIGHTSIZE_PEAK_CEILING:
        return None

    monthly = round(current_cost - target_cost, 2)
    if monthly <= 0:
        return None

    name = tags.get("Name", instance_id)
    return Finding(
        detector=NAME,
        resource_id=instance_id,
        resource_type="EC2 instance",
        region=region,
        severity=severity_from_savings(monthly),
        monthly_savings=monthly,
        summary=(
            f"{instance_type} '{name}' peak CPU only {peak:.1f}% over {LOOKBACK_DAYS} days "
            f"— could likely run on {smaller}"
        ),
        recommendation=(
            f"Resize from {instance_type} to {smaller}. "
            "Memory usage is NOT verified (standard CloudWatch has no memory metric); "
            "check the CloudWatch agent or app metrics before applying."
        ),
        confidence=0.7,
        effort="medium",
        details={
            "current_type": instance_type,
            "recommended_type": smaller,
            "current_monthly_cost": current_cost,
            "target_monthly_cost": target_cost,
            "peak_cpu_pct": round(peak, 2),
            "avg_cpu_pct": round(avg, 2),
            "lookback_days": LOOKBACK_DAYS,
            "memory_verified": False,
            "tags": tags,
        },
    )
