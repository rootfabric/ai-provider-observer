"""Confidence band resolution (spec §27).

Maps a span in minutes to one of ``LOW`` / ``MEDIUM`` / ``HIGH`` confidence
levels used throughout the analytics layer.
"""
from __future__ import annotations

from app.analytics import types as t


def confidence_from_span(
    span_minutes: float | None,
    high_threshold_minutes: float = 120.0,
) -> str:
    """Return confidence band for a given history span.

    Rules (spec §27):
        - ``None`` or span < 15 minutes → ``LOW``.
        - span < ``high_threshold_minutes`` (default 120 min) → ``MEDIUM``.
        - otherwise → ``HIGH``.
    """
    if span_minutes is None:
        return t.CONFIDENCE_LOW
    try:
        span = float(span_minutes)
    except (TypeError, ValueError):
        return t.CONFIDENCE_LOW
    if span < 15.0:
        return t.CONFIDENCE_LOW
    if span < float(high_threshold_minutes):
        return t.CONFIDENCE_MEDIUM
    return t.CONFIDENCE_HIGH


__all__ = ["confidence_from_span"]