from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

import httpx

from app.models import ProviderSnapshot


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def epoch_to_iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 10_000_000_000:
        number /= 1000.0
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def clamp_percent(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(100.0, number))


class Provider(ABC):
    provider_id: str
    label: str

    def __init__(self, timeout: float):
        self.timeout = timeout

    def disabled(self, reason: str) -> ProviderSnapshot:
        return ProviderSnapshot(
            provider=self.provider_id,
            label=self.label,
            status="disabled",
            checked_at=utc_now_iso(),
            details={"note": reason},
        )

    def error(self, message: str, latency_ms: int | None = None) -> ProviderSnapshot:
        return ProviderSnapshot(
            provider=self.provider_id,
            label=self.label,
            status="error",
            checked_at=utc_now_iso(),
            latency_ms=latency_ms,
            error=message[:500],
        )

    async def get_json(
        self,
        url: str,
        headers: dict[str, str],
        *,
        client: httpx.AsyncClient | None = None,
    ) -> tuple[dict[str, Any], int]:
        owned = client is None
        http = client or httpx.AsyncClient(timeout=self.timeout, follow_redirects=False)
        started = time.perf_counter()
        try:
            response = await http.get(url, headers=headers)
            latency_ms = round((time.perf_counter() - started) * 1000)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("JSON response is not an object")
            return payload, latency_ms
        finally:
            if owned:
                await http.aclose()

    @abstractmethod
    async def fetch(self) -> ProviderSnapshot:
        raise NotImplementedError
