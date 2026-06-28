"""Detector: attached gp2 volumes that could move to gp3 at lower cost.

gp3 is ~20% cheaper per GB-month than gp2 and meets or exceeds gp2 performance
for most workloads. The migration is in-place and non-disruptive. We only
flag attached volumes — detached gp2s belong to the ``ebs_unattached`` finding.
"""

from __future__ import annotations

from costdetective.awsclients import client
from costdetective.models import Finding, severity_from_savings
from costdetective.pricing import ebs_monthly_cost

NAME = "ebs_gp2_to_gp3"


def _tags_to_dict(tag_list) -> dict:
    return {t["Key"]: t["Value"] for t in (tag_list or [])}


def detect(session, region: str) -> list[Finding]:
    ec2 = client(session, "ec2", region)
    findings: list[Finding] = []

    paginator = ec2.get_paginator("describe_volumes")
    pages = paginator.paginate(
        Filters=[
            {"Name": "volume-type", "Values": ["gp2"]},
            {"Name": "status", "Values": ["in-use"]},
        ]
    )
    for page in pages:
        for vol in page.get("Volumes", []):
            size_gb = vol["Size"]
            current_cost = ebs_monthly_cost("gp2", size_gb)
            new_cost = ebs_monthly_cost("gp3", size_gb)
            monthly = round(current_cost - new_cost, 2)
            if monthly <= 0:
                continue

            attachments = vol.get("Attachments", [])
            attached_to = attachments[0]["InstanceId"] if attachments else None
            tags = _tags_to_dict(vol.get("Tags"))

            findings.append(
                Finding(
                    detector=NAME,
                    resource_id=vol["VolumeId"],
                    resource_type="EBS volume",
                    region=region,
                    severity=severity_from_savings(monthly),
                    monthly_savings=monthly,
                    summary=(
                        f"{size_gb} GB gp2 volume eligible for in-place gp3 migration "
                        f"(~20% lower per-GB cost, same or better performance)"
                    ),
                    recommendation="Modify the volume type from gp2 to gp3; no downtime required.",
                    confidence=0.99,
                    effort="low",
                    details={
                        "size_gb": size_gb,
                        "current_type": "gp2",
                        "target_type": "gp3",
                        "current_monthly_cost": current_cost,
                        "target_monthly_cost": new_cost,
                        "attached_to": attached_to,
                        "tags": tags,
                    },
                )
            )
    return findings
