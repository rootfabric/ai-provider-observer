"""Series-level utilities: timestamp parsing, point cleaning, segmentation.

All functions are pure: they operate on plain Python lists of
``QuotaPoint`` instances and never touch the database or any framework.
``now`` is always passed explicitly (UTC, tz-aware). The default
``now=None`` resolved at API boundary convenience layer is not used
inside this module.

Key responsibilities:

* :func:`parse_iso_utc` — robust ISO 8601 → ``datetime`` parser that
  tolerates trailing ``Z`` and treats naive timestamps as UTC.
* :func:`clean_points` — dedup by ``collected_at`` (keeping the latest),
  drop ``used_percent`` outside [0, 100], drop invalid timestamps,
  drop points whose time goes backward versus the previous one.
* :func:`build_segments` — split the cleaned timeline at detected
  quota resets (spec §4.2). Suspicious drops without a reset signature
  mark the offending point as excluded, but do not break the segment.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from app.analytics import types as t


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------


def parse_iso_utc(value) -> datetime | None:
    """Parse an ISO 8601 timestamp into a UTC-aware ``datetime``.

    * Accepts trailing ``Z`` (Zulu) as UTC.
    * Naive timestamps are interpreted as UTC.
    * Returns ``None`` on any failure (including ``None`` input).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if not isinstance(value, str):
        try:
            text = str(value)
        except Exception:
            return None
    else:
        text = value
    if not text:
        return None
    s = text.strip()
    if not s:
        return None
    # Normalise trailing Z to +00:00 for fromisoformat compatibility.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------


def _coerce_percent(point: t.QuotaPoint) -> float | None:
    """Best-effort ``used_percent`` resolution for a point."""
    if point.used_percent is not None:
        return float(point.used_percent)
    if point.used is not None and point.limit_value not in (None, 0):
        try:
            return float(point.used) / float(point.limit_value) * 100.0
        except (TypeError, ZeroDivisionError, ValueError):
            return None
    return None


def clean_points(
    points: list[t.QuotaPoint],
    cfg,
) -> tuple[list[t.QuotaPoint], int]:
    """Validate and deduplicate the incoming list.

    Returns a tuple ``(cleaned, dropped_count)`` where ``cleaned`` is
    sorted ascending by ``collected_at`` after the following steps:

    1. Drop points whose ``collected_at`` cannot be parsed.
    2. Drop points with ``used_percent`` outside [0, 100] when the
       field is provided (None values are tolerated).
    3. Deduplicate by ``collected_at`` (the *last* point with the same
       timestamp wins).
    4. Drop points whose parsed time is not strictly greater than the
       previously accepted timestamp (catches both equal-timestamp
       duplicates resolved above and "backward" timestamps). After
       this filter, timestamps are guaranteed to be strictly monotonic.
    """
    dropped = 0
    bucket: dict[datetime, t.QuotaPoint] = {}

    for point in points:
        ts = parse_iso_utc(point.collected_at)
        if ts is None:
            dropped += 1
            continue
        if point.used_percent is not None:
            try:
                pct = float(point.used_percent)
            except (TypeError, ValueError):
                dropped += 1
                continue
            if pct < 0.0 or pct > 100.0:
                dropped += 1
                continue
        bucket[ts] = point  # last write wins

    sorted_items = sorted(bucket.items(), key=lambda kv: kv[0])

    cleaned: list[t.QuotaPoint] = []
    last_ts: datetime | None = None
    for ts, pt in sorted_items:
        if last_ts is not None and ts <= last_ts:
            # Should not happen after the dict-based dedup, but guard
            # against microsecond collisions the dict still preserves
            # in their original insertion sequence.
            dropped += 1
            continue
        cleaned.append(pt)
        last_ts = ts
    return cleaned, dropped


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------


def _drop_between(prev: t.QuotaPoint, cur: t.QuotaPoint) -> float:
    """Return signed drop (prev - cur) in percentage points, or 0."""
    p_pct = _coerce_percent(prev)
    c_pct = _coerce_percent(cur)
    if p_pct is not None and c_pct is not None:
        return float(p_pct) - float(c_pct)
    return 0.0


def _reset_sign(
    prev: t.QuotaPoint,
    cur: t.QuotaPoint,
    cur_ts: datetime,
) -> bool:
    """Return ``True`` when the (prev → cur) transition is a real reset."""
    if prev.reset_at and cur.reset_at and prev.reset_at != cur.reset_at:
        return True
    if prev.reset_at:
        prev_reset = parse_iso_utc(prev.reset_at)
        if prev_reset is not None and cur_ts >= prev_reset:
            return True
    return False


def build_segments(
    points: list[t.QuotaPoint],
    cfg,
    now: datetime,
    is_balance: bool = False,
    window_type: str = "unknown",
    account: str = "",
) -> list[t.Segment]:
    """Split a cleaned, time-sorted point list into reset-aware segments.

    For each consecutive ``(prev, cur)`` transition:

    * ``drop = prev.used_percent − cur.used_percent`` (or derived from
      ``used`` / ``limit_value`` when percentages are missing).
    * ``reset_sign`` is true when both ``reset_at`` values are known
      and differ, or when ``cur.collected_at ≥ prev.reset_at``.

    Then:

    * ``drop > reset_drop_min_pp`` AND reset_sign → close the current
      segment and start a new one (``has_reset_boundary=True``).
    * ``drop > reset_jitter_pp`` AND NOT reset_sign → keep the segment
      but exclude ``cur`` (increment ``excluded_points``).
    * All other transitions keep both points in the current segment.

    For balance / credits windows (``is_balance=True``) the reset logic
    is disabled: balance drops are normal spend, no boundaries are
    detected and no points are excluded.

    Empty input or a single point returns a single segment containing
    those points (if any). No exception is raised.
    """
    reset_drop = float(getattr(cfg, "reset_drop_min_pp", 5.0))
    jitter = float(getattr(cfg, "reset_jitter_pp", 2.0))

    segments: list[t.Segment] = []
    current = t.Segment(window_type=window_type, account=account, unit=None)
    segments.append(current)

    if not points:
        return segments

    def _push(pt: t.QuotaPoint) -> None:
        current.points.append(pt)
        if pt.reset_at:
            current.reset_at = pt.reset_at

    if len(points) == 1:
        _push(points[0])
        current.reset_at = points[0].reset_at
        return segments

    # Push the first point unconditionally.
    _push(points[0])

    for prev, cur in zip(points, points[1:]):
        cur_ts = parse_iso_utc(cur.collected_at) or now
        if is_balance:
            # Balance / credits: no reset detection, no exclusions.
            _push(cur)
            continue

        drop = _drop_between(prev, cur)
        sign = _reset_sign(prev, cur, cur_ts)

        if drop > reset_drop and sign:
            # Close current, start a new segment with the boundary flag.
            current = t.Segment(
                window_type=window_type,
                account=account,
                unit=None,
                has_reset_boundary=True,
            )
            segments.append(current)
            _push(cur)
            continue

        if drop > jitter and not sign:
            # Suspicious drop without reset signal — exclude the point.
            current.excluded_points += 1
            continue

        _push(cur)

    # Propagate the most recent reset_at we know about for each segment.
    for seg in segments:
        if seg.points and seg.reset_at is None:
            seg.reset_at = seg.points[-1].reset_at

    return segments


__all__ = [
    "parse_iso_utc",
    "clean_points",
    "build_segments",
]