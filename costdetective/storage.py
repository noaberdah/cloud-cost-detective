"""Persist each audit run to a local SQLite database for trend tracking.

One row per completed audit, holding only the deterministic facts we chart:
when it ran, the recoverable dollars, the region, and whether it was synthetic.
No AWS or Anthropic involvement — this is plain local bookkeeping.

Every public function is defensively isolated the same way the detectors are:
a storage hiccup (locked file, bad permissions, corrupt db) logs a warning and
returns a null result, so persistence can never crash an audit. The audit and
its report are the product; the trend history is a nice-to-have on top.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# The database lives at the repo root (one level above this package), matching
# where `report.html` and `findings*.json` are written. Gitignored, not source.
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "runs.db"

# How many past runs the report's trend line shows by default.
DEFAULT_TREND_LIMIT = 12

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS runs (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at          TEXT    NOT NULL,
    total_monthly_savings REAL    NOT NULL,
    region                TEXT    NOT NULL,
    synthetic             INTEGER NOT NULL,
    finding_count         INTEGER NOT NULL
)
"""


@dataclass
class RunRecord:
    """One persisted audit row, read back for the trend line."""

    generated_at: datetime
    total_monthly_savings: float
    region: str
    synthetic: bool
    finding_count: int


def save_run(result, db_path: str | Path = DEFAULT_DB_PATH) -> int | None:
    """Append one :class:`AuditResult` as a row; return its id, or ``None``.

    Converts the datetime to ISO text and the ``synthetic`` bool to 0/1
    explicitly. Any failure is logged and swallowed — saving history must never
    break the audit that produced it.
    """
    try:
        with _connect(db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO runs "
                "(generated_at, total_monthly_savings, region, synthetic, finding_count) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    result.generated_at.isoformat(),      # datetime -> ISO text
                    float(result.total_monthly_savings),  # deterministic $ total
                    result.region,
                    int(bool(result.synthetic)),          # bool -> 0/1
                    len(result.findings),
                ),
            )
            log.info("saved run to %s (row %s)", db_path, cursor.lastrowid)
            return cursor.lastrowid
    except Exception as exc:  # noqa: BLE001 - persistence is best-effort
        log.warning("could not save run to %s: %s", db_path, exc)
        return None


def load_recent_runs(
    limit: int = DEFAULT_TREND_LIMIT,
    synthetic: bool | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> list[RunRecord]:
    """Return up to ``limit`` past runs, oldest first (ready to plot).

    Pass ``synthetic=`` to keep the trend honest: a real audit charts only real
    history (``synthetic=False``) and a synthetic demo charts only synthetic
    runs, so a demo never pollutes the real trend. On any failure we log and
    return ``[]``, and the report simply omits the trend.
    """
    try:
        with _connect(db_path) as conn:
            where = ""
            params: list = []
            if synthetic is not None:
                where = "WHERE synthetic = ?"
                params.append(int(bool(synthetic)))
            params.append(limit)
            # Newest-first with LIMIT to cap the window, then reverse so the
            # caller gets chronological (oldest -> newest) order for charting.
            cursor = conn.execute(
                "SELECT generated_at, total_monthly_savings, region, synthetic, "
                f"finding_count FROM runs {where} ORDER BY generated_at DESC, id DESC "
                "LIMIT ?",
                params,
            )
            rows = cursor.fetchall()
    except Exception as exc:  # noqa: BLE001 - trend is best-effort
        log.warning("could not load runs from %s: %s", db_path, exc)
        return []

    records = [_row_to_record(row) for row in rows]
    records.reverse()
    return records


def _connect(db_path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) the database and ensure the schema exists."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(_CREATE_TABLE)
    return conn


def _row_to_record(row: tuple) -> RunRecord:
    generated_at, total, region, synthetic, finding_count = row
    return RunRecord(
        generated_at=_parse_dt(generated_at),
        total_monthly_savings=float(total),
        region=str(region),
        synthetic=bool(synthetic),
        finding_count=int(finding_count),
    )


def _parse_dt(text: str) -> datetime:
    """Parse an ISO timestamp back to an aware datetime, tolerating oddities."""
    try:
        dt = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
