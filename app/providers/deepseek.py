from __future__ import annotations

from app.models import ProviderSnapshot
from app.providers.base import Provider, utc_now_iso


class DeepSeekProvider(Provider):
    provider_id = "deepseek"
    label = "DeepSeek"

    def __init__(self, api_key: str, base_url: str, timeout: float):
        super().__init__(timeout)
        self.api_key = api_key
        self.base_url = base_url

    @staticmethod
    def parse(payload: dict, latency_ms: int) -> ProviderSnapshot:
        balances = []
        for item in payload.get("balance_infos") or []:
            if not isinstance(item, dict):
                continue
            balances.append({
                "currency": item.get("currency"),
                "total": _number(item.get("total_balance")),
                "granted": _number(item.get("granted_balance")),
                "topped_up": _number(item.get("topped_up_balance")),
            })
        return ProviderSnapshot(
            provider="deepseek", label="DeepSeek", status="ok" if balances else "partial",
            checked_at=utc_now_iso(), latency_ms=latency_ms, balances=balances,
            details={
                "available": bool(payload.get("is_available")),
                "note": "Pay-as-you-go balance; no 5h/weekly subscription quota is exposed by this API.",
            },
        )

    async def fetch(self) -> ProviderSnapshot:
        if not self.api_key:
            return self.disabled("Set DEEPSEEK_API_KEY")
        try:
            payload, latency = await self.get_json(
                f"{self.base_url}/user/balance",
                {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
            )
            return self.parse(payload, latency)
        except Exception as exc:
            return self.error(f"DeepSeek balance query failed: {type(exc).__name__}: {exc}")


def _number(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
