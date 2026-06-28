"""Shared boto3 client construction for the read-only AWS detectors.

Every AWS call goes through a botocore ``Config`` with bounded connect/read
timeouts and bounded retries, so a slow or unresponsive endpoint surfaces as a
caught error (and the detector is skipped) instead of hanging the whole audit
indefinitely.
"""

from __future__ import annotations

from botocore.config import Config

# Bounded timeouts so a single slow call can't make the audit appear frozen.
# Adaptive retries smooth over throttling and transient network blips.
DEFAULT_CONFIG = Config(
    connect_timeout=10,
    read_timeout=30,
    retries={"max_attempts": 3, "mode": "adaptive"},
)


def client(session, service: str, region: str):
    """Return a boto3 client for ``service`` with the standard audit config."""
    return session.client(service, region_name=region, config=DEFAULT_CONFIG)
