"""Small persisted dashboard settings (survives restarts) — currently just the
LLM-features on/off toggle. Not for prospect data, just UI/feature switches."""
from __future__ import annotations
import json

from core import config

_SETTINGS_PATH = config.BASE_DIR / "dashboard_settings.json"
_DEFAULTS = {
    "llm_enabled": False,  # off by default until Groq/Ollama is set up and confirmed working
}


def _load() -> dict:
    if _SETTINGS_PATH.exists():
        try:
            return {**_DEFAULTS, **json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))}
        except json.JSONDecodeError:
            pass
    return dict(_DEFAULTS)


def get(key: str):
    return _load().get(key, _DEFAULTS.get(key))


def set(key: str, value) -> None:
    data = _load()
    data[key] = value
    _SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
