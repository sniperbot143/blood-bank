"""Optional narration. The engine does not need this file to exist.

Claude receives the finished numbers and explains them in words. It may not
change a single one: the prompt says so, the response is never parsed back
into the signal, and `narrate()` returns a string that lives beside the
decision rather than inside it.

If there is no API key, no network, or no `anthropic` package, this returns a
deterministic local explanation built from the same reason codes. The system
keeps working; it just stops being eloquent.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import numpy as np

DEFAULT_MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 900

SYSTEM_PROMPT = """You are explaining the output of a deterministic Smart Money
Concepts trading engine to its operator.

Absolute rules:
- Every number in the JSON was computed by the engine. NEVER change one, and
  never invent a number that is not there.
- If the decision is NO_TRADE, explain why it is NO_TRADE. Do not argue for a
  trade the engine refused.
- If the sample size is small or the reliability is low, say so plainly.
- No disclaimers, no "this is not financial advice", no hedging filler. The
  operator built this engine and knows what it is.

Structure your answer with these headings, one short paragraph each:
MARKET NARRATIVE
REASON FOR THE DECISION
BULLISH ALTERNATIVE
BEARISH ALTERNATIVE
INVALIDATION
WHAT WOULD CHANGE THIS
"""


@dataclass
class Narration:
    text: str
    source: str          # "claude" or "local"
    model: str = ""
    error: str = ""

    @property
    def from_claude(self) -> bool:
        return self.source == "claude"


def is_enabled() -> bool:
    """Explicit opt-in AND a key. Either one alone is not enough."""
    enabled = os.getenv("ENABLE_CLAUDE", "false").strip().lower() in {"1", "true", "yes", "on"}
    return enabled and bool(os.getenv("ANTHROPIC_API_KEY", "").strip())


def narrate(signal, *, model: str = DEFAULT_MODEL, timeout: float = 30.0) -> Narration:
    """Explain a signal. Falls back to a local explanation, never to silence."""
    if not is_enabled():
        return Narration(text=local_narration(signal), source="local",
                         error="disabled (set ENABLE_CLAUDE=true and ANTHROPIC_API_KEY)")

    try:
        import anthropic
    except ImportError:
        return Narration(text=local_narration(signal), source="local",
                         error="anthropic package not installed")

    payload = json.dumps(signal.as_dict(), indent=2, default=str)
    try:
        client = anthropic.Anthropic(timeout=timeout)
        response = client.messages.create(
            model=model, max_tokens=MAX_TOKENS, system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content":
                       f"Explain this signal.\n\n```json\n{payload}\n```"}],
        )
        text = "".join(block.text for block in response.content
                       if getattr(block, "type", "") == "text")
        return Narration(text=text.strip(), source="claude", model=model)
    except Exception as exc:      # network, auth, rate limit -- all non-fatal
        return Narration(text=local_narration(signal), source="local", error=str(exc))


def local_narration(signal) -> str:
    """The deterministic fallback, built from the engine's own reason codes."""
    candidate = signal.candidate
    probability = signal.probability
    levels = candidate.levels
    values = candidate.features.values

    probability_text = (f"{probability.probability:.1%} from {probability.sample_size:,} "
                        f"comparable setups (tier {probability.similarity_tier}, "
                        f"reliability {probability.reliability.value})"
                        if np.isfinite(probability.probability)
                        else "no usable historical sample")

    lines = [
        "MARKET NARRATIVE",
        f"{candidate.symbol} on {candidate.timeframe} at "
        f"{candidate.signal_time:%Y-%m-%d %H:%M} UTC. Bias is "
        f"{values.get('bias')}, regime {values.get('regime_key')}, price in the "
        f"{values.get('pd_zone')} half of the dealing range during "
        f"{values.get('session')}. The structural event on the tape is "
        f"{values.get('structure_event')} and the last liquidity event was "
        f"{values.get('liquidity_event')}.",
        "",
        "REASON FOR THE DECISION",
        f"The engine returned {signal.decision.value}. Confluence scored "
        f"{signal.score.rounded}/100 and the historical estimate is {probability_text}. "
        f"Reason codes: {', '.join(signal.reason_codes) or 'none'}.",
        "",
        "BULLISH ALTERNATIVE",
        "Price reclaims the point of interest and closes above the recent "
        "structural high, which would turn the break-confirmed bias bullish.",
        "",
        "BEARISH ALTERNATIVE",
        "Price rejects the point of interest and closes below the protected low, "
        "which would put the bias back to RANGE and then bearish on displacement.",
        "",
        "INVALIDATION",
        (f"{candidate.timeframe} close beyond {levels.stop_loss:.5f}."
         if levels.is_valid else "No valid geometry was produced for this bar."),
        "",
        "WHAT WOULD CHANGE THIS",
        "A larger comparable sample, a higher confluence score, or a better "
        "reward-to-risk on the same structure. The thresholds are in "
        "config/decision_config.py.",
    ]
    return "\n".join(lines)
