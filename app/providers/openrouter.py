from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from app.models import ProviderSnapshot, QuotaWindow
from app.providers.base import Provider, utc_now_iso


class OpenRouterProvider(Provider):
    provider_id = "openrouter"
    label = "OpenRouter"

    def __init__(self, api_key: str, management_key: str, base_url: str, timeout: float):
        super().__init__(timeout)
        self.api_key = api_key
        self.management_key = management_key
        self.base_url = base_url

    @staticmethod
    def parse_key(payload: dict, latency_ms: int) -> ProviderSnapshot:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        windows = []
        limit = _number(data.get("limit"))
        reset = data.get("limit_reset")
        if limit and reset in {"daily", "weekly", "monthly"}:
            used = _number(data.get(f"usage_{reset}")) or 0.0
            remaining = _number(data.get("limit_remaining"))
            pct = min(100.0, max(0.0, used / limit * 100)) if limit > 0 else None
            windows.append(QuotaWindow(
                name=reset, used_percent=pct, remaining_percent=None if pct is None else 100 - pct,
                reset_at=_next_reset(reset), used=used, limit=limit, remaining=remaining, unit="USD",
            ))
        return ProviderSnapshot(
            provider="openrouter", label="OpenRouter", status="ok", checked_at=utc_now_iso(),
            latency_ms=latency_ms, windows=windows,
            details={
                "key_label": data.get("label"),
                "free_tier": data.get("is_free_tier"),
                "usage_total_usd": _number(data.get("usage")),
                "usage_daily_usd": _number(data.get("usage_daily")),
                "usage_weekly_usd": _number(data.get("usage_weekly")),
                "usage_monthly_usd": _number(data.get("usage_monthly")),
                "limit_reset": reset,
            },
        )

    async def fetch(self) -> ProviderSnapshot:
        if not self.api_key and not self.management_key:
            return self.disabled("Set OPENROUTER_API_KEY; optionally add OPENROUTER_MANAGEMENT_KEY")
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
                if self.api_key:
                    key_payload, latency = await self.get_json(
                        f"{self.base_url}/api/v1/key",
                        {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}, client=client,
                    )
                    snap = self.parse_key(key_payload, latency)
                else:
                    snap = ProviderSnapshot(
                        provider="openrouter", label="OpenRouter", status="partial", checked_at=utc_now_iso(),
                        details={"note": "No regular API key; only account credits will be queried."},
                    )
                if self.management_key:
                    credits, credit_latency = await self.get_json(
                        f"{self.base_url}/api/v1/credits",
                        {"Authorization": f"Bearer {self.management_key}", "Accept": "application/json"}, client=client,
                    )
                    data = credits.get("data") or {}
                    total = _number(data.get("total_credits"))
                    used = _number(data.get("total_usage"))
                    if total is not None and used is not None:
                        snap.balances.append({"currency": "USD", "total": round(total - used, 6), "purchased": total, "used": used})
                    snap.details["account_credits_latency_ms"] = credit_latency
                return snap
        except Exception as exc:
            return self.error(f"OpenRouter query failed: {type(exc).__name__}: {exc}")


def _number(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _next_reset(kind: str) -> str | None:
    now = datetime.now(timezone.utc)
    if kind == "daily":
        target = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif kind == "weekly":
        days = 7 - now.weekday()
        target = (now + timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif kind == "monthly":
        if now.month == 12:
            target = now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            target = now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        return None
    return target.isoformat()
