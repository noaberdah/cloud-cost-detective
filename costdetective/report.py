"""Render an :class:`AuditResult` into a styled, self-contained HTML report.

No external assets or dependencies — the CSS is inlined so the file opens
anywhere. When the agent layer (Stage 5) is available, the report gains an
AI-written executive summary, a prioritization/commitments panel, and a
per-finding confidence caveat; without it, those sections are simply omitted.
"""

from __future__ import annotations

import html
from pathlib import Path

from costdetective.cost_explorer import SpendAnomaly, SpendSummary
from costdetective.models import Finding, Severity
from costdetective.scan import AuditResult
from costdetective.storage import RunRecord

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


# Keys the agent layer writes into details; rendered specially, not as chips.
_AGENT_DETAIL_KEYS = {"ai_confidence_reason"}


def _detail_chips(details: dict) -> str:
    if not details:
        return ""
    chips = []
    for key, val in details.items():
        if key in _AGENT_DETAIL_KEYS:
            continue
        if key == "tags" and isinstance(val, dict):
            for tk, tv in val.items():
                chips.append(f'<span class="chip tag">{_esc(tk)}: {_esc(tv)}</span>')
        else:
            chips.append(f'<span class="chip">{_esc(key)}: {_esc(val)}</span>')
    return '<div class="chips">' + "".join(chips) + "</div>"


def _ai_note(f: Finding) -> str:
    """Render the waste-hunter's one-line confidence caveat, if it left one."""
    reason = f.details.get("ai_confidence_reason")
    if not reason:
        return ""
    return f'<div class="ai-note"><span class="ai-tag">AI</span> {_esc(reason)}</div>'


def _finding_row(index: int, f: Finding) -> str:
    accent = _SEVERITY_COLORS.get(f.severity, "#475569")
    return f"""
      <tr>
        <td class="num" style="border-left:4px solid {accent}">{index}</td>
        <td>
          <div class="summary">{_esc(f.summary)}</div>
          <div class="meta">
            <code>{_esc(f.resource_id)}</code> &middot; {_esc(f.resource_type)}
            &middot; {_esc(f.region)} &middot; <em>{_esc(f.detector)}</em>
          </div>
          <div class="rec">&rarr; {_esc(f.recommendation)}</div>
          {_ai_note(f)}
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


def _summary_section(result: AuditResult) -> str:
    """The agent-written executive summary, as paragraphs. Omitted when absent."""
    if not result.summary:
        return ""
    paras = "".join(
        f"<p>{_esc(p.strip())}</p>"
        for p in result.summary.split("\n\n")
        if p.strip()
    )
    return f"""
    <section class="panel summary-panel">
      <h2>Executive summary <span class="ai-tag">AI-written</span></h2>
      {paras}
    </section>"""


def _analysis_section(result: AuditResult) -> str:
    """Prioritization strategy and RI / Savings Plan opportunity from the analyst."""
    analysis = result.savings_analysis
    if analysis is None or not (analysis.strategy or analysis.commitment_note):
        return ""
    blocks = []
    if analysis.strategy:
        blocks.append(
            '<div class="analysis-block"><div class="analysis-label">'
            "Where to start</div>"
            f"<p>{_esc(analysis.strategy)}</p></div>"
        )
    if analysis.commitment_note:
        blocks.append(
            '<div class="analysis-block"><div class="analysis-label">'
            "Reserved Instance / Savings Plan opportunity</div>"
            f"<p>{_esc(analysis.commitment_note)}</p></div>"
        )
    return f"""
    <section class="panel">
      <h2>Prioritization &amp; commitments <span class="ai-tag">AI-written</span></h2>
      {"".join(blocks)}
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


# --- Trend line ---------------------------------------------------------------
# All layout math is deterministic plain Python; the chart is a hand-built,
# self-contained inline SVG (no external assets, matching the rest of the report).
_TREND_W = 720
_TREND_H = 240
_TREND_PAD = {"top": 20, "right": 20, "bottom": 42, "left": 64}


def _trend_section(history: list[RunRecord] | None) -> str:
    """Recoverable-dollars-over-time chart for the last N runs of this mode.

    ``history`` arrives oldest-first and already includes the current run. With
    a single run we show the point plus a note instead of a broken one-point
    line; with none we omit the panel entirely.
    """
    if not history:
        return ""

    mode = "synthetic" if history[-1].synthetic else "live"
    if len(history) == 1:
        only = history[0]
        return f"""
    <section class="panel">
      <h2>Recoverable over time <span class="period">last {len(history)} {mode} run(s)</span></h2>
      <p class="trend-note">First run recorded — {_money(only.total_monthly_savings)}
      recoverable across {only.finding_count} finding(s). Run the audit again to
      see a trend line here.</p>
    </section>"""

    svg = _trend_svg(history)
    return f"""
    <section class="panel">
      <h2>Recoverable over time <span class="period">last {len(history)} {mode} runs</span></h2>
      <div class="trend">{svg}</div>
    </section>"""


def _trend_svg(history: list[RunRecord]) -> str:
    values = [r.total_monthly_savings for r in history]
    n = len(values)
    top = _TREND_PAD["top"]
    left = _TREND_PAD["left"]
    plot_w = _TREND_W - left - _TREND_PAD["right"]
    plot_h = _TREND_H - top - _TREND_PAD["bottom"]
    baseline = top + plot_h

    # Scale to a "nice" ceiling so the top gridline is a round-ish number and a
    # flat series (all-equal values) still renders with headroom, not a zero div.
    ceiling = _nice_ceiling(max(values))

    def x_at(i: int) -> float:
        return left + (plot_w * i / (n - 1))

    def y_at(v: float) -> float:
        return top + plot_h * (1 - v / ceiling)

    # Horizontal gridlines + y-axis money labels at 0 / half / full.
    grid = []
    for frac in (0.0, 0.5, 1.0):
        y = baseline - plot_h * frac
        grid.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" '
            f'class="grid"/>'
            f'<text x="{left - 8}" y="{y + 4:.1f}" class="ylab">'
            f"{_money(ceiling * frac)}</text>"
        )

    pts = [(x_at(i), y_at(v)) for i, v in enumerate(values)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    # Filled area under the line for a little weight.
    area = (
        f"{left},{baseline:.1f} "
        + line
        + f" {left + plot_w},{baseline:.1f}"
    )

    dots = []
    for (x, y), r in zip(pts, history):
        when = r.generated_at.strftime("%Y-%m-%d %H:%M UTC")
        tip = f"{when}: {_money(r.total_monthly_savings)} · {r.finding_count} finding(s)"
        dots.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" class="dot">'
            f"<title>{_esc(tip)}</title></circle>"
        )

    # X labels: first and last only, to avoid crowding on long histories.
    first, last = history[0], history[-1]
    xlabels = (
        f'<text x="{x_at(0):.1f}" y="{baseline + 22:.1f}" class="xlab" '
        f'text-anchor="start">{_esc(first.generated_at.strftime("%m-%d %H:%M"))}</text>'
        f'<text x="{x_at(n - 1):.1f}" y="{baseline + 22:.1f}" class="xlab" '
        f'text-anchor="end">{_esc(last.generated_at.strftime("%m-%d %H:%M"))}</text>'
    )

    return f"""<svg viewBox="0 0 {_TREND_W} {_TREND_H}" class="trend-svg"
      preserveAspectRatio="xMidYMid meet" role="img"
      aria-label="Recoverable dollars per month across recent runs">
      {"".join(grid)}
      <polygon points="{area}" class="area"/>
      <polyline points="{line}" class="path"/>
      {"".join(dots)}
      {xlabels}
    </svg>"""


def _nice_ceiling(peak: float) -> float:
    """Round a peak value up to a clean axis ceiling (never zero)."""
    if peak <= 0:
        return 1.0
    import math

    magnitude = 10 ** math.floor(math.log10(peak))
    for step in (1, 2, 2.5, 5, 10):
        candidate = step * magnitude
        if candidate >= peak:
            return candidate
    return 10 * magnitude


def render_report(result: AuditResult, history: list[RunRecord] | None = None) -> str:
    """Return the full HTML document for an audit result.

    ``history`` is the recent-runs list (oldest-first, same mode as ``result``)
    used to draw the trend line; ``None`` or empty simply omits that panel.
    """
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
  .ai-tag {{ display: inline-block; font-size: .62rem; font-weight: 700;
    letter-spacing: .05em; text-transform: uppercase; color: #6d28d9;
    background: #ede9fe; padding: .12rem .4rem; border-radius: 999px;
    vertical-align: middle; }}
  .summary-panel {{ border-left: 4px solid #7c3aed; }}
  .summary-panel p {{ margin: .6rem 0; color: #1e293b; }}
  .summary-panel p:first-of-type {{ margin-top: 0; }}
  .analysis-block {{ margin-top: .9rem; }}
  .analysis-block:first-of-type {{ margin-top: 0; }}
  .analysis-label {{ font-size: .78rem; font-weight: 700; color: #6d28d9;
    text-transform: uppercase; letter-spacing: .03em; }}
  .analysis-block p {{ margin: .3rem 0 0; color: #334155; }}
  .ai-note {{ margin-top: .5rem; font-size: .84rem; color: #5b21b6;
    background: #f5f3ff; border-radius: 8px; padding: .4rem .6rem; }}
  .ai-note .ai-tag {{ margin-right: .35rem; }}
  .trend {{ width: 100%; overflow-x: auto; }}
  .trend-svg {{ width: 100%; height: auto; display: block; }}
  .trend-svg .grid {{ stroke: #e2e8f0; stroke-width: 1; }}
  .trend-svg .area {{ fill: rgba(21,128,61,.10); stroke: none; }}
  .trend-svg .path {{ fill: none; stroke: #15803d; stroke-width: 2.5;
    stroke-linejoin: round; stroke-linecap: round; }}
  .trend-svg .dot {{ fill: #15803d; stroke: #fff; stroke-width: 1.5; }}
  .trend-svg .ylab {{ fill: #94a3b8; font-size: 12px; text-anchor: end;
    font-variant-numeric: tabular-nums; }}
  .trend-svg .xlab {{ fill: #94a3b8; font-size: 12px; }}
  .trend-note {{ color: #64748b; margin: 0; }}
  footer {{ color: #94a3b8; font-size: .8rem; margin-top: 1.5rem; text-align: center; }}
  footer .stamp {{ color: #64748b; font-weight: 600; }}
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

    {_trend_section(history)}
    {_summary_section(result)}
    {_anomaly_section(result.anomalies)}
    {_analysis_section(result)}
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
      Read-only audit &middot; recommendations only — no resources were changed.<br>
      <span class="stamp">Generated {generated}</span>
    </footer>
  </div>
</body>
</html>
"""


def write_report(
    result: AuditResult,
    output_path: str | Path,
    history: list[RunRecord] | None = None,
) -> Path:
    """Render and write the report to disk; return the path written."""
    path = Path(output_path)
    path.write_text(render_report(result, history=history), encoding="utf-8")
    return path
