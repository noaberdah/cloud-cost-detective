# 🔎 Cloud Cost Detective

A read-only AWS FinOps auditor that scans an account for wasted spend, prices the
waste in real dollars, and produces a prioritized, human-reviewed savings report.

It pairs **deterministic Python** (resource discovery, cost math) with a small
**in-code Anthropic agent layer** (judgment: false-positive review, ROI ranking,
and a written executive summary) — so the numbers are always trustworthy and the
narrative is always readable.

> **CV bullet:** Built a multi-agent AWS cost-optimization tool that scans an
> account read-only, prices wasted spend via the Cost Explorer and Pricing APIs,
> and uses the Anthropic API to down-weight false positives, rank fixes by ROI,
> and write an executive summary — with all cost arithmetic kept deterministic in
> Python.

---

## What it does

- **Scans read-only** for common waste across 8 detectors (see below) and prices
  each finding with the live AWS Pricing API (falling back to a static map).
- **Pulls real spend** from Cost Explorer — 30-day totals by service — and flags
  **week-over-week anomalies** (e.g. spend jumps > 30%).
- **Groups findings** by owner / team tag so waste has an accountable owner.
- **Adds AI judgment** (optional): lowers confidence on likely false positives,
  ranks by dollars-vs-effort, narrates Reserved Instance / Savings Plan gaps, and
  writes the report's executive summary.
- **Outputs a styled, self-contained `report.html`** — no external assets, opens
  anywhere.

### Detectors

| Detector | Flags |
|---|---|
| `ebs_unattached` | EBS volumes sitting in `available` state |
| `ebs_gp2_to_gp3` | gp2 volumes that could move to gp3 at lower cost |
| `snapshots_orphaned` | Old snapshots whose source volume is gone |
| `ec2_idle` | Running instances with near-zero 14-day CPU |
| `ec2_rightsize` | Instances that could drop to a smaller type |
| `eip_unused` | Elastic IPs not associated with anything |
| `lb_idle` | Load balancers with no healthy targets / traffic |
| `rds_overprovisioned` | Over-sized RDS instances |

---

## Design: deterministic facts, AI judgment

The core guardrail of the project:

- **Deterministic → plain Python.** Resource lists and every dollar figure are
  computed in code. The API never does arithmetic or invents a fact.
- **Judgment → Anthropic API.** Which findings are false positives, how to rank
  them, and how to summarize them are the only things delegated to a model.
- **AWS is read-only.** The tool never creates, modifies, or deletes anything.
- **The API is an enhancement, never a requirement.** With no `ANTHROPIC_API_KEY`
  set, the audit runs exactly the same — it just omits the AI sections.

The three agents (Anthropic API calls made *inside* the program, using
`claude-haiku-4-5`) live in [`costdetective/agents/`](costdetective/agents/):

| Agent | Role |
|---|---|
| `waste_hunter` | Reviews findings; lowers confidence on likely false positives with a one-line reason |
| `savings_analyst` | Ranks by $ vs. effort; narrates RI / Savings Plan opportunities |
| `reporter` | Writes the executive-summary prose |

---

## Setup

Requires **Python 3.10+**.

```bash
# 1. Clone and enter
git clone https://github.com/noaberdah/cloud-cost-detective.git
cd cloud-cost-detective

# 2. Create a virtualenv and install deps
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install boto3 anthropic python-dotenv

# 3. AWS credentials (read-only IAM user is plenty)
aws configure

# 4. (Optional) Anthropic API key for the agent layer
#    Put it in a .env file (gitignored) or export it:
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
```

---

## Usage

```bash
# Try it with no AWS calls — built-in sample data:
python -m costdetective audit --synthetic

# Scan your real account (default region us-east-1):
python -m costdetective audit

# Another region, custom output path:
python -m costdetective audit --region eu-west-1 --output report.html
```

It prints a ranked table to the console and writes `report.html`. Open that file
in a browser for the full report — metric cards, spend breakdown, anomalies,
per-owner grouping, and (if the API key is set) the AI executive summary and
per-finding confidence notes.

---

## Project layout

```
costdetective/
  __main__.py        # enables `python -m costdetective`
  cli.py             # the `audit` command
  models.py          # Finding, Severity, ranking, tag grouping
  scan.py            # orchestrates detectors + agent layer -> AuditResult
  report.py          # renders findings into report.html
  pricing.py         # fallback price map
  pricing_live.py    # live AWS Pricing API client
  cost_explorer.py   # real spend + anomaly detection
  detectors/         # one module per waste check
  agents/            # in-code Anthropic API layer (Stage 5)
  sample_data/       # synthetic findings for --synthetic
planning/            # spec + staged build plan
```

---

## Guardrails

- **AWS access is strictly read-only** — no create/modify/delete calls anywhere.
- **No secrets in the repo.** AWS keys live in `aws configure`; the Anthropic key
  lives in `ANTHROPIC_API_KEY` (via `.env`, which is gitignored). Generated output
  (`report.html`, `findings*.json`) is gitignored too.
- **Default region `us-east-1`** (where Cost Explorer + Pricing are served); scan
  elsewhere with `--region`.

---

## Roadmap

- [ ] Trend tracking — persist each run and chart recoverable $ over time
- [ ] Polished report tweaks + generated screenshots
- [ ] Optional Claude Code `/audit` command layer
- [ ] Optional cross-account scanning via STS AssumeRole (read-only)

---

*Read-only audit — recommendations only. No AWS resources are ever changed.*
