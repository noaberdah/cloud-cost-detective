"""Detector: EBS volumes sitting in the 'available' (unattached) state.

An unattached volume bills for its full provisioned size every month while doing
nothing. This is deterministic — we list the volumes and price them in Python.
Read-only: a single ``DescribeVolumes`` call, no mutations.
"""

from __future__ import annotations

from datetime import datetime, timezone

from costdetective.awsclients import client
from costdetective.models import Finding, severity_from_savings
from costdetective.pricing import ebs_monthly_cost

NAME = "ebs_unattached"


def _tags_to_dict(tag_list) -> dict:
    return {t["Key"]: t["Value"] for t in (tag_list or [])}


def detect(session, region: str) -> list[Finding]:
    ec2 = client(session, "ec2", region)
    findings: list[Finding] = []

    paginator = ec2.get_paginator("describe_volumes")
    pages = paginator.paginate(Filters=[{"Name": "status", "Values": ["available"]}])
    for page in pages:
        for vol in page.get("Volumes", []):
            size_gb = vol["Size"]
            vol_type = vol.get("VolumeType", "gp2")
            monthly = ebs_monthly_cost(vol_type, size_gb)
            tags = _tags_to_dict(vol.get("Tags"))

            age_days = _age_days(vol.get("CreateTime"))
            age_note = f", created {age_days} days ago" if age_days is not None else ""

            findings.append(
                Finding(
                    detector=NAME,
                    resource_id=vol["VolumeId"],
                    resource_type="EBS volume",
                    region=region,
                    severity=severity_from_savings(monthly),
                    monthly_savings=monthly,
                    summary=(
                        f"{size_gb} GB {vol_type} volume in 'available' state "
                        f"(unattached){age_note}"
                    ),
                    recommendation="Snapshot if the data may be needed, then delete the volume.",
                    confidence=0.95,
                    effort="low",
                    details={
                        "size_gb": size_gb,
                        "volume_type": vol_type,
                        "state": vol.get("State"),
                        "availability_zone": vol.get("AvailabilityZone"),
                        "age_days": age_days,
                        "tags": tags,
                    },
                )
            )
    return findings


def _age_days(create_time) -> int | None:
    if not create_time:
        return None
    now = datetime.now(timezone.utc)
    return (now - create_time).days
