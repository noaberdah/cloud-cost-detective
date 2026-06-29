"""Real spend + week-over-week anomalies via the Cost Explorer API.

Cost Explorer (``ce``) is only served from ``us-east-1``, so we always create
the client there regardless of the audit's --region flag. All calls are
read-only.

Two things live here:

* :func:`fetch_monthly_spend` — last 30 days of unblended cost grouped by
  service, used by the report's "real spend" card.
* :func:`detect_spend_anomalies` — compares the most recent 7 days of daily
  spend per service against the previous 7 days and flags services whose
  week-over-week change exceeds a configurable threshold (default 30%).

Both functions return plain data; the agent layer (Stage 5) will write prose
about it but never invents the numbers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

log = logging.getLogger(__name__)

# CE is only served from us-east-1 — using any other region returns an empty
# result with no error, which is the worst possible failure mode.
_CE_REGION = "us-east-1"

# Services dominated by tiny ($/cents) spend get noisy WoW ratios. Skip a
# service entirely if neither week crosses this floor.
_ANOMALY_MIN_WEEKLY_USD = 1.0

# Default threshold for "this is worth a human looking at."
DEFAULT_ANOMALY_THRESHOLD = 0.30


@dataclass
class SpendSummary:
    """Last-N-days unblended spend, with a per-service breakdown."""

    total_usd: float
    by_service: dict[str, float]
    period_start: date
    period_end: date
    days: int

    @property
    def top_services(self) -> list[tuple[str, float]]:
        return sorted(self.by_service.items(), key=lambda kv: kv[1], reverse=True)


@dataclass
class SpendAnomaly:
    """One service whose week-over-week spend moved enough to flag."""

    service: str
    previous_week_usd: float
    this_week_usd: float
    pct_change: float            # signed: +1.0 = doubled, -0.5 = halved
    threshold: float
    period_start: date
    period_end: date

    @property
    def direction(self) -> str:
        return "up" if self.pct_change >= 0 else "down"

    def summary(self) -> str:
        sign = "+" if self.pct_change >= 0 else "-"
        return (
            f"{self.service} spend went {self.direction} {sign}{abs(self.pct_change) * 100:.0f}% "
            f"week-over-week (${self.previous_week_usd:,.2f} → ${self.this_week_usd:,.2f})"
        )


@dataclass
class CostExplorerResult:
    """Bundle returned by :func:`gather_cost_explorer_data`."""

    spend: SpendSummary | None = None
    anomalies: list[SpendAnomaly] = field(default_factory=list)
    available: bool = False              # False means we couldn't reach CE
    error: str | None = None


def fetch_monthly_spend(session, days: int = 30) -> SpendSummary:
    """Last ``days`` of unblended cost grouped by service."""
    ce = session.client("ce", region_name=_CE_REGION)
    end = date.today()
    start = end - timedelta(days=days)

    by_service: dict[str, float] = {}
    next_token = None
    while True:
        kwargs = {
            "TimePeriod": {"Start": start.isoformat(), "End": end.isoformat()},
            "Granularity": "MONTHLY",
            "Metrics": ["UnblendedCost"],
            "GroupBy": [{"Type": "DIMENSION", "Key": "SERVICE"}],
        }
        if next_token:
            kwargs["NextPageToken"] = next_token
        page = ce.get_cost_and_usage(**kwargs)
        for bucket in page.get("ResultsByTime", []):
            for group in bucket.get("Groups", []):
                service = group["Keys"][0]
                amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
                by_service[service] = by_service.get(service, 0.0) + amount
        next_token = page.get("NextPageToken")
        if not next_token:
            break

    total = round(sum(by_service.values()), 2)
    by_service = {k: round(v, 2) for k, v in by_service.items() if v > 0}
    return SpendSummary(
        total_usd=total,
        by_service=by_service,
        period_start=start,
        period_end=end,
        days=days,
    )


def detect_spend_anomalies(
    session,
    threshold: float = DEFAULT_ANOMALY_THRESHOLD,
) -> list[SpendAnomaly]:
    """Flag services whose WoW spend change exceeds ``threshold`` (default 30%)."""
    ce = session.client("ce", region_name=_CE_REGION)
    end = date.today()
    start = end - timedelta(days=14)
    midpoint = end - timedelta(days=7)

    page = ce.get_cost_and_usage(
        TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )

    prev_week: dict[str, float] = {}
    this_week: dict[str, float] = {}
    for bucket in page.get("ResultsByTime", []):
        bucket_start = date.fromisoformat(bucket["TimePeriod"]["Start"])
        target = this_week if bucket_start >= midpoint else prev_week
        for group in bucket.get("Groups", []):
            service = group["Keys"][0]
            amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
            target[service] = target.get(service, 0.0) + amount

    anomalies: list[SpendAnomaly] = []
    for service in set(prev_week) | set(this_week):
        prev = prev_week.get(service, 0.0)
        cur = this_week.get(service, 0.0)
        if max(prev, cur) < _ANOMALY_MIN_WEEKLY_USD:
            continue
        if prev == 0:
            # New service appearing this week — always interesting.
            pct = 1.0
        else:
            pct = (cur - prev) / prev
        if abs(pct) < threshold:
            continue
        anomalies.append(
            SpendAnomaly(
                service=service,
                previous_week_usd=round(prev, 2),
                this_week_usd=round(cur, 2),
                pct_change=round(pct, 4),
                threshold=threshold,
                period_start=start,
                period_end=end,
            )
        )
    anomalies.sort(key=lambda a: abs(a.pct_change), reverse=True)
    return anomalies


def gather_cost_explorer_data(
    session,
    days: int = 30,
    anomaly_threshold: float = DEFAULT_ANOMALY_THRESHOLD,
) -> CostExplorerResult:
    """Best-effort: fetch spend + anomalies, swallow failures into ``error``."""
    try:
        spend = fetch_monthly_spend(session, days=days)
    except Exception as exc:  # noqa: BLE001 - never abort the audit
        log.warning("cost explorer spend fetch failed: %s", exc)
        return CostExplorerResult(available=False, error=str(exc))

    try:
        anomalies = detect_spend_anomalies(session, threshold=anomaly_threshold)
    except Exception as exc:  # noqa: BLE001
        log.warning("cost explorer anomaly fetch failed: %s", exc)
        anomalies = []

    return CostExplorerResult(spend=spend, anomalies=anomalies, available=True)
