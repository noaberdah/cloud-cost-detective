"""The in-code agent layer (Stage 5).

These "agents" are Anthropic API calls made *inside* the program — not Claude
Code subagents. They add judgment on top of the deterministic detectors:
down-weighting likely false positives, ranking by effort, and writing the
executive summary. Every agent degrades to a no-op when the API is unavailable,
so ``python -m costdetective audit`` keeps working with no ``ANTHROPIC_API_KEY``.
"""
