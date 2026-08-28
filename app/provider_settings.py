"""Resolve per-provider runtime config: DB overrides layered over .env defaults."""
from __future__ import annotations

from typing import Any

from app.provider_registry import PROVIDER_DEFS


def env_config(slug: str, settings) -> dict[str, Any]:
    """Env-derived defaults so the cabinet starts pre-filled from .env."""
    return {
        "zai": {"api_key": settings.zai_api_key, "base_url": settings.zai_base_url},
        "minimax": {
            "api_key": settings.minimax_api_key,
            "base_url": settings.minimax_base_url,
        },
        "deepseek": {
            "api_key": settings.deepseek_api_key,
            "base_url": settings.deepseek_base_url,
        },
        "openrouter": {
            "api_key": settings.openrouter_api_key,
            "management_key": settings.openrouter_management_key,
            "base_url": settings.openrouter_base_url,
        },
        "codex": {"auth_path": "", "base_url": settings.codex_base_url},
    }.get(slug, {})


def merge_provider_configs(store, settings) -> dict[str, dict[str, Any]]:
    """Return ``{slug: {field: value, "_enabled": bool}}`` for all known providers.

    Priority: DB override -> .env -> empty string.
    """
    overrides = {c["slug"]: c for c in store.list_provider_configs()}
    merged: dict[str, dict[str, Any]] = {}
    for slug in PROVIDER_DEFS:
        cfg = dict(env_config(slug, settings))
        if slug in overrides:
            cfg.update(overrides[slug]["config"])
            enabled = overrides[slug]["enabled"]
        else:
            enabled = True
        cfg["_enabled"] = enabled
        merged[slug] = cfg
    return merged
