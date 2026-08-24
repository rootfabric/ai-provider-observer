from __future__ import annotations

import httpx

from app.models import ProviderSnapshot, QuotaWindow
from app.providers.base import Provider, clamp_percent, epoch_to_iso, utc_now_iso


class ZaiProvider(Provider):
    provider_id = "zai"
    label = "Z.AI"

    def __init__(self, api_key: str, base_url: str, timeout: float):
        super().__init__(timeout)
        self.api_key = api_key
        self.base_url = base_url

    @staticmethod
    def parse(payload: dict, latency_ms: int) -> ProviderSnapshot:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        limits = data.get("limits") or []
        windows: list[QuotaWindow] = []
        for item in limits:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("type") or "")
            unit = item.get("unit")
            number = item.get("number")
            if kind not in {"TOKENS_LIMIT", "CREDIT_LIMIT"}:
                continue
            if unit == 3 and number == 5:
                name = "5h"
            elif unit == 6 and number == 1:
                name = "week"
            else:
                name = f"window {number or '?'}"
            used_pct = clamp_percent(item.get("percentage"))
            windows.append(
                QuotaWindow(
                    name=name,
                    used_percent=used_pct,
                    remaining_percent=None if used_pct is None else round(100 - used_pct, 2),
                    reset_at=epoch_to_iso(item.get("nextResetTime")),
                    used=_number(item.get("currentValue")),
                    limit=_number(item.get("usage")),
                    remaining=_number(item.get("remaining")),
                    unit="credits" if kind == "CREDIT_LIMIT" else "tokens",
                )
            )
        plan = data.get("level") or data.get("planName") or data.get("plan_name")
        return ProviderSnapshot(
            provider="zai",
            label="Z.AI",
            status="ok" if windows else "partial",
            checked_at=utc_now_iso(),
            latency_ms=latency_ms,
            plan=str(plan) if plan else None,
            windows=windows,
            details={"source": "monitor usage API", "windows_found": len(windows)},
        )

    async def fetch(self) -> ProviderSnapshot:
        if not self.api_key:
            return self.disabled("Set ZAI_API_KEY")
        candidates = [
            (f"{self.base_url}/api/monitor/usage", f"Bearer {self.api_key}"),
            (f"{self.base_url}/api/monitor/usage", self.api_key),
            (f"{self.base_url}/api/monitor/usage/quota/limit", self.api_key),
            (f"{self.base_url}/api/monitor/usage/quota/limit", f"Bearer {self.api_key}"),
        ]
        errors: list[str] = []
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
            for url, auth in candidates:
                try:
                    payload, latency = await self.get_json(
                        url,
                        {"Authorization": auth, "Accept": "application/json", "Accept-Language": "en-US,en"},
                        client=client,
                    )
                    if payload.get("success") is False or payload.get("code") not in (None, 0, 200):
                        raise RuntimeError(str(payload.get("msg") or payload.get("message") or "API rejected request"))
                    snap = self.parse(payload, latency)
                    snap.details["endpoint"] = url.removeprefix(self.base_url)
                    snap.details["auth_style"] = "bearer" if auth.startswith("Bearer ") else "raw"
                    return snap
                except Exception as exc:  # endpoint/auth variants are intentionally probed
                    errors.append(f"{url.rsplit('/', 1)[-1]}: {type(exc).__name__}")
        return self.error("Z.AI quota query failed; tried current and legacy monitor endpoints. " + ", ".join(errors))


def _number(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
