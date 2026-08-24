"""Monetary balance runway (spec §4.6, §16-§17).

For ``balance`` / ``credits`` windows the caller passes the account's
balance history (where each point's ``remaining`` equals the balance
at ``collected_at``). We perform two simple OLS regressions of
``remaining`` against time, over the 24h and 168h windows, and report
spend as ``-slope × scale`` where ``scale`` is ``24`` for the daily
window and ``168`` for the weekly window.

The runway is ``balance / spend_per_day`` when ``spend_per_day > ε``.
Otherwise the runway is reported as ``None`` and the section is marked
``stable`` (no spend observed yet).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.analytics import types as t
from app.analytics.confidence import confidence_from_span
from app.analytics.series import parse_iso_utc


_EPS = 1e-9


def _ols_slope_per_hour(pairs: list[tuple[datetime, float]]) -> float | None:
    if len(pairs) < 2:
        return None
    first = pairs[0][0]
    xs = [(ts - first).total_seconds() / 3600.0 for ts, _ in pairs]
    ys = [y for _, y in pairs]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = 0.0
    var = 0.0
    for x, y in zip(xs, ys):
        dx = x - mean_x
        cov += dx * (y - mean_y)
        var += dx * dx
    if var <= 0.0:
        return None
    return cov / var


def _span_minutes(pairs: list[tuple[datetime, float]]) -> float | None:
    if len(pairs) < 2:
        return 0.0
    delta = pairs[-1][0] - pairs[0][0]
    return max(delta.total_seconds() / 60.0, 0.0)


def _window_pairs(
    points: list[t.QuotaPoint],
    now: datetime,
    window: timedelta,
) -> list[tuple[datetime, float]]:
    cutoff = now - window
    pairs: list[tuple[datetime, float]] = []
    for point in points:
        ts = parse_iso_utc(point.collected_at)
        if ts is None:
            continue
        if ts < cutoff or ts > now:
            continue
        if point.remaining is None:
            continue
        try:
            value = float(point.remaining)
        except (TypeError, ValueError):
            continue
        pairs.append((ts, value))
    pairs.sort(key=lambda kv: kv[0])
    return pairs


def compute_runway(
    points: list[t.QuotaPoint],
    currency: str | None,
    now: datetime,
    cfg,
) -> t.Runway:
    """Return a :class:`Runway` for a balance history.

    ``points`` are expected to be the balance history where each
    ``QuotaPoint.remaining`` carries the absolute balance (USD or
    credits). At least one of the 24h or 168h windows must produce a
    regression for the result to be ``ok``.
    """
    min_pts = int(getattr(cfg, "burn_min_points", 3))
    min_span = float(getattr(cfg, "burn_min_span_minutes", 5.0))

    span_minutes: float | None = None
    usd_per_hour: float | None = None
    usd_per_day: float | None = None
    usd_per_week: float | None = None
    balance_total: float | None = None
    runway_days: float | None = None
    monthly_spend: float | None = None

    # Prefer the 24h window; fall back to 7d if it does not yield enough.
    candidates: list[tuple[timedelta, float]] = [
        (timedelta(hours=24), 24.0),
        (timedelta(days=7), 168.0),
    ]

    used_candidates: list[tuple[timedelta, float]] = []
    for window, scale_hours in candidates:
        pairs = _window_pairs(points, now, window)
        if len(pairs) < min_pts:
            continue
        span = _span_minutes(pairs)
        if span + 1e-9 < min_span:
            continue
        slope = _ols_slope_per_hour(pairs)
        if slope is None:
            continue
        spend_per_hour = -slope  # spend is positive when balance declines
        used_candidates.append((window, spend_per_hour))
        if span_minutes is None or span > span_minutes:
            span_minutes = span

    if used_candidates:
        # The first successful candidate is the primary one (24h wins).
        primary_window, primary_spend_per_hour = used_candidates[0]
        usd_per_hour = primary_spend_per_hour
        if primary_window == timedelta(hours=24):
            usd_per_day = primary_spend_per_hour * 24.0
        else:
            usd_per_week = primary_spend_per_hour * 168.0
            usd_per_day = primary_spend_per_hour * 24.0

    # Balance total — take the most recent observation.
    sorted_points = sorted(
        [p for p in points if p.remaining is not None],
        key=lambda p: parse_iso_utc(p.collected_at) or now,
    )
    if sorted_points:
        try:
            balance_total = float(sorted_points[-1].remaining)
        except (TypeError, ValueError):
            balance_total = None

    status = t.STATUS_INSUFFICIENT_DATA
    if usd_per_day is not None and balance_total is not None:
        spend_per_day = usd_per_day
        if spend_per_day > _EPS:
            runway_days = balance_total / spend_per_day
            monthly_spend = spend_per_day * 30.4375
            status = t.STATUS_OK
        else:
            # Spend ≤ 0 — stable (balance growing or flat).
            status = t.STATUS_OK
            runway_days = None
            monthly_spend = None
    elif usd_per_day is not None:
        status = t.STATUS_OK

    confidence = confidence_from_span(span_minutes)

    return t.Runway(
        currency=currency,
        balance_total=balance_total,
        usd_per_hour=usd_per_hour,
        usd_per_day=usd_per_day,
        usd_per_week=usd_per_week,
        runway_days=runway_days,
        projected_monthly_spend=monthly_spend,
        status=status,
        confidence=confidence,
    )


__all__ = ["compute_runway"]