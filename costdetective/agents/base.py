"""Shared plumbing for the agent layer: one place that talks to the API.

Design rules (see CLAUDE.md):
- **Judgment only.** The API reasons *over* deterministic facts; it never does
  arithmetic or invents a :class:`~costdetective.models.Finding`'s fields.
- **Fail open.** A missing key, a missing SDK, or any API error is logged and
  returns ``None`` — the caller keeps its deterministic result and the audit
  never aborts. This is what lets the tool run without ``ANTHROPIC_API_KEY``.

Every agent goes through :func:`call_text` (prose) or :func:`call_json`
(schema-validated judgment), so the graceful-degradation and error handling
live in exactly one module.
"""

from __future__ import annotations

import json
import logging
import os

log = logging.getLogger(__name__)

# Pinned model for every agent. Haiku 4.5 is the fast, cheap tier — the right
# fit for these bounded judgment calls. Note: Haiku 4.5 does NOT support the
# ``effort`` or adaptive-thinking parameters, so requests stay plain.
MODEL = "claude-haiku-4-5-20251001"

# These calls return short JSON or a few paragraphs; keep the ceiling modest.
DEFAULT_MAX_TOKENS = 1024

# One client for the whole run. ``_disabled`` latches on the first failure so we
# don't retry a doomed import/auth for every finding.
_client = None
_disabled = False


def agent_layer_available() -> bool:
    """Cheap pre-check: do we have both a key and the SDK?

    Lets a caller skip assembling a prompt (and log a single "AI layer off"
    line) instead of calling into each agent only to get ``None`` back.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def _get_client():
    """Return a cached Anthropic client, or ``None`` if the layer is unavailable."""
    global _client, _disabled
    if _client is not None or _disabled:
        return _client
    if not os.environ.get("ANTHROPIC_API_KEY"):
        _disabled = True
        return None
    try:
        import anthropic

        _client = anthropic.Anthropic()
    except Exception as exc:  # noqa: BLE001 - fail open, disable the layer
        log.warning("Anthropic client unavailable: %s — agent layer disabled", exc)
        _disabled = True
    return _client


def call_text(
    system: str, user: str, max_tokens: int = DEFAULT_MAX_TOKENS
) -> str | None:
    """Run one judgment call and return its text, or ``None`` if unavailable.

    Never raises: a missing key/SDK or any API error is logged and returns
    ``None`` so the caller falls back to its deterministic result.
    """
    client = _get_client()
    if client is None:
        return None
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    except Exception as exc:  # noqa: BLE001 - fail open, never abort the audit
        log.warning("agent call failed: %s — using deterministic result", exc)
        return None
    return _first_text(response)


def call_json(
    system: str,
    user: str,
    schema: dict,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> dict | None:
    """Run a judgment call constrained to ``schema`` and return the parsed dict.

    Uses structured outputs so the model's reply is guaranteed valid JSON
    matching ``schema`` (Haiku 4.5 supports ``output_config.format``). Returns
    ``None`` on any failure — unavailable layer, API error, or unparseable body.
    """
    client = _get_client()
    if client is None:
        return None
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
    except Exception as exc:  # noqa: BLE001 - fail open, never abort the audit
        log.warning("agent JSON call failed: %s — using deterministic result", exc)
        return None
    text = _first_text(response)
    if text is None:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError) as exc:  # structured output should prevent this
        log.warning("agent returned non-JSON output: %s", exc)
        return None


def _first_text(response) -> str | None:
    """Pull the first text block out of a Messages response."""
    for block in response.content:
        if block.type == "text":
            return block.text
    return None
