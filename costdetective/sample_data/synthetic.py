"""Fake-but-realistic findings for ``--synthetic`` mode.

This lets us watch the whole detect -> rank -> report pipeline run without
touching AWS, and guarantees there's always something to screenshot. The
numbers mirror real us-east-1 pricing closely enough to be believable.
"""

from __future__ import annotations

from costdetective.models import Finding, Severity


def generate_findings(region: str = "us-east-1") -> list[Finding]:
    """Return a handful of representative waste findings.

    Covers a spread of detectors, severities, costs, and confidences so the
    report and ranking logic get exercised properly.
    """
    return [
        Finding(
            detector="ec2_idle",
            resource_id="i-0a1b2c3d4e5f60718",
            resource_type="EC2 instance",
            region=region,
            severity=Severity.HIGH,
            monthly_savings=139.97,
            summary="m5.xlarge running 24/7 with 2.1% peak CPU over the last 14 days",
            recommendation="Stop or downsize; nothing is using this instance.",
            confidence=0.9,
            effort="medium",
            details={
                "instance_type": "m5.xlarge",
                "peak_cpu_pct_14d": 2.1,
                "avg_cpu_pct_14d": 0.8,
                "tags": {"Name": "old-staging-box", "owner": "platform"},
            },
        ),
        Finding(
            detector="ebs_unattached",
            resource_id="vol-0fedcba9876543210",
            resource_type="EBS volume",
            region=region,
            severity=Severity.MEDIUM,
            monthly_savings=40.00,
            summary="500 GB gp3 volume in 'available' state, detached for 38 days",
            recommendation="Snapshot if needed, then delete the volume.",
            confidence=0.95,
            effort="low",
            details={
                "size_gb": 500,
                "volume_type": "gp3",
                "state": "available",
                "detached_days": 38,
                "tags": {"owner": "data-eng"},
            },
        ),
        Finding(
            detector="lb_idle",
            resource_id="app/legacy-api-alb/50dc6c495c0c9188",
            resource_type="Application Load Balancer",
            region=region,
            severity=Severity.MEDIUM,
            monthly_savings=18.30,
            summary="ALB with zero healthy targets and no requests in 30 days",
            recommendation="Delete the load balancer and its empty target group.",
            confidence=0.85,
            effort="low",
            details={
                "request_count_30d": 0,
                "healthy_targets": 0,
                "tags": {"owner": "payments"},
            },
        ),
        Finding(
            detector="snapshots_orphaned",
            resource_id="snap-0123456789abcdef0",
            resource_type="EBS snapshot",
            region=region,
            severity=Severity.LOW,
            monthly_savings=12.45,
            summary="249 GB snapshot whose source volume was deleted 11 months ago",
            recommendation="Verify it isn't a compliance backup, then delete.",
            confidence=0.6,
            effort="low",
            details={
                "size_gb": 249,
                "age_days": 337,
                "source_volume_exists": False,
                "tags": {"backup": "true"},
            },
        ),
        Finding(
            detector="eip_unused",
            resource_id="eipalloc-08e9f0a1b2c3d4e5f",
            resource_type="Elastic IP",
            region=region,
            severity=Severity.LOW,
            monthly_savings=3.65,
            summary="Allocated Elastic IP not associated with any instance",
            recommendation="Release the address to stop the idle-IP charge.",
            confidence=0.98,
            effort="low",
            details={
                "public_ip": "54.210.18.42",
                "association_id": None,
                "tags": {},
            },
        ),
        Finding(
            detector="ebs_gp2_to_gp3",
            resource_id="vol-0aa11bb22cc33dd44",
            resource_type="EBS volume",
            region=region,
            severity=Severity.LOW,
            monthly_savings=8.00,
            summary="200 GB gp2 volume eligible for gp3 at the same performance",
            recommendation="Migrate gp2 -> gp3 for ~20% lower per-GB cost.",
            confidence=0.99,
            effort="low",
            details={
                "size_gb": 200,
                "current_type": "gp2",
                "target_type": "gp3",
                "tags": {"owner": "web"},
            },
        ),
    ]
