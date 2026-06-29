"""Detector: RDS instances whose CPU stays low — candidates for a smaller class.

Mirrors ``ec2_idle`` but for RDS: 14 days of CloudWatch ``CPUUtilization``,
flag anything whose peak never crosses the threshold. RDS rightsizing is
trickier than EC2 (storage IOPS, memory pressure, connection limits) so we
keep confidence modest and recommend "one step down" rather than a specific
target class — the agent layer can refine in Stage 5.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from costdetective.awsclients import client
from costdetective.models import Finding, severity_from_savings
from costdetective.pricing import rds_monthly_cost

NAME = "rds_overprovisioned"

LOOKBACK_DAYS = 14
PERIOD_SECONDS = 3600
LOW_PEAK_CPU_PCT = 20.0   # peak below this -> oversized
MIN_DATAPOINTS = 72        # ~3 days of hourly data minimum


def detect(session, region: str) -> list[Finding]:
    rds = client(session, "rds", region)
    cw = client(session, "cloudwatch", region)
    findings: list[Finding] = []

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)

    for page in rds.get_paginator("describe_db_instances").paginate():
        for db in page.get("DBInstances", []):
            if db.get("DBInstanceStatus") != "available":
                continue
            finding = _evaluate_db(cw, db, region, start, end)
            if finding is not None:
                findings.append(finding)
    return findings


def _evaluate_db(cw, db, region, start, end) -> Finding | None:
    db_id = db["DBInstanceIdentifier"]
    db_class = db.get("DBInstanceClass", "unknown")
    engine = db.get("Engine", "unknown")
    multi_az = bool(db.get("MultiAZ"))

    stats = cw.get_metric_statistics(
        Namespace="AWS/RDS",
        MetricName="CPUUtilization",
        Dimensions=[{"Name": "DBInstanceIdentifier", "Value": db_id}],
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
    if peak >= LOW_PEAK_CPU_PCT:
        return None

    full = rds_monthly_cost(db_class, engine, multi_az=multi_az, region=region)
    # Rough estimate: a one-size-down move saves ~half the per-class cost.
    monthly = round(full * 0.5, 2) if full else 0.0

    summary = (
        f"RDS '{db_id}' ({db_class}, {engine}) peak CPU {peak:.1f}% "
        f"over {LOOKBACK_DAYS} days — likely oversized"
    )
    if not full:
        summary += " (price for this class not in fallback map)"

    return Finding(
        detector=NAME,
        resource_id=db_id,
        resource_type="RDS instance",
        region=region,
        severity=severity_from_savings(monthly),
        monthly_savings=monthly,
        summary=summary,
        recommendation=(
            "Consider scaling down one class (e.g. .large -> .medium). "
            "Verify memory/IOPS headroom before applying."
        ),
        confidence=0.55,  # CPU alone for a DB is a weak signal
        effort="medium",
        details={
            "db_class": db_class,
            "engine": engine,
            "multi_az": multi_az,
            "peak_cpu_pct": round(peak, 2),
            "avg_cpu_pct": round(avg, 2),
            "lookback_days": LOOKBACK_DAYS,
            "datapoints": len(points),
            "price_known": full is not None,
            "full_class_monthly_cost": full,
        },
    )
