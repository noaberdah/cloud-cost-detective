"""Detector: old self-owned EBS snapshots whose source volume no longer exists.

A snapshot keeps billing for the GB it stores long after the volume it came from
is gone. We only flag snapshots that are (a) owned by this account, (b) older
than the age threshold, and (c) whose ``VolumeId`` is missing from the account.
Snapshots tagged for backup/compliance are kept but down-confidence so a human
reviews before deleting.
"""

from __future__ import annotations

from datetime import datetime, timezone

from costdetective.awsclients import client
from costdetective.models import Finding, severity_from_savings
from costdetective.pricing import ebs_snapshot_monthly_cost

NAME = "snapshots_orphaned"

MIN_AGE_DAYS = 90  # ignore recent snapshots — they may be a backup-in-progress
BACKUP_TAG_KEYS = {"backup", "Backup", "BackupPolicy", "retain", "Retain"}


def _tags_to_dict(tag_list) -> dict:
    return {t["Key"]: t["Value"] for t in (tag_list or [])}


def _live_volume_ids(ec2) -> set[str]:
    """Every VolumeId currently in the account, paginated."""
    ids: set[str] = set()
    for page in ec2.get_paginator("describe_volumes").paginate():
        for vol in page.get("Volumes", []):
            ids.add(vol["VolumeId"])
    return ids


def detect(session, region: str) -> list[Finding]:
    ec2 = client(session, "ec2", region)
    findings: list[Finding] = []
    now = datetime.now(timezone.utc)

    live_volumes = _live_volume_ids(ec2)

    pages = ec2.get_paginator("describe_snapshots").paginate(OwnerIds=["self"])
    for page in pages:
        for snap in page.get("Snapshots", []):
            source_vol = snap.get("VolumeId")
            if source_vol and source_vol in live_volumes:
                continue  # source volume still exists

            start_time = snap.get("StartTime")
            age_days = (now - start_time).days if start_time else None
            if age_days is not None and age_days < MIN_AGE_DAYS:
                continue

            size_gb = snap.get("VolumeSize", 0)
            monthly = ebs_snapshot_monthly_cost(size_gb)
            tags = _tags_to_dict(snap.get("Tags"))
            looks_like_backup = bool(BACKUP_TAG_KEYS & set(tags))

            confidence = 0.6 if looks_like_backup else 0.85
            age_note = f"{age_days} days ago" if age_days is not None else "long ago"

            findings.append(
                Finding(
                    detector=NAME,
                    resource_id=snap["SnapshotId"],
                    resource_type="EBS snapshot",
                    region=region,
                    severity=severity_from_savings(monthly),
                    monthly_savings=monthly,
                    summary=(
                        f"{size_gb} GB snapshot whose source volume {source_vol or '(unknown)'} "
                        f"no longer exists; taken {age_note}"
                    ),
                    recommendation=(
                        "Verify it isn't a compliance backup, then delete the snapshot."
                    ),
                    confidence=confidence,
                    effort="low",
                    details={
                        "size_gb": size_gb,
                        "age_days": age_days,
                        "source_volume_id": source_vol,
                        "source_volume_exists": False,
                        "looks_like_backup": looks_like_backup,
                        "tags": tags,
                    },
                )
            )
    return findings
