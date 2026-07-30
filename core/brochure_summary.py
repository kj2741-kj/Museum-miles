"""One-sentence LLM summary of a firm's strategy/client base/fees from its
own SEC ADV Part 2 brochure text. Reuses whatever text iapd.lookup_brochure()
already fetched for named-person discovery, no extra network call."""
from __future__ import annotations

from core import llm

_MAX_CHARS = 6000  # strategy/client/fee content is almost always in the first few sections

_SYSTEM_PROMPT = (
    "You summarize SEC investment adviser brochures for a prospecting team. "
    "Write exactly one sentence, under 40 words, covering the firm's "
    "investment strategy and client base if stated. Plain, factual, no "
    "marketing language. If the brochure doesn't clearly state a strategy "
    "or client base, say so briefly instead of guessing."
)


def summarize(brochure_text: str) -> str | None:
    """Returns a one-sentence summary, or None if the LLM call fails."""
    if not brochure_text or not brochure_text.strip():
        return None
    excerpt = brochure_text[:_MAX_CHARS]
    response, model = llm.chat(excerpt, system=_SYSTEM_PROMPT)
    if model == "none" or not response:
        return None
    return response.strip()
