"""Live AWS Pricing API lookups, with the hardcoded fallback as a safety net.

The Pricing API is only served from ``us-east-1`` and ``ap-south-1``. Its JSON
schema is sprawling; we filter aggressively and parse the one "OnDemand"
SKU/term/dimension we care about.

Design goals:
* **No detector changes.** Detectors keep calling ``pricing.ebs_monthly_cost``
  etc. This module is wired in by :func:`bind` so those functions try the live
  API first and silently fall back to the hardcoded map on any failure.
* **Cache aggressively.** Pricing data is essentially static; one process never
  needs to fetch the same SKU twice.
* **Never crash the audit.** Any error → ``None`` → fallback path.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache

from botocore.config import Config

log = logging.getLogger(__name__)

# Pricing API only lives in these two regions; us-east-1 is the canonical one.
_PRICING_ENDPOINT_REGION = "us-east-1"

# Pricing query results don't change inside a run; map AWS region codes to the
# human "location" string the API filters on. Incomplete by design — we only
# need the regions we actually scan, with a fallback for everything else.
_REGION_TO_LOCATION = {
    "us-east-1": "US East (N. Virginia)",
    "us-east-2": "US East (Ohio)",
    "us-west-1": "US West (N. California)",
    "us-west-2": "US West (Oregon)",
    "eu-west-1": "EU (Ireland)",
    "eu-west-2": "EU (London)",
    "eu-central-1": "EU (Frankfurt)",
    "ap-south-1": "Asia Pacific (Mumbai)",
    "ap-southeast-1": "Asia Pacific (Singapore)",
    "ap-southeast-2": "Asia Pacific (Sydney)",
    "ap-northeast-1": "Asia Pacific (Tokyo)",
    "il-central-1": "Israel (Tel Aviv)",
}


def _location_for(region: str) -> str | None:
    return _REGION_TO_LOCATION.get(region)


class LivePricingClient:
    """Thin wrapper around the Pricing API with per-process LRU caching."""

    def __init__(self, session):
        self._client = session.client(
            "pricing",
            region_name=_PRICING_ENDPOINT_REGION,
            config=Config(
                connect_timeout=10,
                read_timeout=20,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )

    @lru_cache(maxsize=128)
    def ebs_gb_month(self, volume_type: str, region: str) -> float | None:
        """USD per GB-month for an EBS volume type in ``region``."""
        location = _location_for(region)
        if location is None:
            return None
        try:
            return self._first_price(
                ServiceCode="AmazonEC2",
                Filters=[
                    _filter("productFamily", "Storage"),
                    _filter("volumeApiName", volume_type),
                    _filter("location", location),
                ],
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("pricing api ebs %s/%s failed: %s", volume_type, region, exc)
            return None

    @lru_cache(maxsize=512)
    def ec2_hourly(self, instance_type: str, region: str) -> float | None:
        """USD per hour for an on-demand Linux EC2 instance in ``region``."""
        location = _location_for(region)
        if location is None:
            return None
        try:
            return self._first_price(
                ServiceCode="AmazonEC2",
                Filters=[
                    _filter("productFamily", "Compute Instance"),
                    _filter("instanceType", instance_type),
                    _filter("operatingSystem", "Linux"),
                    _filter("tenancy", "Shared"),
                    _filter("preInstalledSw", "NA"),
                    _filter("capacitystatus", "Used"),
                    _filter("location", location),
                ],
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("pricing api ec2 %s/%s failed: %s", instance_type, region, exc)
            return None

    @lru_cache(maxsize=256)
    def rds_hourly(
        self,
        db_class: str,
        engine: str,
        region: str,
        multi_az: bool = False,
    ) -> float | None:
        """USD per hour for an RDS DB instance class/engine in ``region``."""
        location = _location_for(region)
        if location is None:
            return None
        try:
            return self._first_price(
                ServiceCode="AmazonRDS",
                Filters=[
                    _filter("productFamily", "Database Instance"),
                    _filter("instanceType", db_class),
                    _filter("databaseEngine", _rds_engine_label(engine)),
                    _filter("deploymentOption", "Multi-AZ" if multi_az else "Single-AZ"),
                    _filter("location", location),
                ],
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("pricing api rds %s/%s/%s failed: %s", db_class, engine, region, exc)
            return None

    def _first_price(self, **query) -> float | None:
        """Run a single-page Pricing query and parse the first on-demand USD price."""
        page = self._client.get_products(MaxResults=1, **query)
        items = page.get("PriceList", [])
        if not items:
            return None
        item = items[0]
        if isinstance(item, str):
            item = json.loads(item)
        terms = item.get("terms", {}).get("OnDemand", {})
        for term in terms.values():
            for dim in term.get("priceDimensions", {}).values():
                usd = dim.get("pricePerUnit", {}).get("USD")
                if usd is None:
                    continue
                try:
                    price = float(usd)
                except ValueError:
                    continue
                if price > 0:
                    return price
        return None


def _filter(field: str, value: str) -> dict:
    return {"Type": "TERM_MATCH", "Field": field, "Value": value}


def _rds_engine_label(engine: str) -> str:
    """Translate an RDS engine name into the Pricing API's label."""
    mapping = {
        "mysql": "MySQL",
        "postgres": "PostgreSQL",
        "postgresql": "PostgreSQL",
        "mariadb": "MariaDB",
        "aurora": "Aurora MySQL",
        "aurora-mysql": "Aurora MySQL",
        "aurora-postgresql": "Aurora PostgreSQL",
    }
    return mapping.get(engine.lower(), engine)
