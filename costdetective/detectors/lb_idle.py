"""Detector: Application/Network/Gateway load balancers that look idle.

An ELBv2 with zero healthy targets across all of its target groups, or with
zero requests over the lookback window, is almost certainly forgotten. The
hourly LCU+base charge runs whether anyone uses it or not. Classic ELB
(``elb`` client) is intentionally skipped — modern accounts shouldn't have any.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from costdetective.awsclients import client
from costdetective.models import Finding, severity_from_savings
from costdetective.pricing import lb_monthly_cost

NAME = "lb_idle"

LOOKBACK_DAYS = 14
PERIOD_SECONDS = 86400  # daily datapoints are enough for a "any traffic at all?" check

# (lb type, CloudWatch namespace, request-count metric name).
_TRAFFIC_METRIC = {
    "application": ("AWS/ApplicationELB", "RequestCount"),
    "network": ("AWS/NetworkELB", "ActiveFlowCount"),
    "gateway": ("AWS/GatewayELB", "ActiveFlowCount"),
}


def _tags_to_dict(tag_list) -> dict:
    return {t["Key"]: t["Value"] for t in (tag_list or [])}


def _arn_suffix(arn: str) -> str:
    """CloudWatch's LoadBalancer dimension wants ``app/name/id``, not the full ARN."""
    marker = ":loadbalancer/"
    idx = arn.find(marker)
    return arn[idx + len(marker):] if idx >= 0 else arn


def _healthy_target_count(elbv2, target_group_arns: list[str]) -> int:
    total = 0
    for tg_arn in target_group_arns:
        health = elbv2.describe_target_health(TargetGroupArn=tg_arn).get(
            "TargetHealthDescriptions", []
        )
        total += sum(
            1
            for h in health
            if h.get("TargetHealth", {}).get("State") == "healthy"
        )
    return total


def _traffic_sum(cw, lb_type: str, lb_dim: str, start, end) -> float | None:
    spec = _TRAFFIC_METRIC.get(lb_type)
    if spec is None:
        return None
    namespace, metric = spec
    stats = cw.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric,
        Dimensions=[{"Name": "LoadBalancer", "Value": lb_dim}],
        StartTime=start,
        EndTime=end,
        Period=PERIOD_SECONDS,
        Statistics=["Sum"],
    )
    points = stats.get("Datapoints", [])
    return sum(p["Sum"] for p in points) if points else 0.0


def detect(session, region: str) -> list[Finding]:
    elbv2 = client(session, "elbv2", region)
    cw = client(session, "cloudwatch", region)
    findings: list[Finding] = []

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)

    for page in elbv2.get_paginator("describe_load_balancers").paginate():
        for lb in page.get("LoadBalancers", []):
            arn = lb["LoadBalancerArn"]
            lb_type = lb.get("Type", "application")
            name = lb.get("LoadBalancerName", arn)

            tgs = elbv2.describe_target_groups(LoadBalancerArn=arn).get(
                "TargetGroups", []
            )
            tg_arns = [tg["TargetGroupArn"] for tg in tgs]
            healthy = _healthy_target_count(elbv2, tg_arns) if tg_arns else 0

            traffic = _traffic_sum(cw, lb_type, _arn_suffix(arn), start, end)
            if traffic is None:
                continue  # unsupported LB type — skip rather than guess

            if healthy > 0 and traffic > 0:
                continue  # in use

            tag_resp = elbv2.describe_tags(ResourceArns=[arn]).get("TagDescriptions", [])
            tags = _tags_to_dict(tag_resp[0].get("Tags") if tag_resp else None)

            monthly = lb_monthly_cost(lb_type)
            reason_bits = []
            if healthy == 0:
                reason_bits.append("zero healthy targets")
            if traffic == 0:
                reason_bits.append(f"no traffic in {LOOKBACK_DAYS} days")
            reason = " and ".join(reason_bits) or "no useful activity"

            findings.append(
                Finding(
                    detector=NAME,
                    resource_id=_arn_suffix(arn),
                    resource_type=f"{lb_type.capitalize()} Load Balancer",
                    region=region,
                    severity=severity_from_savings(monthly),
                    monthly_savings=monthly,
                    summary=f"{lb_type.upper()} '{name}' with {reason}",
                    recommendation="Delete the load balancer and its empty target groups.",
                    confidence=0.9 if healthy == 0 and traffic == 0 else 0.7,
                    effort="low",
                    details={
                        "lb_type": lb_type,
                        "healthy_targets": healthy,
                        "target_group_count": len(tg_arns),
                        "traffic_metric_sum_lookback": traffic,
                        "lookback_days": LOOKBACK_DAYS,
                        "tags": tags,
                    },
                )
            )
    return findings
