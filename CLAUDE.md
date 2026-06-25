# Cloud Cost Detective — working notes for Claude

A multi-agent AWS FinOps tool: it scans an account (read-only) for wasted spend,
computes the dollar impact, and produces a prioritized, human-reviewed savings
report. See `planning/cloud-cost-detective-spec.md` (what & why) and
`planning/PLAN.md` (the staged build plan — the source of truth for order of work).

## How we build
- **Stage by stage, in PLAN.md order. One stage at a time.** Finish a stage, tick
  its boxes in `planning/PLAN.md`, then STOP and let the human test before moving on.
- Keep `python -m costdetective audit` runnable at every stage. The Anthropic API
  and Claude Code are enhancements, never hard requirements.

## Guardrails (apply to every change)
- **AWS is read-only.** Never create, modify, or delete AWS resources.
- **Deterministic facts → plain Python** (resource lists, cost math). **Judgment →
  Anthropic API** (false positives, ranking, written summary). Never use the API
  for arithmetic or to fetch facts.
- **Never commit secrets or `.venv`.** AWS keys live in `aws configure`; the
  Anthropic key lives in the `ANTHROPIC_API_KEY` env var. Nowhere else.
- **Default region `us-east-1`** (Cost Explorer + Pricing are served there). Scan
  other regions with `--region`.
- The "agents" are Anthropic API calls *inside the Python program* (Stage 5), not
  Claude Code subagents. The optional Claude Code `/audit` layer is Stage 7 only —
  do not create a `.claude/` command/skill layer before then.

## Conventions
- Python 3.10+. Standard library + `boto3` + `anthropic` only unless we agree to add a dep.
- Each detector is its own module in `costdetective/detectors/`, returning `Finding`s.
- Generated output (`report.html`, `findings*.json`) is gitignored, not committed.

## Project layout
```
costdetective/
  __main__.py        # enables `python -m costdetective`
  cli.py             # argparse: the `audit` command
  models.py          # Finding, Severity
  scan.py            # orchestrates detectors -> AuditResult
  report.py          # renders findings into report.html
  detectors/         # one module per waste check (Stage 2+)
  sample_data/
    synthetic.py     # fake-but-realistic findings for `--synthetic`
planning/            # spec + PLAN
```
