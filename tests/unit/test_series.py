"""Series-level tests: parse_iso_utc, clean_points, build_segments."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.analytics import types as t
from app.analytics.series import build_segments, clean_points, parse_iso_utc


CFG = SimpleNamespace(
    burn_min_points=3,
    burn_min_span_minutes=5.0,
    reset_drop_min_pp=5.0,
    reset_jitter_pp=2.0,
    accel_baseline_min=1.0,
    week_min_elapsed_pct=3.0,
)


def _point(collected_at, used_percent=None, used=None, limit_value=None,
           reset_at=None, reset_estimated=False, unit=None):
    return t.QuotaPoint(
        collected_at=collected_at,
        used=used,
        remaining=None,
        limit_value=limit_value,
        used_percent=used_percent,
        unit=unit,
        reset_at=reset_at,
        reset_estimated=reset_estimated,
    )


# ---------------------------------------------------------------------------
# parse_iso_utc
# ---------------------------------------------------------------------------


def test_parse_iso_utc_accepts_zulu():
    dt = parse_iso_utc("2026-08-24T12:00:00Z")
    assert dt == datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def test_parse_iso_utc_accepts_offset():
    dt = parse_iso_utc("2026-08-24T14:00:00+02:00")
    assert dt == datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def test_parse_iso_utc_treats_naive_as_utc():
    dt = parse_iso_utc("2026-08-24T12:00:00")
    assert dt == datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def test_parse_iso_utc_invalid_returns_none():
    assert parse_iso_utc("not a date") is None
    assert parse_iso_utc(None) is None
    assert parse_iso_utc("") is None


# ---------------------------------------------------------------------------
# clean_points
# ---------------------------------------------------------------------------


def test_clean_points_drops_out_of_range_percent():
    points = [
        _point("2026-08-24T12:00:00Z", used_percent=10.0),
        _point("2026-08-24T12:30:00Z", used_percent=150.0),  # invalid
        _point("2026-08-24T13:00:00Z", used_percent=-5.0),   # invalid
        _point("2026-08-24T13:30:00Z", used_percent=20.0),
    ]
    cleaned, dropped = clean_points(points, CFG)
    assert dropped == 2
    assert len(cleaned) == 2
    assert [p.used_percent for p in cleaned] == [10.0, 20.0]


def test_clean_points_drops_backward_time():
    base = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    # Two points claim the same collected_at; the second one (with
    # higher timestamp due to microseconds) is dropped because the
    # time does not advance relative to the previous kept point.
    points = [
        _point(base.isoformat(), used_percent=10.0),
        _point(base.isoformat(), used_percent=11.0),
        _point((base.replace(minute=30)).isoformat(), used_percent=15.0),
    ]
    cleaned, dropped = clean_points(points, CFG)
    # Last-write-wins keeps 11.0 (replaces 10.0 in the bucket); no extra drop.
    assert dropped == 0
    assert len(cleaned) == 2
    assert [p.used_percent for p in cleaned] == [11.0, 15.0]


def test_clean_points_out_of_order_dedups():
    base = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    # When duplicates exist with the same timestamp, dedup is by last write.
    points = [
        _point(base.isoformat(), used_percent=10.0),
        _point((base.replace(minute=30)).isoformat(), used_percent=15.0),
        _point(base.isoformat(), used_percent=12.0),  # replaces 10.0
        _point((base.replace(minute=45)).isoformat(), used_percent=25.0),
    ]
    cleaned, dropped = clean_points(points, CFG)
    assert dropped == 0
    assert len(cleaned) == 3
    assert [p.used_percent for p in cleaned] == [12.0, 15.0, 25.0]


def test_clean_points_dedupes_by_timestamp_last_wins():
    points = [
        _point("2026-08-24T12:00:00Z", used_percent=10.0),
        _point("2026-08-24T12:00:00Z", used_percent=11.0),
        _point("2026-08-24T12:30:00Z", used_percent=15.0),
    ]
    cleaned, dropped = clean_points(points, CFG)
    assert dropped == 0
    assert len(cleaned) == 2
    assert cleaned[0].used_percent == 11.0
    assert cleaned[1].used_percent == 15.0


def test_clean_points_drops_invalid_timestamps():
    points = [
        _point("not a date", used_percent=10.0),
        _point("2026-08-24T12:00:00Z", used_percent=15.0),
    ]
    cleaned, dropped = clean_points(points, CFG)
    assert dropped == 1
    assert len(cleaned) == 1


# ---------------------------------------------------------------------------
# build_segments — reset detection
# ---------------------------------------------------------------------------


def test_reset_no_negative_burn():
    base = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    from datetime import timedelta
    points = [
        _point(base.isoformat(), used_percent=20.0,
               reset_at=(base + timedelta(hours=4)).isoformat()),
        _point((base + timedelta(minutes=30)).isoformat(), used_percent=25.0,
               reset_at=(base + timedelta(hours=4)).isoformat()),
        _point((base + timedelta(hours=1)).isoformat(), used_percent=30.0,
               reset_at=(base + timedelta(hours=4)).isoformat()),
        # Reset boundary: drop > 5 with a new reset_at.
        _point((base + timedelta(hours=1, minutes=30)).isoformat(), used_percent=3.0,
               reset_at=(base + timedelta(hours=6)).isoformat()),
        _point((base + timedelta(hours=2)).isoformat(), used_percent=5.0,
               reset_at=(base + timedelta(hours=6)).isoformat()),
        _point((base + timedelta(hours=2, minutes=30)).isoformat(), used_percent=8.0,
               reset_at=(base + timedelta(hours=6)).isoformat()),
    ]
    cleaned, _ = clean_points(points, CFG)
    segments = build_segments(cleaned, CFG, now=base + timedelta(hours=2, minutes=30))
    assert len(segments) == 2
    assert segments[0].has_reset_boundary is False
    assert segments[1].has_reset_boundary is True
    assert segments[0].excluded_points == 0
    assert segments[1].excluded_points == 0
    assert len(segments[0].points) == 3
    assert len(segments[1].points) == 3
    # No negative percentages survived.
    for seg in segments:
        for p in seg.points:
            assert p.used_percent >= 0


def test_drop_without_reset_sign_excludes_point():
    base = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    from datetime import timedelta
    points = [
        _point(base.isoformat(), used_percent=50.0,
               reset_at=(base + timedelta(hours=1)).isoformat()),
        _point((base + timedelta(minutes=15)).isoformat(), used_percent=40.0,
               reset_at=(base + timedelta(hours=1)).isoformat()),  # drop>5, no reset signal
        _point((base + timedelta(minutes=30)).isoformat(), used_percent=55.0,
               reset_at=(base + timedelta(hours=1)).isoformat()),
    ]
    cleaned, _ = clean_points(points, CFG)
    segments = build_segments(cleaned, CFG, now=base + timedelta(minutes=30))
    assert len(segments) == 1
    assert segments[0].excluded_points == 1
    kept = [p.used_percent for p in segments[0].points]
    assert kept == [50.0, 55.0]


def test_balance_never_segments():
    base = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    from datetime import timedelta
    points = [
        _point(base.isoformat(), used=42.3, limit_value=100.0, unit="USD",
               reset_at=(base + timedelta(hours=1)).isoformat()),
        _point((base + timedelta(minutes=30)).isoformat(), used=41.1, limit_value=100.0,
               unit="USD", reset_at=(base + timedelta(hours=1)).isoformat()),
        _point((base + timedelta(hours=1)).isoformat(), used=38.9, limit_value=100.0,
               unit="USD", reset_at=(base + timedelta(hours=1)).isoformat()),
    ]
    cleaned, _ = clean_points(points, CFG)
    segments = build_segments(cleaned, CFG, now=base + timedelta(hours=1), is_balance=True)
    assert len(segments) == 1
    assert segments[0].excluded_points == 0
    assert len(segments[0].points) == 3


def test_build_segments_handles_single_point():
    base = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    points = [_point(base.isoformat(), used_percent=10.0)]
    cleaned, _ = clean_points(points, CFG)
    segments = build_segments(cleaned, CFG, now=base)
    assert len(segments) == 1
    assert len(segments[0].points) == 1


def test_build_segments_handles_empty_input():
    segments = build_segments([], CFG, now=datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc))
    assert len(segments) == 1
    assert segments[0].points == []


def test_build_segments_reset_sign_via_cur_past_prev_reset_at():
    base = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    # prev.reset_at == 12:15, cur at 12:20 → cur_time >= prev.reset_at.
    points = [
        _point(base.isoformat(), used_percent=80.0,
               reset_at=(base.replace(minute=15)).isoformat()),
        _point((base.replace(minute=20)).isoformat(), used_percent=85.0,
               reset_at=(base.replace(minute=15)).isoformat()),
        # Big drop after the reset window opens.
        _point((base.replace(minute=25)).isoformat(), used_percent=10.0,
               reset_at=(base.replace(minute=15)).isoformat()),
    ]
    cleaned, _ = clean_points(points, CFG)
    segments = build_segments(cleaned, CFG, now=base.replace(minute=25))
    assert len(segments) == 2
    assert segments[1].has_reset_boundary is True