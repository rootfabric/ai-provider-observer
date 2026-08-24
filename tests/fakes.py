"""Test fakes / builders for scenario tests (M6 — §32, §36.18).

These helpers are intentionally **minimal**: just enough surface to
build ``ProviderSnapshot`` / ``QuotaPoint`` rows and have them flow
through the real ``Store`` + ``AnalyticsEngine`` code paths. The
priority is fidelity to the on-disk schema, not feature parity with
real provider collectors.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

from app.models import ProviderSnapshot, QuotaWindow


# ---------------------------------------------------------------------------
# Window / snapshot builders
# ---------------------------------------------------------------------------


def make_window(
    name: str,
    used_percent: float,
    reset_in_hours: float = 2.0,
    used: float | None = None,
    limit: float | None = None,
    remaining: float | None = None,
    unit: str | None = None,
    reset_at: datetime | None = None,
    reset_estimated: bool = False,
    now: datetime | None = None,
) -> QuotaWindow:
    """Return a single ``QuotaWindow`` with sensible defaults."""
    if now is None:
        now = datetime.now(timezone.utc)
    if reset_at is None and reset_in_hours is not None:
        reset_at = now + timedelta(hours=reset_in_hours)
    return QuotaWindow(
        name=name,
        used_percent=used_percent,
        remaining_percent=max(0.0, 100.0 - used_percent),
        reset_at=reset_at.isoformat() if isinstance(reset_at, datetime) else reset_at,
        used=used,
        limit=limit,
        remaining=remaining,
        unit=unit,
        unlimited=False,
        window_type=None,
        reset_estimated=reset_estimated,
    )


def make_snapshot(
    provider: str,
    label: str,
    windows: Sequence[QuotaWindow] | None = None,
    balances: Sequence[dict] | None = None,
    status: str = "ok",
    when: datetime | None = None,
    plan: str | None = None,
    error: str | None = None,
) -> ProviderSnapshot:
    """Return a single ``ProviderSnapshot`` for one provider."""
    if when is None:
        when = datetime.now(timezone.utc)
    return ProviderSnapshot(
        provider=provider,
        label=label,
        status=status,
        checked_at=when.isoformat(),
        latency_ms=100,
        plan=plan,
        windows=list(windows or []),
        balances=list(balances or []),
        details={"demo": True},
        error=error,
    )


# ---------------------------------------------------------------------------
# Linear series generator
# ---------------------------------------------------------------------------


def linear_points(
    *,
    hours: float = 3.0,
    step_minutes: int = 5,
    start_used: float = 40.0,
    slope_per_hour: float = 10.0,
    reset_in_hours: float = 2.0,
    limit_value: float = 100.0,
    unit: str | None = "credits",
    reset_at: datetime | None = None,
    now: datetime | None = None,
    start_used_percent: float | None = None,
) -> list[tuple[datetime, float]]:
    """Generate ``(timestamp, used_percent)`` pairs on a linear slope.

    ``start_used_percent`` is an alias for ``start_used`` (kept for
    readability at the call site).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if start_used_percent is not None:
        start_used = start_used_percent
    if reset_at is None:
        reset_at = now + timedelta(hours=reset_in_hours)
    step = max(1, int(step_minutes))
    total_minutes = int(hours * 60)
    n_points = max(2, total_minutes // step + 1)
    out: list[tuple[datetime, float]] = []
    for i in range(n_points):
        ts = now - timedelta(minutes=(n_points - 1 - i) * step)
        pct = max(0.0, min(100.0, start_used + slope_per_hour * (i * step / 60.0)))
        out.append((ts, pct))
    return out


# ---------------------------------------------------------------------------
# Direct quota_snapshots writer (bypasses ProviderSnapshot normalisation)
# ---------------------------------------------------------------------------


def write_history(
    store,
    provider: str,
    account: str,
    window_type: str,
    points_spec: Iterable[tuple[datetime, float]],
    *,
    unit: str | None = "credits",
    reset_at: datetime | None = None,
    window_label: str | None = None,
    reset_estimated: bool = False,
    limit_value: float | None = 100.0,
) -> int:
    """Write a series of (ts, used_percent) rows into ``quota_snapshots``.

    Returns the number of inserted rows. ``reset_at`` is applied to
    every point so the segmenter keeps them in one segment.
    """
    rows: list[dict] = []
    label = window_label or window_type
    reset_iso = reset_at.isoformat() if isinstance(reset_at, datetime) else reset_at
    for ts, pct in points_spec:
        pct = max(0.0, min(100.0, float(pct)))
        used = (limit_value or 0.0) * pct / 100.0 if limit_value else None
        rows.append(
            {
                "provider": provider,
                "account": account,
                "window_type": window_type,
                "window_label": label,
                "collected_at": ts.isoformat() if isinstance(ts, datetime) else ts,
                "used": used,
                "remaining": (limit_value - used) if limit_value and used is not None else None,
                "limit_value": limit_value,
                "used_percent": pct,
                "unit": unit,
                "reset_at": reset_iso,
                "reset_estimated": int(bool(reset_estimated)),
                "raw_json": "{}",
            }
        )
    return store.save_quota_snapshots(rows)


__all__ = [
    "make_window",
    "make_snapshot",
    "linear_points",
    "write_history",
]
