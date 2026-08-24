from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import ProviderSnapshot, QuotaWindow


def demo_snapshots() -> list[ProviderSnapshot]:
    now = datetime.now(timezone.utc)
    ts = now.isoformat()
    def reset(hours: int) -> str:
        return (now + timedelta(hours=hours)).isoformat()
    return [
        ProviderSnapshot("zai", "Z.AI", "ok", ts, 178, "lite", [
            QuotaWindow("5h", 61, 39, reset(2), 1220, 2000, 780, "credits"),
            QuotaWindow("week", 34, 66, reset(92), 3400, 10000, 6600, "credits"),
        ], details={"demo": True}),
        ProviderSnapshot("minimax", "MiniMax", "ok", ts, 205, "Plus", [
            QuotaWindow("5h", 27, 73, reset(3)), QuotaWindow("week", 48, 52, reset(110)),
        ], details={"demo": True}),
        ProviderSnapshot("deepseek", "DeepSeek", "ok", ts, 132, balances=[
            {"currency": "USD", "total": 18.42, "granted": 0.0, "topped_up": 18.42}
        ], details={"available": True, "demo": True}),
        ProviderSnapshot("openrouter", "OpenRouter", "ok", ts, 149, windows=[
            QuotaWindow("weekly", 42, 58, reset(73), 21, 50, 29, "USD")
        ], balances=[{"currency": "USD", "total": 36.12, "purchased": 100, "used": 63.88}], details={"demo": True}),
        ProviderSnapshot("codex", "OpenAI Codex", "ok", ts, 221, "plus", [
            QuotaWindow("5h", 44, 56, reset(2)), QuotaWindow("week", 71, 29, reset(61)),
        ], details={"demo": True, "source": "Codex ChatGPT usage endpoint"}),
    ]
