"""Registry of known providers: how they map to collector classes, their
configurable fields, and env-defaults.

Both the admin cabinet (``app/admin.py``) and the collector
(``app/collector.py``) read from here so forms and runtime stay in sync.
"""
from __future__ import annotations

from typing import Any

# Field spec: ``secret`` fields are write-only through the admin API — the
# response only carries ``"is_set": true`` so keys never leak to the browser.
PROVIDER_DEFS: dict[str, dict[str, Any]] = {
    "zai": {
        "label": "Z.AI Coding Plan",
        "needs_key": True,
        "fields": [
            {"name": "api_key", "label": "API key", "secret": True},
            {"name": "base_url", "label": "Base URL", "secret": False, "placeholder": "https://api.z.ai"},
        ],
    },
    "minimax": {
        "label": "MiniMax Token/Coding Plan",
        "needs_key": True,
        "fields": [
            {"name": "api_key", "label": "API key", "secret": True},
            {"name": "base_url", "label": "Base URL", "secret": False, "placeholder": "https://www.minimax.io"},
        ],
    },
    "deepseek": {
        "label": "DeepSeek API (balance)",
        "needs_key": True,
        "fields": [
            {"name": "api_key", "label": "API key", "secret": True},
            {"name": "base_url", "label": "Base URL", "secret": False, "placeholder": "https://api.deepseek.com"},
        ],
    },
    "openrouter": {
        "label": "OpenRouter",
        "needs_key": True,
        "fields": [
            {"name": "api_key", "label": "API key", "secret": True},
            {"name": "management_key", "label": "Management key (optional)", "secret": True},
            {"name": "base_url", "label": "Base URL", "secret": False, "placeholder": "https://openrouter.ai"},
        ],
    },
    "codex": {
        "label": "OpenAI Codex (ChatGPT plan)",
        "needs_key": False,
        "fields": [
            {"name": "auth_path", "label": "Path to auth.json", "secret": False,
             "placeholder": "~/.codex/auth.json"},
            {"name": "base_url", "label": "Base URL", "secret": False,
             "placeholder": "https://chatgpt.com/backend-api"},
        ],
    },
}

DEFAULT_ENABLED = {slug: True for slug in PROVIDER_DEFS}


def mask_config(slug: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Render field values for API responses with secrets masked."""
    out: list[dict[str, Any]] = []
    for field in PROVIDER_DEFS.get(slug, {}).get("fields", []):
        name = field["name"]
        value = config.get(name)
        entry: dict[str, Any] = {
            "name": name,
            "label": field.get("label", name),
            "secret": bool(field.get("secret")),
            "is_set": bool(value),
        }
        if not field.get("secret"):
            entry["value"] = value or ""
            if field.get("placeholder"):
                entry["placeholder"] = field["placeholder"]
        elif field.get("placeholder"):
            entry["placeholder"] = field["placeholder"]
        out.append(entry)
    return out
