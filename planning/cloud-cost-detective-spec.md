# Cloud Cost Detective — Project Spec

**One-liner:** A multi-agent AWS FinOps tool that scans an account for wasted spend, calculates the dollar impact, and produces a prioritized, human-reviewed savings report.

**Built with:** Python + Claude Code (multi-agent). Read-only and safe by design.

---

## The problem
Teams spin up cloud resources and forget them. Idle servers, orphaned disks, and unused IPs keep billing 24/7. Finding and killing that waste is a real job category (FinOps). This tool automates the detection and the math.

## What it does
1. One command (`costdetective audit`) connects to AWS with **read-only** access.
2. Specialized detectors hunt for known waste patterns.
3. Each finding gets a **dollar/month** estimate from real pricing data.
4. Agents reason over the raw findings — filter false positives, rank by savings vs. effort.
5. It outputs a clean **HTML report**: total recoverable, fixes ordered easiest-first.
6. It never deletes anything — it recommends; the human decides.

## Architecture
Two parts. The agents live **inside the program** (via the Anthropic API), so it's a genuine multi-agent application no matter who runs it or how — not dependent on Claude Code being present.

- **Deterministic core (plain Python):** detector modules, cost math, AWS clients via `boto3`. No AI — exact, testable, cheap. This is where the EBS/EC2/pricing logic lives.
- **Agent layer, in-code via the Anthropic API (the multi-agent story):** the *judgment* steps call Claude through the `anthropic` library as functions in the program:
  - `waste-hunter` — reasons over raw findings, down-ranks false positives using context (e.g. a volume tagged `backup`).
  - `savings-analyst` — ranks findings by $/month vs. risk; narrates Reserved Instance / Savings Plan gaps.
  - `reporter` — assembles the final HTML report.
  - Rule of thumb: deterministic facts → plain Python; judgment → API call.
- **Optional Claude Code layer (dev-experience bonus):** a `/audit` command, `CLAUDE.md`, and skills that orchestrate the tool from the terminal. Nice to have, not load-bearing — the agents above work without it.

## Detectors (checks)
- Unattached EBS volumes
- Idle EC2 (low CPU over 14 days)
- **EC2 rightsizing** (oversized vCPU/RAM → recommend smaller type; CPU confident, memory flagged unverified unless CloudWatch Agent is installed)
- Idle load balancers
- Old / orphaned snapshots
- Overprovisioned RDS
- Unused Elastic IPs
- gp2 → gp3 migration candidates

## In scope (what makes it *good*, not just a checklist)
- **Accurate cost math** via AWS Pricing / Cost Explorer — credibility over feature count.
- **Anomaly / spike detection** — "S3 spend jumped 40% on the 14th."
- **Tag / owner awareness** — group waste by team or project tag.
- **Weekly trend tracking** — store run snapshots, show "$X cut over a month."
- **Provider-agnostic schema** — AWS implemented; Azure/GCP pluggable by design (not built).

## Out of scope (deliberately)
- Multi-cloud implementation, ML forecasting (a linear trend line is enough), Kubernetes, carbon footprint. A finished focused tool beats a half-built impressive one.

## Tech stack
- **Core:** Python 3.10+, `boto3`
- **API/CLI:** FastAPI optional; CLI first
- **Storage:** SQLite to start (Postgres only if adding the persistence story)
- **Agents:** `anthropic` Python library (Claude API) for in-code reasoning; Claude Code (`/audit`, skills, `CLAUDE.md`) optional on top
- **Output:** HTML report; **synthetic mode** generates a full sample report with no AWS account needed (always have something to screenshot)
- **Packaging:** Docker (skip K8s)
- **Default region:** `il-central-1`

## Build phases
0. **Setup** — Python venv, AWS read-only credential, repo + git.
1. **Run** — synthetic mode end-to-end, then real account with EBS + EC2 detectors.
2. **More detectors** — fill the remaining checks.
3. **Real spend** — wire Cost Explorer; add anomaly detection + tag grouping.
4. **Agent layer** — wire the `anthropic` API into the judgment steps (waste-hunter, savings-analyst, reporter); optionally add a Claude Code `/audit` command on top.
5. **Report + trend** — polished HTML, weekly snapshot tracking.
6. **Polish** — README with architecture diagram, sample report, tests, push to GitHub.

## CV story
> Built a multi-agent FinOps tool in Python that audits AWS accounts for cost waste, uses Claude (via the API) to reason over findings and filter false positives, computes per-resource savings from live pricing data, and produces prioritized remediation reports — read-only with human-in-the-loop approval.

## To start
1. AWS read-only credentials; `aws sts get-caller-identity` passes.
2. Python 3.10+ with a venv, `boto3`, and `anthropic`.
3. An Anthropic API key (separate from any Claude subscription; ~cents per run).
4. The scaffold + this spec. (Claude Code optional, for the `/audit` layer only.)
