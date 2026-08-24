"""Weekly pacing and projection tests (spec §4.5)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.analytics import types as t
from app.analytics.pacing import compute_pacing


CFG = SimpleNamespace(
    burn_min_points=3,
    burn_min_span_minutes=5.0,
    reset_drop_min_pp=5.0,
    reset_jitter_pp=2.0,
    accel_baseline_min=1.0,
    week_min_elapsed_pct=3.0,
)


def _point(at, used_percent=None, reset_at=None, limit_value=None):
    return t.QuotaPoint(
        collected_at=at.isoformat() if hasattr(at, "isoformat") else at,
        used=None,
        remaining=None,
        limit_value=limit_value,
        used_percent=used_percent,
        unit=None,
        reset_at=reset_at.isoformat() if hasattr(reset_at, "isoformat") else reset_at,
        reset_estimated=False,
    )


def _burn(label, value):
    return t.BurnStat(
        lookback=label,
        value=value,
        unit="percentage_points_per_hour",
        points_used=3,
        span_minutes=60.0,
        status=t.STATUS_OK,
    )


def test_pace_2x_projected_200_critical():
    # Weekly window: reset_at is now+5d, so week_start = reset_at - 7d.
    # now is 25% into the week, used_percent = 50.
    # pace_ratio = 50 / 25 = 2.0 → critical, projected_whole = 50 / 0.25 = 200.
    reset_at = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    week_start = reset_at - timedelta(days=7)
    elapsed = timedelta(days=7) * 0.25  # exactly 25% of the week
    now = week_start + elapsed
    assert (now - week_start).total_seconds() / (7 * 86400) == pytest.approx(0.25, rel=1e-9)
    segment = [_point(week_start, used_percent=0.0, reset_at=reset_at),
               _point(now, used_percent=50.0, reset_at=reset_at)]
    burns = {"24h": _burn("24h", 1.0), "3d": _burn("3d", 1.0)}
    pacing = compute_pacing(segment, burns, now, CFG)
    assert pacing is not None
    assert pacing.pace_ratio == pytest.approx(2.0, rel=1e-9)
    assert pacing.projected_whole_window == pytest.approx(200.0, rel=1e-9)
    assert pacing.pace_band == "critical"


def test_pacing_min_elapsed_guard():
    reset_at = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    week_start = reset_at - timedelta(days=7)
    # now is 1% into the week.
    now = week_start + timedelta(days=7) * 0.01
    segment = [_point(week_start, used_percent=0.0, reset_at=reset_at),
               _point(now, used_percent=5.0, reset_at=reset_at)]
    burns = {"24h": _burn("24h", 1.0), "3d": _burn("3d", 1.0)}
    pacing = compute_pacing(segment, burns, now, CFG)
    assert pacing is not None
    assert pacing.pace_ratio is None
    assert pacing.projected_whole_window is None


def test_projections_from_burns():
    # used 40%, 96h left, burn_24h = 2.5 pp/h, burn_3d = 2.5 pp/h.
    # projected_pace_24h = 40 + 2.5 * 96 = 280.
    reset_at = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    week_start = reset_at - timedelta(days=7)
    now = week_start + timedelta(days=3)  # 3 days in, 4 days left = 96h
    segment = [_point(week_start, used_percent=0.0, reset_at=reset_at),
               _point(now, used_percent=40.0, reset_at=reset_at)]
    burns = {"24h": _burn("24h", 2.5), "3d": _burn("3d", 2.5)}
    pacing = compute_pacing(segment, burns, now, CFG)
    assert pacing is not None
    assert pacing.projected_pace_24h == pytest.approx(280.0, rel=1e-9)
    assert pacing.projected_pace_3d == pytest.approx(280.0, rel=1e-9)


def test_pacing_returns_none_when_segment_empty():
    pacing = compute_pacing([], {}, datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc), CFG)
    assert pacing is None


def test_pacing_band_comfortable_when_well_under_pace():
    reset_at = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    week_start = reset_at - timedelta(days=7)
    elapsed = timedelta(days=7) * (5 / 7)  # 5/7 ≈ 71.4% elapsed
    now = week_start + elapsed
    segment = [_point(week_start, used_percent=0.0, reset_at=reset_at),
               _point(now, used_percent=20.0, reset_at=reset_at)]
    burns = {"24h": _burn("24h", 1.0), "3d": _burn("3d", 1.0)}
    pacing = compute_pacing(segment, burns, now, CFG)
    assert pacing is not None
    # used_percent (20) / elapsed_percent (71.43) ≈ 0.28 → comfortable.
    assert pacing.pace_band == "comfortable"
    expected_ratio = 20.0 / (100.0 * 5 / 7)
    assert pacing.pace_ratio == pytest.approx(expected_ratio, rel=1e-6)


def test_pacing_confidence_low_when_short_span():
    reset_at = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    week_start = reset_at - timedelta(days=7)
    now = week_start + timedelta(days=2)
    # Only 10-minute span between two points.
    p1 = week_start + timedelta(days=2)
    p2 = p1 + timedelta(minutes=10)
    segment = [_point(p1, used_percent=0.0, reset_at=reset_at),
               _point(p2, used_percent=20.0, reset_at=reset_at)]
    burns = {"24h": _burn("24h", 1.0), "3d": _burn("3d", 1.0)}
    pacing = compute_pacing(segment, burns, p2, CFG)
    assert pacing is not None
    assert pacing.confidence == t.CONFIDENCE_LOW


def test_pacing_confidence_medium_when_span_under_day():
    reset_at = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    week_start = reset_at - timedelta(days=7)
    now = week_start + timedelta(days=2)
    # 60-minute span → MEDIUM.
    p1 = week_start + timedelta(days=2)
    p2 = p1 + timedelta(minutes=60)
    segment = [_point(p1, used_percent=0.0, reset_at=reset_at),
               _point(p2, used_percent=20.0, reset_at=reset_at)]
    burns = {"24h": _burn("24h", 1.0), "3d": _burn("3d", 1.0)}
    pacing = compute_pacing(segment, burns, p2, CFG)
    assert pacing is not None
    assert pacing.confidence == t.CONFIDENCE_MEDIUM


def test_pacing_confidence_high_when_long_span():
    reset_at = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    week_start = reset_at - timedelta(days=7)
    now = week_start + timedelta(days=3)
    # 30h span → HIGH.
    p1 = week_start + timedelta(days=2)
    p2 = p1 + timedelta(hours=30)
    segment = [_point(p1, used_percent=0.0, reset_at=reset_at),
               _point(p2, used_percent=20.0, reset_at=reset_at)]
    burns = {"24h": _burn("24h", 1.0), "3d": _burn("3d", 1.0)}
    pacing = compute_pacing(segment, burns, p2, CFG)
    assert pacing is not None
    assert pacing.confidence == t.CONFIDENCE_HIGH