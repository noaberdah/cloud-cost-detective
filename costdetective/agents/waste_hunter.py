"""Waste-hunter agent: down-weight findings that look like false positives.

The detectors are deliberately trigger-happy — better to surface a borderline
volume than miss real waste. This agent adds the judgment a human reviewer
would: an unattached volume tagged ``backup=true``, an "idle" box that's clearly
a warm standby, a snapshot that smells like a compliance hold. It lowers the
finding's ``confidence`` and records a one-line reason; it never touches the
dollar math or any factual field.

All findings go in one batched call (cheaper and lets the model weigh them
against each other). If the agent layer is unavailable, findings pass through
untouched with their deterministic confidence.
"""

from __future__ import annotations

import logging

from costdetective.agents import base
from costdetective.models import Finding

log = logging.getLogger(__name__)

_SYSTEM = """\
You are a senior FinOps reviewer auditing a list of automated AWS cost-waste
findings. Each was produced by a deterministic detector that is intentionally
aggressive, so some are false positives (e.g. a "detached" volume that is a
deliberate backup, an "idle" instance that is a warm standby or DR host, a
snapshot kept for compliance).

For each finding, judge how likely it is to be GENUINE, remediable waste and
return a confidence from 0.0 to 1.0:
  - ~0.9-1.0: almost certainly waste, safe to act on
  - ~0.4-0.7: plausible but needs a human to check the caveat you name
  - ~0.0-0.3: likely a false positive; explain why in the reason

Give a single short reason (one sentence) for each. Base your judgment only on
the facts provided — tags, ages, metrics. Do NOT recompute or question the
dollar amounts; those are already correct. Return one entry per finding, keyed
by its index."""

# Numerical/length bounds aren't enforceable in structured-output schemas, so we
# clamp confidence to [0, 1] in Python after parsing.
_SCHEMA = {
    "type": "object",
    "properties": {
        "assessments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["index", "confidence", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["assessments"],
    "additionalProperties": False,
}


def review(findings: list[Finding]) -> list[Finding]:
    """Adjust each finding's ``confidence`` in place and return the same list.

    On any failure (no key, API error, unparseable reply) the findings are
    returned unchanged — the deterministic result always stands.
    """
    if not findings:
        return findings

    payload = _describe(findings)
    user = (
        "Review these findings and return a confidence + one-line reason for "
        "each, keyed by index:\n\n" + payload
    )
    result = base.call_json(_SYSTEM, user, _SCHEMA, max_tokens=1500)
    if result is None:
        log.info("waste_hunter: agent layer unavailable — confidences unchanged")
        return findings

    _apply(findings, result.get("assessments", []))
    return findings


def _describe(findings: list[Finding]) -> str:
    """Render findings as compact, numbered facts for the model to weigh."""
    lines = []
    for i, f in enumerate(findings):
        lines.append(
            f"[{i}] detector={f.detector} type={f.resource_type} "
            f"region={f.region} monthly_savings=${f.monthly_savings:.2f}\n"
            f"    summary: {f.summary}\n"
            f"    recommendation: {f.recommendation}\n"
            f"    details: {f.details}"
        )
    return "\n".join(lines)


def _apply(findings: list[Finding], assessments: list[dict]) -> None:
    """Apply the model's per-index judgments, only ever LOWERING confidence."""
    adjusted = 0
    for a in assessments:
        idx = a.get("index")
        if not isinstance(idx, int) or not (0 <= idx < len(findings)):
            continue
        f = findings[idx]
        raw = a.get("confidence")
        if isinstance(raw, (int, float)):
            new_conf = max(0.0, min(1.0, float(raw)))
            # Monotonic: the agent can lower confidence but never inflate it
            # above the detector's deterministic value.
            f.confidence = min(f.confidence, new_conf)
        reason = a.get("reason")
        if isinstance(reason, str) and reason.strip():
            f.details["ai_confidence_reason"] = reason.strip()
        adjusted += 1
    log.info("waste_hunter: reviewed %d finding(s)", adjusted)
