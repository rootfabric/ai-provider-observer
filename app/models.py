from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class QuotaWindow:
    name: str
    used_percent: float | None = None
    remaining_percent: float | None = None
    reset_at: str | None = None
    used: float | None = None
    limit: float | None = None
    remaining: float | None = None
    unit: str | None = None
    unlimited: bool = False
    # Additive R1 annotations (canonical window classification, spec §4.1).
    window_type: str | None = None          # five_hour|daily|weekly|monthly|balance|credits|unknown
    reset_estimated: bool = False           # True when reset_at is computed, not guaranteed


@dataclass(slots=True)
class ProviderSnapshot:
    provider: str
    label: str
    status: str
    checked_at: str
    latency_ms: int | None = None
    plan: str | None = None
    windows: list[QuotaWindow] = field(default_factory=list)
    balances: list[dict[str, Any]] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    # Additive R1 annotation: account/key identity ("default" unless provider has several).
    account: str = "default"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
