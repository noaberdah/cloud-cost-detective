# Cloud Cost Detective — PLAN

**How to use this file:** Build top to bottom, one stage at a time. Do not start a
stage until the previous stage's "Done when" boxes are all checked. After finishing
a stage, STOP, let the human test it, then continue. Tick boxes as you go.

## Guardrails (apply to every stage)
- AWS access is **read-only**. Never create, modify, or delete AWS resources.
- **Deterministic facts** (resource lists, cost math) = plain Python.
  **Judgment** (false positives, ranking, written summary) = Anthropic API.
  Never use the API for arithmetic or to fetch facts.
- Never commit secrets or `.venv`. AWS keys live in `aws configure`; the Anthropic
  key lives in the `ANTHROPIC_API_KEY` env var. Nowhere else.
- Default region `us-east-1` (Cost Explorer + Pricing are served there). Scan other
  regions by passing `--region`.
- The tool must stay runnable with plain `python -m costdetective audit` at every stage —
  the Anthropic API and Claude Code are enhancements, never hard requirements.

---

## Stage 0 — Environment  ✅ done
- [x] Python 3.10+ venv; `boto3` and `anthropic` installed
- [x] AWS read-only key works (`aws sts get-caller-identity` passes)
- [x] `ANTHROPIC_API_KEY` set in the environment

## Stage 1 — Skeleton + synthetic report
**Goal:** watch the whole pipeline run on fake data, with no AWS calls.
- [x] Package layout: `costdetective/` with `__init__.py`, `models.py` (Finding, Severity),
      `scan.py`, `cli.py`, `report.py`, and a `detectors/` folder
- [x] `sample_data/synthetic.py` — generates ~5 realistic fake findings
- [x] `cli.py` — an `audit` command supporting `--region`, `--synthetic`, `--output`
- [x] `report.py` — render findings into a styled `report.html`
- [x] `CLAUDE.md` at repo root (coding conventions + the guardrails above)

**Done when:**
- [x] `python -m costdetective audit --synthetic` prints a ranked table and writes `report.html`
- [x] Opening `report.html` shows the styled report

## Stage 2 — Real AWS detectors
**Goal:** scan the real account for the two anchor checks.
- [x] `detectors/ebs_unattached.py` — EBS volumes in `available` state + monthly cost
- [x] `detectors/ec2_idle.py` — running instances; 14-day CloudWatch CPU; flag low peak
- [x] `pricing.py` — fallback price map (Pricing API gets wired in Stage 4)
- [x] `scan.py` runs the real detectors when `--synthetic` is off

**Done when:**
- [x] `python -m costdetective audit` runs against the account and produces a report (even if empty)
- [x] A single broken detector logs a warning but does not crash the audit

## Stage 3 — More detectors
- [x] Unused Elastic IPs
- [x] gp2 → gp3 migration candidates
- [x] Old / orphaned snapshots
- [x] Idle load balancers
- [x] Overprovisioned RDS
- [x] EC2 rightsizing (recommend a smaller type from CPU; flag memory as unverified)

**Done when:**
- [x] Each detector is its own registered module with a unit test against synthetic input

## Stage 4 — Real spend + anomaly + tags
- [ ] Wire Cost Explorer (`ce:GetCostAndUsage`) for real monthly spend by service
- [ ] Wire the Pricing API for accurate per-resource cost (replace the fallback map)
- [ ] Anomaly detection: flag week-over-week spend jumps (e.g. > 30%)
- [ ] Group findings by owner / team tag

**Done when:**
- [ ] Report shows real total spend + recoverable, any spend spikes, and per-tag grouping

## Stage 5 — Agent layer (in-code, Anthropic API)
**Goal:** the multi-agent reasoning, living inside the program.
- [ ] `agents/waste_hunter.py` — API call: review findings, lower confidence on likely
      false positives, with a one-line reason each
- [ ] `agents/savings_analyst.py` — API call: rank by $ vs. effort; narrate RI / Savings Plan gaps
- [ ] `agents/reporter.py` — API call: write the executive-summary prose for the report
- [ ] Wire the agents into the pipeline after detection; deterministic math stays in Python

**Done when:**
- [ ] Findings carry an AI confidence + justification, plus a ranked written summary
- [ ] Running without `ANTHROPIC_API_KEY` still works (agents degrade gracefully)

## Stage 6 — Trend tracking + polished report
- [ ] Persist each run (SQLite, or timestamped JSON in `runs/`)
- [ ] Trend line: recoverable $ over the last N runs
- [ ] Polish the HTML — severity colors, metric cards, generated timestamp

**Done when:**
- [ ] Two runs produce a visible trend in the report

## Stage 7 — Polish for CV (+ optional Claude Code layer)
- [ ] `README.md`: what it is, the CV bullet, setup steps, a screenshot, an architecture diagram
- [ ] Tests pass; add `requirements.txt`
- [ ] Push to GitHub (public)
- [ ] Optional: `.claude/commands/audit.md` + skills so `/audit` drives the tool from Claude Code
- [ ] Optional: a `Dockerfile`

**Done when:**
- [ ] A stranger can read the README and run the synthetic demo in under 5 minutes

## Stage 8 — Cross-account scanning (OPTIONAL stretch, add anytime)
**Goal:** scan an account other than your own (e.g. a friend's), with permission, read-only.
This only changes how the boto3 session is created; detectors stay untouched.
- [ ] Cheap version: add a `--profile` flag so the tool can target any account
      configured via `aws configure --profile <name>`
- [ ] Impressive version: support `sts:AssumeRole` — assume a read-only role in the
      target account and scan with the returned temporary credentials
- [ ] README line: "secure cross-account scanning via STS AssumeRole (temporary,
      revocable, read-only)"
**Done when:**
- [ ] `python -m costdetective audit --profile friend` (or via an assumed role) scans a
      second account and produces a report
- [ ] No long-lived credentials from the other account are stored in the repo