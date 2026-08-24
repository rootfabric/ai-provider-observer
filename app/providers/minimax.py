from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import ProviderSnapshot, QuotaWindow
from app.providers.base import Provider, clamp_percent, epoch_to_iso, utc_now_iso


class MiniMaxProvider(Provider):
    provider_id = "minimax"
    label = "MiniMax"

    def __init__(self, api_key: str, base_url: str, timeout: float):
        super().__init__(timeout)
        self.api_key = api_key
        self.base_url = base_url

    @staticmethod
    def parse(payload: dict, latency_ms: int) -> ProviderSnapshot:
        base_resp = payload.get("base_resp") or {}
        if isinstance(base_resp, dict) and base_resp.get("status_code") not in (None, 0):
            raise ValueError(str(base_resp.get("status_msg") or "MiniMax business-level error"))
        rows = payload.get("model_remains") or []
        row = next((x for x in rows if isinstance(x, dict) and x.get("model_name") == "general"), None)
        if row is None:
            row = next((x for x in rows if isinstance(x, dict)), None)
        if not row:
            return ProviderSnapshot(
                provider="minimax", label="MiniMax", status="partial", checked_at=utc_now_iso(),
                latency_ms=latency_ms, details={"note": "No model_remains rows"},
            )
        five_remaining = clamp_percent(row.get("current_interval_remaining_percent"))
        week_remaining = clamp_percent(row.get("current_weekly_remaining_percent"))
        five_reset = epoch_to_iso(row.get("end_time")) or _reset_from_ms(row.get("remains_time"))
        week_reset = epoch_to_iso(row.get("weekly_end_time")) or _reset_from_ms(row.get("weekly_remains_time"))
        windows = []
        if five_remaining is not None:
            windows.append(QuotaWindow(
                name="5h", used_percent=round(100 - five_remaining, 2), remaining_percent=five_remaining,
                reset_at=five_reset, unlimited=row.get("current_interval_status") == 3,
            ))
        if week_remaining is not None:
            windows.append(QuotaWindow(
                name="week", used_percent=round(100 - week_remaining, 2), remaining_percent=week_remaining,
                reset_at=week_reset, unlimited=row.get("current_weekly_status") == 3,
            ))
        return ProviderSnapshot(
            provider="minimax", label="MiniMax", status="ok" if windows else "partial",
            checked_at=utc_now_iso(), latency_ms=latency_ms,
            windows=windows,
            details={"resource_group": row.get("model_name"), "source": "token_plan/remains"},
        )

    async def fetch(self) -> ProviderSnapshot:
        if not self.api_key:
            return self.disabled("Set MINIMAX_API_KEY (Token Plan sk-cp key)")
        url = f"{self.base_url}/v1/token_plan/remains"
        try:
            payload, latency = await self.get_json(url, {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            })
            return self.parse(payload, latency)
        except Exception as exc:
            return self.error(f"MiniMax quota query failed: {type(exc).__name__}: {exc}")


def _reset_from_ms(value) -> str | None:
    try:
        ms = float(value)
    except (TypeError, ValueError):
        return None
    if ms < 0:
        return None
    return (datetime.now(timezone.utc) + timedelta(milliseconds=ms)).isoformat()
