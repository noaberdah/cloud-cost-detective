"""Render an :class:`AuditResult` into a styled, self-contained HTML report.

No external assets or dependencies — the CSS is inlined so the file opens
anywhere. The agent-written executive summary (Stage 5) will slot in later;
for now we show a deterministic summary line.
"""

from __future__ import annotations

import html
from pathlib import Path

from costdetective.cost_explorer import SpendAnomaly, SpendSummary
from costdetective.models import Finding, Severity
from costdetective.scan import AuditResult

_SEVERITY_COLORS = {
    Severity.CRITICAL: "#b91c1c",
    Severity.HIGH: "#ea580c",
    Severity.MEDIUM: "#ca8a04",
    Severity.LOW: "#2563eb",
}


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _esc(value) -> str:
    return html.escape(str(value))


def _severity_badge(sev: Severity) -> str:
    color = _SEVERITY_COLORS.get(sev, "#475569")
    return (
        f'<span class="badge" style="background:{color}">'
        f"{_esc(sev.value.upper())}</span>"
    )


def _confidence_bar(confidence: float) -> str:
    pct = max(0, min(100, round(confidence * 100)))
    return (
        f'<div class="conf"><span class="conf-label">{pct}%</span>'
        f'<div class="conf-track"><div class="conf-fill" style="width:{pct}%"></div>'
        f"</div></div>"
    )


def _detail_chips(details: dict) -> str:
    if not details:
        return ""
    chips = []
    for key, val in details.items():
        if key == "tags" and isinstance(val, dict):
            for tk, tv in val.items():
                chips.append(f'<span class="chip tag">{_esc(tk)}: {_esc(tv)}</span>')
        else:
            chips.append(f'<span class="chip">{_esc(key)}: {_esc(val)}</span>')
    return '<div class="chips">' + "".join(chips) + "</div>"


def _finding_row(index: int, f: Finding) -> str:
    return f"""
      <tr>
        <td class="num">{index}</td>
        <td>
          <div class="summary">{_esc(f.summary)}</div>
          <div class="meta">
            <code>{_esc(f.resource_id)}</code> &middot; {_esc(f.resource_type)}
            &middot; {_esc(f.region)} &middot; <em>{_esc(f.detector)}</em>
          </div>
          <div class="rec">&rarr; {_esc(f.recommendation)}</div>
          {_detail_chips(f.details)}
        </td>
        <td class="center">{_severity_badge(f.severity)}</td>
        <td class="center">{_esc(f.effort)}</td>
        <td class="center">{_confidence_bar(f.confidence)}</td>
        <td class="money">{_money(f.monthly_savings)}</td>
      </tr>"""


def _spend_card(spend: SpendSummary | None, error: str | None) -> str:
    """One metric card showing real 30-day spend, or a graceful fallback."""
    if spend is None:
        msg = "Cost Explorer unavailable" if error else "Not fetched"
        return (
            '<div class="card"><div class="label">Real spend (30d)</div>'
            f'<div class="value muted">{_esc(msg)}</div></div>'
        )
    return (
        '<div class="card"><div class="label">Real spend (last 30d)</div>'
        f'<div class="value">{_money(spend.total_usd)}</div></div>'
    )


def _spend_breakdown_section(spend: SpendSummary | None) -> str:
    if spend is None or not spend.by_service:
        return ""
    top = spend.top_services[:8]
    rows = "".join(
        f'<tr><td>{_esc(service)}</td><td class="money">{_money(amount)}</td></tr>'
        for service, amount in top
    )
    period = f"{spend.period_start.isoformat()} → {spend.period_end.isoformat()}"
    return f"""
    <section class="panel">
      <h2>Real spend by service <span class="period">{_esc(period)}</span></h2>
      <table class="mini">
        <thead><tr><th>Service</th><th class="money">$ in period</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </section>"""


def _anomaly_section(anomalies: list[SpendAnomaly]) -> str:
    if not anomalies:
        return ""
    items = "".join(
        f'<li class="anomaly anomaly-{a.direction}">'
        f'<span class="arrow">{"↑" if a.direction == "up" else "↓"}</span> '
        f'<span class="anom-text">{_esc(a.summary())}</span>'
        f"</li>"
        for a in anomalies
    )
    return f"""
    <section class="panel">
      <h2>Spend anomalies <span class="period">week-over-week</span></h2>
      <ul class="anoms">{items}</ul>
    </section>"""


def _owner_groups_section(result: AuditResult) -> str:
    groups = result.findings_by_owner
    if not groups:
        return ""
    ordered = sorted(
        groups.items(),
        key=lambda kv: sum(f.monthly_savings for f in kv[1]),
        reverse=True,
    )
    rows = []
    for owner, items in ordered:
        subtotal = sum(f.monthly_savings for f in items)
        rows.append(
            f"<tr><td>{_esc(owner)}</td>"
            f'<td class="center">{len(items)}</td>'
            f'<td class="money">{_money(subtotal)}</td></tr>'
        )
    return f"""
    <section class="panel">
      <h2>Findings by owner / team tag</h2>
      <table class="mini">
        <thead><tr><th>Owner</th><th class="center">Findings</th><th class="money">Recoverable / month</th></tr></thead>
        <tbody>{"".join(rows)}</tbody>
      </table>
    </section>"""


def render_report(result: AuditResult) -> str:
    """Return the full HTML document for an audit result."""
    rows = "".join(
        _finding_row(i, f) for i, f in enumerate(result.findings, start=1)
    )
    if not rows:
        rows = (
            '<tr><td colspan="6" class="empty">No waste detected in this region. '
            "Either the account is tidy, or the resources live in another region.</td></tr>"
        )

    mode = "Synthetic sample data" if result.synthetic else "Live AWS account"
    generated = result.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    finding_count = len(result.findings)
    pct = result.savings_pct_of_spend
    pct_card = (
        '<div class="card"><div class="label">Recoverable share of spend</div>'
        f'<div class="value">{pct:.1f}%</div></div>'
        if pct is not None else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cloud Cost Detective — Savings Report</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 2.5rem 1.5rem;
    font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: #f1f5f9; color: #0f172a; line-height: 1.5;
  }}
  .wrap {{ max-width: 1040px; margin: 0 auto; }}
  header h1 {{ margin: 0; font-size: 1.7rem; }}
  header .sub {{ color: #64748b; margin-top: .25rem; font-size: .95rem; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
    gap: 1rem; margin: 1.75rem 0; }}
  .card {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 12px;
    padding: 1.1rem 1.25rem; box-shadow: 0 1px 2px rgba(15,23,42,.04); }}
  .card .label {{ font-size: .8rem; text-transform: uppercase; letter-spacing: .04em;
    color: #64748b; }}
  .card .value {{ font-size: 1.7rem; font-weight: 700; margin-top: .35rem; }}
  .card.savings .value {{ color: #15803d; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff;
    border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden; }}
  thead th {{ text-align: left; font-size: .78rem; text-transform: uppercase;
    letter-spacing: .04em; color: #475569; background: #f8fafc;
    padding: .75rem 1rem; border-bottom: 1px solid #e2e8f0; }}
  tbody td {{ padding: 1rem; border-bottom: 1px solid #f1f5f9; vertical-align: top; }}
  tbody tr:last-child td {{ border-bottom: none; }}
  .num {{ color: #94a3b8; font-variant-numeric: tabular-nums; width: 2rem; }}
  .center {{ text-align: center; white-space: nowrap; }}
  .money {{ text-align: right; font-weight: 700; color: #15803d;
    font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .summary {{ font-weight: 600; }}
  .meta {{ color: #64748b; font-size: .82rem; margin-top: .2rem; }}
  .meta code {{ background: #f1f5f9; padding: .05rem .35rem; border-radius: 5px; }}
  .rec {{ color: #334155; font-size: .9rem; margin-top: .35rem; }}
  .chips {{ margin-top: .55rem; display: flex; flex-wrap: wrap; gap: .35rem; }}
  .chip {{ font-size: .72rem; background: #f1f5f9; color: #475569;
    padding: .15rem .45rem; border-radius: 999px; }}
  .chip.tag {{ background: #eef2ff; color: #4338ca; }}
  .badge {{ color: #fff; font-size: .7rem; font-weight: 700; letter-spacing: .03em;
    padding: .2rem .5rem; border-radius: 999px; }}
  .conf {{ width: 78px; margin: 0 auto; }}
  .conf-label {{ display: block; font-size: .72rem; font-weight: 700;
    color: #1e293b; margin-bottom: 3px; }}
  .conf-track {{ position: relative; height: 8px; background: #e2e8f0;
    border-radius: 999px; overflow: hidden; }}
  .conf-fill {{ position: absolute; inset: 0 auto 0 0; background: #6366f1; }}
  .empty {{ text-align: center; color: #64748b; padding: 2.5rem 1rem; }}
  .muted {{ color: #94a3b8; font-size: 1rem; font-weight: 500; }}
  .panel {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 12px;
    padding: 1.25rem 1.5rem; margin-top: 1.5rem;
    box-shadow: 0 1px 2px rgba(15,23,42,.04); }}
  .panel h2 {{ margin: 0 0 .9rem; font-size: 1.05rem; color: #0f172a; }}
  .panel h2 .period {{ font-size: .75rem; color: #94a3b8; font-weight: 500;
    margin-left: .5rem; text-transform: uppercase; letter-spacing: .04em; }}
  table.mini {{ width: 100%; border: none; box-shadow: none; }}
  table.mini thead th {{ background: transparent; padding: .4rem .5rem;
    color: #64748b; }}
  table.mini tbody td {{ padding: .5rem .5rem; }}
  ul.anoms {{ list-style: none; padding: 0; margin: 0; }}
  ul.anoms li {{ display: flex; gap: .55rem; align-items: baseline;
    padding: .55rem .25rem; border-bottom: 1px solid #f1f5f9; }}
  ul.anoms li:last-child {{ border-bottom: none; }}
  .anomaly .arrow {{ font-weight: 700; font-size: 1rem; }}
  .anomaly-up .arrow {{ color: #b91c1c; }}
  .anomaly-down .arrow {{ color: #15803d; }}
  .anom-text {{ color: #1e293b; }}
  footer {{ color: #94a3b8; font-size: .8rem; margin-top: 1.5rem; text-align: center; }}
</style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>🔎 Cloud Cost Detective</h1>
      <div class="sub">{mode} &middot; region {_esc(result.region)} &middot; generated {generated}</div>
    </header>

    <section class="cards">
      <div class="card savings">
        <div class="label">Recoverable / month</div>
        <div class="value">{_money(result.total_monthly_savings)}</div>
      </div>
      <div class="card savings">
        <div class="label">Recoverable / year</div>
        <div class="value">{_money(result.total_annual_savings)}</div>
      </div>
      {_spend_card(result.spend, result.spend_error)}
      {pct_card}
      <div class="card">
        <div class="label">Findings</div>
        <div class="value">{finding_count}</div>
      </div>
    </section>

    {_anomaly_section(result.anomalies)}
    {_spend_breakdown_section(result.spend)}
    {_owner_groups_section(result)}

    <h2 style="margin-top:1.75rem;font-size:1.05rem;">All findings, ranked</h2>
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>Finding</th>
          <th class="center">Severity</th>
          <th class="center">Effort</th>
          <th class="center">Confidence</th>
          <th class="money">$/month</th>
        </tr>
      </thead>
      <tbody>{rows}
      </tbody>
    </table>

    <footer>
      Read-only audit &middot; recommendations only — no resources were changed.
    </footer>
  </div>
</body>
</html>
"""


def write_report(result: AuditResult, output_path: str | Path) -> Path:
    """Render and write the report to disk; return the path written."""
    path = Path(output_path)
    path.write_text(render_report(result), encoding="utf-8")
    return path
