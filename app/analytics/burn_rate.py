"""Burn rate computation (spec §4.3, §6).

This module performs a simple ordinary-least-squares regression on a
slice of cleaned quota points. It depends only on :mod:`app.analytics.series`
for timestamp parsing and on :mod:`app.analytics.types` for the
dataclasses — no I/O, no numpy/scipy.

The OLS slope is computed manually via the covariance formula:

    slope = Σ (x_i − x̄)(y_i − ȳ) / Σ (x_i − x̄)²

Units are explicit: when every retained point has a numeric ``used``
value *and* a ``unit`` hint is provided, the regression operates on the
absolute ``used`` series (e.g. credits/hour, tokens/hour). Otherwise
it operates on ``used_percent`` and the resulting unit is
``percentage_points_per_hour``.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Mapping

from app.analytics import types as t
from app.analytics.series import parse_iso_utc


# ---------------------------------------------------------------------------
# Lookback registry
# ---------------------------------------------------------------------------

_LOOKBACK_DURATION: dict[str, timedelta] = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "3h": timedelta(hours=3),
    "24h": timedelta(hours=24),
    "3d": timedelta(days=3),
    "7d": timedelta(days=7),
}

_LOOKBACK_HOURS: dict[str, float] = {
    "15m": 0.25,
    "1h": 1.0,
    "3h": 3.0,
    "24h": 24.0,
    "3d": 72.0,
    "7d": 168.0,
}


def _span_minutes(points: list[tuple[datetime, float]]) -> float | None:
    if len(points) < 2:
        return 0.0
    first = points[0][0]
    last = points[-1][0]
    delta = last - first
    return max(delta.total_seconds() / 60.0, 0.0)


def _ols(points: list[tuple[datetime, float]]) -> tuple[float, int, float | None]:
    """Return (slope_per_hour, n, span_minutes)."""
    n = len(points)
    span = _span_minutes(points)
    if n < 2:
        return 0.0, n, span
    hours = [(ts - points[0][0]).total_seconds() / 3600.0 for ts, _ in points]
    xs = [0.0 + h for h in hours]  # explicit copy
    ys = [y for _, y in points]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = 0.0
    var = 0.0
    for x, y in zip(xs, ys):
        dx = x - mean_x
        cov += dx * (y - mean_y)
        var += dx * dx
    if var <= 0.0:
        return 0.0, n, span
    slope = cov / var
    return slope, n, span


def _has_absolute_unit(points: list[t.QuotaPoint], unit_hint: str | None) -> bool:
    """All points must have a numeric ``used`` to use absolute regression."""
    if not unit_hint:
        return False
    return all(p.used is not None for p in points)


def _burn_unit(unit_hint: str | None) -> str:
    if unit_hint:
        return f"{unit_hint}/hour"
    return "percentage_points_per_hour"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_burn(
    points: list[t.QuotaPoint],
    lookback_seconds: float,
    now: datetime,
    unit: str | None,
    cfg,
) -> t.BurnStat:
    """Compute the OLS burn rate for a single lookback window.

    Selects the points whose ``collected_at`` falls in
    ``[now - lookback_seconds, now]`` (inclusive on both ends). When
    ``unit`` is provided *and* every retained point has a numeric
    ``used`` value, the regression runs on the absolute series; else it
    falls back to ``used_percent``.

    Returns a :class:`BurnStat` with ``status='ok'`` only when the
    minimum number of points (``cfg.burn_min_points``, default 3) and
    span (``cfg.burn_min_span_minutes``, default 5) are both satisfied.
    Otherwise the status is ``insufficient_data`` and ``value`` is
    ``None`` (never zero).
    """
    lookback = t.STATUS_INSUFFICIENT_DATA
    value: float | None = None
    unit_str = _burn_unit(unit)
    points_used = 0
    span_minutes: float | None = None

    min_pts = int(getattr(cfg, "burn_min_points", 3))
    min_span = float(getattr(cfg, "burn_min_span_minutes", 5.0))

    if not points:
        return t.BurnStat(
            lookback="custom",
            value=None,
            unit=unit_str,
            points_used=0,
            span_minutes=None,
            status=t.STATUS_INSUFFICIENT_DATA,
        )

    if lookback_seconds <= 0:
        return t.BurnStat(
            lookback="custom",
            value=None,
            unit=unit_str,
            points_used=0,
            span_minutes=None,
            status=t.STATUS_INSUFFICIENT_DATA,
        )

    cutoff = now - timedelta(seconds=lookback_seconds)

    # Filter window (preserves input order) and pick the regression target.
    in_window: list[tuple[datetime, float, bool]] = []
    use_absolute = _has_absolute_unit(points, unit) and bool(unit)
    for point in points:
        ts = parse_iso_utc(point.collected_at)
        if ts is None:
            continue
        if ts < cutoff or ts > now:
            continue
        if use_absolute:
            assert point.used is not None  # guaranteed by _has_absolute_unit
            in_window.append((ts, float(point.used), True))
        else:
            if point.used_percent is None:
                continue
            try:
                pct = float(point.used_percent)
            except (TypeError, ValueError):
                continue
            if pct < 0.0 or pct > 100.0:
                continue
            in_window.append((ts, pct, False))

    in_window.sort(key=lambda row: row[0])

    if not in_window:
        return t.BurnStat(
            lookback="custom",
            value=None,
            unit=unit_str,
            points_used=0,
            span_minutes=None,
            status=t.STATUS_INSUFFICIENT_DATA,
        )

    points_used = len(in_window)
    pairs = [(ts, y) for ts, y, _ in in_window]
    span_minutes = _span_minutes(pairs)

    if points_used < min_pts:
        return t.BurnStat(
            lookback="custom",
            value=None,
            unit=unit_str,
            points_used=points_used,
            span_minutes=span_minutes,
            status=t.STATUS_INSUFFICIENT_DATA,
        )

    if span_minutes is None or span_minutes + 1e-9 < min_span:
        return t.BurnStat(
            lookback="custom",
            value=None,
            unit=unit_str,
            points_used=points_used,
            span_minutes=span_minutes,
            status=t.STATUS_INSUFFICIENT_DATA,
        )

    slope, _, _ = _ols(pairs)
    value = slope
    lookback = t.STATUS_OK
    return t.BurnStat(
        lookback="custom",
        value=value,
        unit=unit_str,
        points_used=points_used,
        span_minutes=span_minutes,
        status=lookback,
    )


def compute_burns(
    current_segment_points: list[t.QuotaPoint],
    now: datetime,
    unit: str | None,
    cfg,
    window_type: str,
) -> dict[str, t.BurnStat]:
    """Return burn stats for every relevant lookback window.

    The standard three windows ``15m``, ``1h`` and ``3h`` are always
    returned. For ``weekly`` windows, ``24h`` and ``3d`` are appended
    so that pacing / weekly forecasting has enough history to work
    with. A synthetic ``window`` burn is also computed over the whole
    segment (from its earliest observation to ``now``); it is reported
    as ``insufficient_data`` when fewer than the minimum points are
    available.
    """
    out: dict[str, t.BurnStat] = {}

    for label in ("15m", "1h", "3h"):
        seconds = _LOOKBACK_DURATION[label].total_seconds()
        stat = compute_burn(current_segment_points, seconds, now, unit, cfg)
        stat.lookback = label
        out[label] = stat

    if window_type == "weekly":
        for label in ("24h", "3d"):
            seconds = _LOOKBACK_DURATION[label].total_seconds()
            stat = compute_burn(current_segment_points, seconds, now, unit, cfg)
            stat.lookback = label
            out[label] = stat

    # burn_window — from the first point of the segment to now.
    window_stat = compute_burn_window(current_segment_points, now, unit, cfg)
    out["window"] = window_stat

    return out


def compute_burn_window(
    current_segment_points: list[t.QuotaPoint],
    now: datetime,
    unit: str | None,
    cfg,
) -> t.BurnStat:
    """Compute ``burn_window`` — OLS over the entire current segment."""
    min_pts = int(getattr(cfg, "burn_min_points", 3))
    unit_str = _burn_unit(unit)

    if not current_segment_points:
        return t.BurnStat(
            lookback="window",
            value=None,
            unit=unit_str,
            points_used=0,
            span_minutes=None,
            status=t.STATUS_INSUFFICIENT_DATA,
        )

    use_absolute = _has_absolute_unit(current_segment_points, unit) and bool(unit)
    pairs: list[tuple[datetime, float]] = []
    for point in current_segment_points:
        ts = parse_iso_utc(point.collected_at)
        if ts is None:
            continue
        if use_absolute and point.used is not None:
            pairs.append((ts, float(point.used)))
        elif not use_absolute and point.used_percent is not None:
            try:
                pct = float(point.used_percent)
            except (TypeError, ValueError):
                continue
            if 0.0 <= pct <= 100.0:
                pairs.append((ts, pct))

    pairs.sort(key=lambda kv: kv[0])
    if len(pairs) < min_pts:
        return t.BurnStat(
            lookback="window",
            value=None,
            unit=unit_str,
            points_used=len(pairs),
            span_minutes=_span_minutes(pairs),
            status=t.STATUS_INSUFFICIENT_DATA,
        )

    span = _span_minutes(pairs)
    min_span = float(getattr(cfg, "burn_min_span_minutes", 5.0))
    if span is None or span + 1e-9 < min_span:
        return t.BurnStat(
            lookback="window",
            value=None,
            unit=unit_str,
            points_used=len(pairs),
            span_minutes=span,
            status=t.STATUS_INSUFFICIENT_DATA,
        )

    slope, _, _ = _ols(pairs)
    return t.BurnStat(
        lookback="window",
        value=slope,
        unit=unit_str,
        points_used=len(pairs),
        span_minutes=span,
        status=t.STATUS_OK,
    )


# ---------------------------------------------------------------------------
# Acceleration
# ---------------------------------------------------------------------------


def compute_acceleration(
    burn_15m: t.BurnStat,
    burn_1h: t.BurnStat,
    cfg,
) -> t.Acceleration:
    """Classify the burn acceleration of ``burn_15m`` vs ``burn_1h``.

    Rules (spec §6):

        ratio = burn_15m.value / burn_1h.value
        |burn_1h.value| < ACCEL_BASELINE_MIN ⇒ ratio = None, baseline_ok=False, band=None
        ratio < 0.7                → decelerating
        0.7 ≤ ratio ≤ 1.3          → stable
        1.3 < ratio ≤ 2.0          → accelerating
        ratio > 2.0                → anomaly

    Only fires when both lookbacks report ``status == 'ok'`` and have
    non-None numeric values; otherwise the result is ``None`` for every
    field.
    """
    baseline_min = float(getattr(cfg, "accel_baseline_min", 1.0))

    if (
        burn_15m.status != t.STATUS_OK
        or burn_1h.status != t.STATUS_OK
        or burn_15m.value is None
        or burn_1h.value is None
    ):
        return t.Acceleration(ratio=None, band=None, baseline_ok=False)

    if abs(burn_1h.value) < baseline_min:
        return t.Acceleration(ratio=None, band=None, baseline_ok=False)

    ratio = burn_15m.value / burn_1h.value
    if ratio < 0.7:
        band = "decelerating"
    elif ratio <= 1.3:
        band = "stable"
    elif ratio <= 2.0:
        band = "accelerating"
    else:
        band = "anomaly"
    return t.Acceleration(ratio=ratio, band=band, baseline_ok=True)


__all__ = [
    "compute_burn",
    "compute_burns",
    "compute_burn_window",
    "compute_acceleration",
    "_LOOKBACK_HOURS",
]