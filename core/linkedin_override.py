"""LLM-assisted parsing of user-confirmed LinkedIn name corrections.

This is a narrow, well-grounded use of the LLM (Groq/Ollama via core.llm) --
NOT a guess-the-brand-name generator. A human has already confirmed the
real name/firm on a live LinkedIn account and describes it in plain text
(e.g. "the firm is called The Suby Group on LinkedIn" or "he goes by Brad
Benz"); the correction is always entered while one specific firm/contact
record is already selected in the dashboard, so there's no entity-
resolution ambiguity for the LLM to get wrong -- its only job is turning
free text into a structured value.

Explicitly rejected (2026-07-17): having the LLM guess a firm's likely
public/LinkedIn name from scratch (legal name + website domain, no human
confirmation). That has no ground truth to check against and nobody would
review thousands of generated guesses at that scale -- same failure mode
this project already killed websearch.py for. This module only ever
parses a correction a human has already verified.
"""
from __future__ import annotations

from core import llm

_SYSTEM = (
    "You extract a structured correction from a user's short note about how "
    "a specific firm or person should actually be searched for on LinkedIn, "
    "because the SEC/NFA-filed name differs from the real LinkedIn name "
    "(e.g. a nickname, or a DBA/brand name instead of the legal entity name). "
    "Respond ONLY with JSON of the form "
    '{"firm_override": <string or null>, "person_override": <string or null>}. '
    "Set a field to null if the note doesn't correct that part. Extract ONLY "
    "the corrected name itself (e.g. \"The Suby Group\", \"Brad Benz\"), not "
    "the whole sentence."
)


def parse_correction(current_firm_name: str, current_person_name: str | None, user_text: str) -> dict:
    """Returns {"firm_override": str|None, "person_override": str|None, "model": str}.
    Both overrides None (with model != "none") means the LLM ran but found
    nothing to extract -- caller should treat that as a no-op, not clear any
    existing override. model == "none" means both backends failed outright."""
    prompt = (
        f"Current filed firm name: {current_firm_name!r}\n"
        f"Current contact name: {current_person_name!r}\n"
        f"User's correction: {user_text!r}"
    )
    result, model = llm.chat_json(prompt, system=_SYSTEM)
    if not result:
        return {"firm_override": None, "person_override": None, "model": model}
    return {
        "firm_override": (result.get("firm_override") or None),
        "person_override": (result.get("person_override") or None),
        "model": model,
    }
