"""Detector: allocated Elastic IPs that are not associated with anything.

Since Feb 2024, AWS bills every public IPv4 address (associated or not) at
~$0.005/hour. An unassociated EIP just sits there burning that charge. One
read-only ``DescribeAddresses`` call is enough to find them.
"""

from __future__ import annotations

from costdetective.awsclients import client
from costdetective.models import Finding, severity_from_savings
from costdetective.pricing import eip_monthly_cost

NAME = "eip_unused"


def _tags_to_dict(tag_list) -> dict:
    return {t["Key"]: t["Value"] for t in (tag_list or [])}


def detect(session, region: str) -> list[Finding]:
    ec2 = client(session, "ec2", region)
    addrs = ec2.describe_addresses().get("Addresses", [])
    findings: list[Finding] = []

    for addr in addrs:
        if addr.get("AssociationId"):
            continue  # in use — not waste

        public_ip = addr.get("PublicIp", "?")
        alloc_id = addr.get("AllocationId", public_ip)
        monthly = eip_monthly_cost()
        tags = _tags_to_dict(addr.get("Tags"))

        findings.append(
            Finding(
                detector=NAME,
                resource_id=alloc_id,
                resource_type="Elastic IP",
                region=region,
                severity=severity_from_savings(monthly),
                monthly_savings=monthly,
                summary=f"Elastic IP {public_ip} allocated but not associated with any resource",
                recommendation="Release the address to stop the idle-IP charge.",
                confidence=0.98,
                effort="low",
                details={
                    "public_ip": public_ip,
                    "domain": addr.get("Domain"),
                    "network_border_group": addr.get("NetworkBorderGroup"),
                    "tags": tags,
                },
            )
        )
    return findings
