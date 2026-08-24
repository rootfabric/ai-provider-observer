"""Weekly pacing and end-of-week forecast (spec §4.5, §10-§11).

A :class:`Pacing` block is only meaningful for ``weekly`` quota windows.
It answers the question: given how much of the week has elapsed and the
current burn rate, where is the account heading at the end of the
window?

The week boundary is anchored on the provider-reported ``reset_at``
(``reset_at − 7d``); when the reset is unknown the segment's first
observation is used as a soft fallback. The end-of-week projection has
three flavours:

* ``projected_whole_window`` – naive ratio ``used / elapsed_fraction``.
* ``projected_pace_24h``    – ``used + burn_24h × hours_left``.
* ``projected_pace_3d``     – ``used + burn_3d × hours_left``.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.analytics import types as t
from app.analytics.confidence import confidence_from_span
from app.analytics.series import parse_iso_utc


WEEK = timedelta(days=7)


def _pace_band(ratio: float | None) -> str | None:
    if ratio is None:
        return None
    if ratio < 0.8:
        return "comfortable"
    if ratio <= 1.1:
        return "normal"
    if ratio <= 1.4:
        return "elevated"
    if ratio <= 2.0:
        return "critical"
    return "unsustainable"


def compute_pacing(
    segment_points: list[t.QuotaPoint],
    burns: dict[str, t.BurnStat],
    now: datetime,
    cfg,
) -> t.Pacing | None:
    """Return a :class:`Pacing` block, or ``None`` when the input is empty."""
    if not segment_points:
        return None

    latest = segment_points[-1]
    week_min = float(getattr(cfg, "week_min_elapsed_pct", 3.0))

    # Anchor week_start.
    week_start: datetime | None = None
    if latest.reset_at:
        week_start = parse_iso_utc(latest.reset_at)
        if week_start is not None:
            week_start = week_start - WEEK

    if week_start is None:
        first_ts = parse_iso_utc(segment_points[0].collected_at)
        week_start = first_ts or now

    elapsed_total = (now - week_start).total_seconds()
    week_seconds = WEEK.total_seconds()
    elapsed_fraction = elapsed_total / week_seconds if week_seconds > 0 else 0.0
    elapsed_fraction = max(0.0, min(1.0, elapsed_fraction))
    elapsed_percent = elapsed_fraction * 100.0

    used_percent: float | None = None
    if latest.used_percent is not None:
        try:
            used_percent = float(latest.used_percent)
        except (TypeError, ValueError):
            used_percent = None
    if used_percent is None and latest.used is not None and latest.limit_value not in (None, 0):
        try:
            used_percent = float(latest.used) / float(latest.limit_value) * 100.0
        except (TypeError, ValueError):
            used_percent = None

    if used_percent is None:
        return None

    pace_ratio: float | None = None
    projected_whole: float | None = None
    if elapsed_percent >= week_min and elapsed_fraction > 0.0:
        pace_ratio = used_percent / elapsed_percent if elapsed_percent > 0 else None
        projected_whole = used_percent / elapsed_fraction if elapsed_fraction > 0 else None

    band = _pace_band(pace_ratio)

    # Compute hours left and apply 24h / 3d burns.
    hours_left = max(0.0, (week_seconds - elapsed_total) / 3600.0)

    def _project_from_burn(burn: t.BurnStat | None) -> float | None:
        if burn is None or burn.status != t.STATUS_OK:
            return None
        if burn.value is None:
            return None
        # Projections work with percentage points per hour only.
        if burn.unit != "percentage_points_per_hour":
            return None
        return used_percent + burn.value * hours_left

    projected_24h = _project_from_burn(burns.get("24h"))
    projected_3d = _project_from_burn(burns.get("3d"))

    # Confidence bands: <15m LOW, <24h MEDIUM, else HIGH.
    span_minutes: float | None = None
    if segment_points:
        first_ts = parse_iso_utc(segment_points[0].collected_at)
        last_ts = parse_iso_utc(latest.collected_at) or now
        if first_ts is not None:
            span_minutes = max(0.0, (last_ts - first_ts).total_seconds() / 60.0)

    if span_minutes is None or span_minutes < 15.0:
        confidence = t.CONFIDENCE_LOW
    elif span_minutes < 24 * 60.0:
        confidence = t.CONFIDENCE_MEDIUM
    else:
        confidence = t.CONFIDENCE_HIGH

    return t.Pacing(
        elapsed_percent=elapsed_percent,
        expected_usage_by_now=elapsed_percent,
        used_percent=used_percent,
        pace_ratio=pace_ratio,
        pace_band=band,
        projected_whole_window=projected_whole,
        projected_pace_24h=projected_24h,
        projected_pace_3d=projected_3d,
        confidence=confidence,
    )


__all__ = ["compute_pacing"]