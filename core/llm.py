"""
Multi-LLM routing: Groq (cloud, free tier, fast) as primary, local Ollama
(llama3.2:3b) as fallback when Groq is unavailable (no key, rate-limited,
network error). Same pattern as career-monitor's llm_assistant.py.
"""
from __future__ import annotations
import json
import sys
import traceback

import requests

from core import config

GROQ_MODEL = "llama-3.3-70b-versatile"
OLLAMA_MODEL = "llama3.2:3b"
OLLAMA_URL = "http://localhost:11434/api/chat"


def _groq_key() -> str | None:
    if config.GROQ_KEY_FILE.exists():
        key = config.GROQ_KEY_FILE.read_text(encoding="utf-8").strip()
        return key or None
    return None


def _call_groq(messages: list[dict], json_mode: bool) -> str | None:
    key = _groq_key()
    if not key:
        return None
    try:
        from groq import Groq
        client = Groq(api_key=key)
        kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
        resp = client.chat.completions.create(
            model=GROQ_MODEL, messages=messages, temperature=0.2, timeout=20, **kwargs
        )
        return resp.choices[0].message.content
    except Exception:
        # Real diagnostic need (2026-07-17): this used to swallow the
        # exception silently, so a real failure (bad key, rate limit,
        # network block) looked identical to "no key configured" -- printed
        # to stderr (captured in logs/sec/streamlit_stderr.log when run via
        # Start-Process) so the actual cause is visible on the next failure.
        print("[llm] Groq call failed:", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return None


def _call_ollama(messages: list[dict], json_mode: bool) -> str | None:
    try:
        payload = {"model": OLLAMA_MODEL, "messages": messages, "stream": False}
        if json_mode:
            payload["format"] = "json"
        resp = requests.post(OLLAMA_URL, json=payload, timeout=45)  # first call after idle can be slow to load the model
        resp.raise_for_status()
        return resp.json()["message"]["content"]
    except Exception:
        print("[llm] Ollama call failed (expected if Ollama isn't running locally):", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return None


def chat(prompt: str, system: str | None = None, json_mode: bool = False) -> tuple[str | None, str]:
    """Send a prompt to Groq, falling back to local Ollama. Returns (response, model_used).
    response is None if both backends failed."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    result = _call_groq(messages, json_mode)
    if result is not None:
        return result, "groq"

    result = _call_ollama(messages, json_mode)
    if result is not None:
        return result, "ollama"

    return None, "none"


def chat_json(prompt: str, system: str | None = None) -> tuple[dict | None, str]:
    """Like chat(), but parses the response as JSON. Returns (dict, model_used) or (None, model)."""
    raw, model = chat(prompt, system=system, json_mode=True)
    if raw is None:
        return None, model
    try:
        return json.loads(raw), model
    except json.JSONDecodeError:
        return None, model
