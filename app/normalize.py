from __future__ import annotations

import json
import re
from typing import Any

from app.models import ProviderSnapshot, QuotaWindow

_REDACT_RE = re.compile(r"(api[-_]?key|authorization|token|secret|management[-_]?key)", re.IGNORECASE)

_WINDOW_TYPE_BY_NAME = {
    "5h": "five_hour",
    "five_hour": "five_hour",
    "day": "daily",
    "daily": "daily",
    "week": "weekly",
    "weekly": "weekly",
    "month": "monthly",
    "monthly": "monthly",
}


def snapshot_to_rows(snap: ProviderSnapshot) -> list[dict]:
    if snap.status == "error":
        return []
    account = _resolve_account(snap)
    raw_json = json.dumps(redact(snap.to_dict()), ensure_ascii=False, separators=(",", ":"))
    rows: list[dict] = []
    for window in snap.windows:
        rows.append(_window_row(snap, account, window, raw_json))
    for balance in snap.balances:
        if not isinstance(balance, dict):
            continue
        rows.append(_balance_row(snap, account, balance, raw_json))
    return rows


def redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        result: dict[str, Any] = {}
        for key, value in obj.items():
            if _REDACT_RE.search(str(key)) is not None:
                continue
            result[key] = redact(value)
        return result
    if isinstance(obj, list):
        return [redact(item) for item in obj]
    return obj


def _resolve_account(snap: ProviderSnapshot) -> str:
    account = getattr(snap, "account", None) or "default"
    if account and account != "default":
        return account
    if snap.provider == "openrouter":
        details = snap.details or {}
        label = details.get("key_label")
        if isinstance(label, str) and label.strip():
            return label.strip()
    return "default"


def _window_row(snap: ProviderSnapshot, account: str, window: QuotaWindow, raw_json: str) -> dict:
    window_type = _classify_window(window.name)
    return {
        "provider": snap.provider,
        "account": account,
        "window_type": window_type,
        "window_label": window.name,
        "collected_at": snap.checked_at,
        "used": window.used,
        "remaining": window.remaining,
        "limit_value": window.limit,
        "used_percent": window.used_percent,
        "unit": window.unit,
        "reset_at": window.reset_at,
        "reset_estimated": int(bool(getattr(window, "reset_estimated", False))),
        "raw_json": raw_json,
    }


def _classify_window(name: str | None) -> str:
    if not name:
        return "unknown"
    key = name.strip().lower()
    if key in _WINDOW_TYPE_BY_NAME:
        return _WINDOW_TYPE_BY_NAME[key]
    return "unknown"


def _balance_row(snap: ProviderSnapshot, account: str, balance: dict, raw_json: str) -> dict:
    currency = balance.get("currency")
    total = balance.get("total")
    window_type = _classify_balance(snap, balance)
    limit_value = _balance_limit(snap, balance)
    return {
        "provider": snap.provider,
        "account": account,
        "window_type": window_type,
        "window_label": currency if isinstance(currency, str) else None,
        "collected_at": snap.checked_at,
        "used": None,
        "remaining": total if isinstance(total, (int, float)) else None,
        "limit_value": limit_value,
        "used_percent": None,
        "unit": currency if isinstance(currency, str) else None,
        "reset_at": None,
        "reset_estimated": 0,
        "raw_json": raw_json,
    }


def _classify_balance(snap: ProviderSnapshot, balance: dict) -> str:
    if snap.provider == "codex":
        details = snap.details or {}
        currency = balance.get("currency")
        has_credits = bool(details.get("has_credits"))
        unlimited = bool(details.get("unlimited"))
        if currency == "USD" and (has_credits or unlimited):
            return "credits"
        return "balance"
    return "balance"


def _balance_limit(snap: ProviderSnapshot, balance: dict) -> float | None:
    if snap.provider == "openrouter":
        purchased = balance.get("purchased")
        if isinstance(purchased, (int, float)):
            return float(purchased)
        return None
    if snap.provider == "deepseek":
        topped_up = balance.get("topped_up")
        if isinstance(topped_up, (int, float)):
            return float(topped_up)
        granted = balance.get("granted")
        if isinstance(granted, (int, float)):
            return float(granted)
    return None