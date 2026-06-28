"""Unit tests for the Stage 3 detectors.

Each detector is exercised against a hand-built fake AWS response, so the
deterministic detection + cost math runs end-to-end without network access.
Run with: ``python -m unittest discover -s tests``.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from costdetective.detectors import (
    ebs_gp2_to_gp3,
    ebs_unattached,
    ec2_idle,
    ec2_rightsize,
    eip_unused,
    lb_idle,
    rds_overprovisioned,
    snapshots_orphaned,
)
from costdetective.pricing import next_smaller_ec2_type

from tests.fakes import FakeClient, FakeSession, cpu_datapoints


REGION = "us-east-1"


class TestEbsUnattached(unittest.TestCase):
    def test_flags_available_volumes(self):
        ec2 = FakeClient(
            paginators={
                "describe_volumes": [
                    {
                        "Volumes": [
                            {
                                "VolumeId": "vol-aaa",
                                "Size": 100,
                                "VolumeType": "gp3",
                                "State": "available",
                                "AvailabilityZone": "us-east-1a",
                                "CreateTime": datetime.now(timezone.utc) - timedelta(days=42),
                                "Tags": [{"Key": "env", "Value": "stage"}],
                            }
                        ]
                    }
                ]
            }
        )
        session = FakeSession(ec2=ec2)
        findings = ebs_unattached.detect(session, REGION)

        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f.detector, "ebs_unattached")
        self.assertEqual(f.resource_id, "vol-aaa")
        self.assertGreater(f.monthly_savings, 0)
        self.assertEqual(f.details["size_gb"], 100)


class TestEbsGp2ToGp3(unittest.TestCase):
    def test_flags_attached_gp2_with_savings(self):
        ec2 = FakeClient(
            paginators={
                "describe_volumes": [
                    {
                        "Volumes": [
                            {
                                "VolumeId": "vol-gp2-1",
                                "Size": 500,
                                "VolumeType": "gp2",
                                "Attachments": [{"InstanceId": "i-abc"}],
                                "Tags": [],
                            }
                        ]
                    }
                ]
            }
        )
        session = FakeSession(ec2=ec2)
        findings = ebs_gp2_to_gp3.detect(session, REGION)

        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f.detector, "ebs_gp2_to_gp3")
        self.assertEqual(f.details["current_type"], "gp2")
        self.assertEqual(f.details["target_type"], "gp3")
        self.assertEqual(f.details["attached_to"], "i-abc")
        # 500 GB * ($0.10 - $0.08) = $10/month
        self.assertAlmostEqual(f.monthly_savings, 10.00, places=2)


class TestSnapshotsOrphaned(unittest.TestCase):
    def test_flags_old_snapshot_with_dead_source(self):
        now = datetime.now(timezone.utc)
        ec2 = FakeClient(
            paginators={
                "describe_volumes": [{"Volumes": [{"VolumeId": "vol-still-alive"}]}],
                "describe_snapshots": [
                    {
                        "Snapshots": [
                            {
                                "SnapshotId": "snap-old",
                                "VolumeId": "vol-deleted-long-ago",
                                "VolumeSize": 200,
                                "StartTime": now - timedelta(days=400),
                                "Tags": [],
                            },
                            {
                                "SnapshotId": "snap-recent",
                                "VolumeId": "vol-also-deleted",
                                "VolumeSize": 50,
                                "StartTime": now - timedelta(days=10),
                                "Tags": [],
                            },
                            {
                                "SnapshotId": "snap-live",
                                "VolumeId": "vol-still-alive",
                                "VolumeSize": 50,
                                "StartTime": now - timedelta(days=400),
                                "Tags": [],
                            },
                        ]
                    }
                ],
            }
        )
        session = FakeSession(ec2=ec2)
        findings = snapshots_orphaned.detect(session, REGION)

        ids = {f.resource_id for f in findings}
        self.assertEqual(ids, {"snap-old"})  # recent + live-source ones are filtered out
        f = findings[0]
        self.assertFalse(f.details["source_volume_exists"])
        self.assertGreaterEqual(f.details["age_days"], 90)

    def test_backup_tag_lowers_confidence(self):
        now = datetime.now(timezone.utc)
        ec2 = FakeClient(
            paginators={
                "describe_volumes": [{"Volumes": []}],
                "describe_snapshots": [
                    {
                        "Snapshots": [
                            {
                                "SnapshotId": "snap-backup",
                                "VolumeId": "vol-gone",
                                "VolumeSize": 100,
                                "StartTime": now - timedelta(days=300),
                                "Tags": [{"Key": "backup", "Value": "true"}],
                            }
                        ]
                    }
                ],
            }
        )
        session = FakeSession(ec2=ec2)
        findings = snapshots_orphaned.detect(session, REGION)
        self.assertEqual(len(findings), 1)
        self.assertLess(findings[0].confidence, 0.7)
        self.assertTrue(findings[0].details["looks_like_backup"])


class TestEipUnused(unittest.TestCase):
    def test_only_unassociated_addresses_become_findings(self):
        def describe_addresses(**_):
            return {
                "Addresses": [
                    {
                        "AllocationId": "eipalloc-idle",
                        "PublicIp": "54.10.20.30",
                        "Domain": "vpc",
                        "Tags": [],
                    },
                    {
                        "AllocationId": "eipalloc-attached",
                        "PublicIp": "54.10.20.31",
                        "AssociationId": "eipassoc-xyz",
                        "Domain": "vpc",
                    },
                ]
            }

        ec2 = FakeClient(describe_addresses=describe_addresses)
        session = FakeSession(ec2=ec2)
        findings = eip_unused.detect(session, REGION)

        self.assertEqual([f.resource_id for f in findings], ["eipalloc-idle"])
        self.assertGreater(findings[0].monthly_savings, 0)


class TestEc2Idle(unittest.TestCase):
    def test_flags_low_cpu_instance(self):
        ec2 = FakeClient(
            paginators={
                "describe_instances": [
                    {
                        "Reservations": [
                            {
                                "Instances": [
                                    {
                                        "InstanceId": "i-quiet",
                                        "InstanceType": "m5.large",
                                        "Tags": [{"Key": "Name", "Value": "old-box"}],
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        )
        cw = FakeClient(
            get_metric_statistics=lambda **_: cpu_datapoints(hours=200, peak=1.5),
        )
        session = FakeSession(ec2=ec2, cloudwatch=cw)
        findings = ec2_idle.detect(session, REGION)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].detector, "ec2_idle")
        self.assertEqual(findings[0].details["peak_cpu_pct"], 1.5)

    def test_skips_busy_instance(self):
        ec2 = FakeClient(
            paginators={
                "describe_instances": [
                    {
                        "Reservations": [
                            {"Instances": [{"InstanceId": "i-busy", "InstanceType": "m5.large"}]}
                        ]
                    }
                ]
            }
        )
        cw = FakeClient(get_metric_statistics=lambda **_: cpu_datapoints(hours=200, peak=85.0))
        session = FakeSession(ec2=ec2, cloudwatch=cw)
        self.assertEqual(ec2_idle.detect(session, REGION), [])


class TestEc2Rightsize(unittest.TestCase):
    def test_recommends_one_step_down(self):
        ec2 = FakeClient(
            paginators={
                "describe_instances": [
                    {
                        "Reservations": [
                            {
                                "Instances": [
                                    {
                                        "InstanceId": "i-mid",
                                        "InstanceType": "m5.xlarge",
                                        "Tags": [{"Key": "Name", "Value": "api"}],
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        )
        cw = FakeClient(
            get_metric_statistics=lambda **_: cpu_datapoints(hours=200, peak=25.0, avg=12.0),
        )
        session = FakeSession(ec2=ec2, cloudwatch=cw)
        findings = ec2_rightsize.detect(session, REGION)

        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f.details["current_type"], "m5.xlarge")
        self.assertEqual(f.details["recommended_type"], "m5.large")
        self.assertFalse(f.details["memory_verified"])
        self.assertGreater(f.monthly_savings, 0)

    def test_skips_idle_instance(self):
        # Pure-idle is owned by ec2_idle; rightsize should not double-flag it.
        ec2 = FakeClient(
            paginators={
                "describe_instances": [
                    {
                        "Reservations": [
                            {"Instances": [{"InstanceId": "i-x", "InstanceType": "m5.xlarge"}]}
                        ]
                    }
                ]
            }
        )
        cw = FakeClient(get_metric_statistics=lambda **_: cpu_datapoints(hours=200, peak=2.0))
        session = FakeSession(ec2=ec2, cloudwatch=cw)
        self.assertEqual(ec2_rightsize.detect(session, REGION), [])

    def test_size_ladder(self):
        self.assertEqual(next_smaller_ec2_type("m5.xlarge"), "m5.large")
        self.assertEqual(next_smaller_ec2_type("t3.nano"), None)
        self.assertEqual(next_smaller_ec2_type("c6i.4xlarge"), "c6i.2xlarge")
        self.assertIsNone(next_smaller_ec2_type("weird-type"))


class TestLbIdle(unittest.TestCase):
    def _session(self, traffic_sum: float, healthy_count: int):
        elbv2 = FakeClient(
            paginators={
                "describe_load_balancers": [
                    {
                        "LoadBalancers": [
                            {
                                "LoadBalancerArn": "arn:aws:elasticloadbalancing:us-east-1:1:loadbalancer/app/legacy/abc",
                                "LoadBalancerName": "legacy",
                                "Type": "application",
                            }
                        ]
                    }
                ]
            },
            describe_target_groups=lambda **_: {"TargetGroups": [{"TargetGroupArn": "tg-1"}]},
            describe_target_health=lambda **_: {
                "TargetHealthDescriptions": [
                    {"TargetHealth": {"State": "healthy"}} for _ in range(healthy_count)
                ]
            },
            describe_tags=lambda **_: {"TagDescriptions": [{"Tags": []}]},
        )
        cw = FakeClient(
            get_metric_statistics=lambda **_: {
                "Datapoints": [{"Sum": traffic_sum, "Maximum": 0, "Average": 0}]
            }
        )
        return FakeSession(elbv2=elbv2, cloudwatch=cw)

    def test_flags_lb_with_no_targets_and_no_traffic(self):
        findings = lb_idle.detect(self._session(traffic_sum=0.0, healthy_count=0), REGION)
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f.details["healthy_targets"], 0)
        self.assertEqual(f.details["traffic_metric_sum_lookback"], 0.0)
        self.assertGreater(f.monthly_savings, 0)

    def test_skips_busy_lb(self):
        findings = lb_idle.detect(self._session(traffic_sum=1_000_000, healthy_count=3), REGION)
        self.assertEqual(findings, [])


class TestRdsOverprovisioned(unittest.TestCase):
    def test_flags_low_cpu_rds(self):
        rds = FakeClient(
            paginators={
                "describe_db_instances": [
                    {
                        "DBInstances": [
                            {
                                "DBInstanceIdentifier": "prod-db",
                                "DBInstanceClass": "db.m5.xlarge",
                                "Engine": "postgres",
                                "MultiAZ": False,
                                "DBInstanceStatus": "available",
                            }
                        ]
                    }
                ]
            }
        )
        cw = FakeClient(
            get_metric_statistics=lambda **_: cpu_datapoints(hours=200, peak=8.0, avg=3.0),
        )
        session = FakeSession(rds=rds, cloudwatch=cw)
        findings = rds_overprovisioned.detect(session, REGION)

        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f.details["db_class"], "db.m5.xlarge")
        self.assertTrue(f.details["price_known"])
        self.assertGreater(f.monthly_savings, 0)

    def test_skips_busy_or_unavailable(self):
        rds = FakeClient(
            paginators={
                "describe_db_instances": [
                    {
                        "DBInstances": [
                            {
                                "DBInstanceIdentifier": "busy",
                                "DBInstanceClass": "db.m5.large",
                                "Engine": "postgres",
                                "DBInstanceStatus": "available",
                            },
                            {
                                "DBInstanceIdentifier": "rebooting",
                                "DBInstanceClass": "db.m5.large",
                                "Engine": "postgres",
                                "DBInstanceStatus": "rebooting",
                            },
                        ]
                    }
                ]
            }
        )
        cw = FakeClient(get_metric_statistics=lambda **_: cpu_datapoints(hours=200, peak=70.0))
        session = FakeSession(rds=rds, cloudwatch=cw)
        self.assertEqual(rds_overprovisioned.detect(session, REGION), [])


if __name__ == "__main__":
    unittest.main()
