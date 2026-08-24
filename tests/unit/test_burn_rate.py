"""Burn rate regression tests (spec §4.3, §6)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.analytics import types as t
from app.analytics.burn_rate import (
    compute_acceleration,
    compute_burn,
    compute_burns,
)


CFG = SimpleNamespace(
    burn_min_points=3,
    burn_min_span_minutes=5.0,
    reset_drop_min_pp=5.0,
    reset_jitter_pp=2.0,
    accel_baseline_min=1.0,
    week_min_elapsed_pct=3.0,
)


def _pct_point(collected_at, used_percent, reset_at=None):
    return t.QuotaPoint(
        collected_at=collected_at,
        used=None,
        remaining=None,
        limit_value=None,
        used_percent=used_percent,
        unit=None,
        reset_at=reset_at,
        reset_estimated=False,
    )


def _abs_point(collected_at, used, unit, limit_value=None, reset_at=None):
    return t.QuotaPoint(
        collected_at=collected_at,
        used=used,
        remaining=None,
        limit_value=limit_value,
        used_percent=None,
        unit=unit,
        reset_at=reset_at,
        reset_estimated=False,
    )


# ---------------------------------------------------------------------------
# compute_burn: linear regression on percentage points
# ---------------------------------------------------------------------------


def test_linear_burn_10pp_per_hour():
    base = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    points = [
        _pct_point(base.isoformat(), 20.0),
        _pct_point((base + timedelta(minutes=30)).isoformat(), 25.0),
        _pct_point((base + timedelta(hours=1)).isoformat(), 30.0),
    ]
    stat = compute_burn(points, 3600.0, base + timedelta(hours=1), None, CFG)
    assert stat.status == t.STATUS_OK
    assert stat.points_used == 3
    assert stat.unit == "percentage_points_per_hour"
    assert stat.value == pytest.approx(10.0, rel=1e-6)
    assert stat.span_minutes == pytest.approx(60.0, rel=1e-9)


def test_insufficient_data_when_too_few_points():
    base = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    points = [
        _pct_point(base.isoformat(), 20.0),
        _pct_point((base + timedelta(minutes=30)).isoformat(), 25.0),
    ]
    stat = compute_burn(points, 3600.0, base + timedelta(minutes=30), None, CFG)
    assert stat.status == t.STATUS_INSUFFICIENT_DATA
    assert stat.value is None  # never 0
    assert stat.points_used == 2


def test_insufficient_data_when_span_too_short():
    base = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    # Three points but only 2 minutes apart (< 5 min threshold).
    points = [
        _pct_point(base.isoformat(), 20.0),
        _pct_point((base + timedelta(minutes=1)).isoformat(), 22.0),
        _pct_point((base + timedelta(minutes=2)).isoformat(), 24.0),
    ]
    stat = compute_burn(points, 3600.0, base + timedelta(minutes=2), None, CFG)
    assert stat.status == t.STATUS_INSUFFICIENT_DATA
    assert stat.value is None


def test_absolute_units_zai_style():
    base = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    # Z.AI-style: used 1220 → 1500 over an hour (limit 2000).
    points = [
        _abs_point(base.isoformat(), 1220.0, "credits", limit_value=2000.0),
        _abs_point((base + timedelta(minutes=30)).isoformat(), 1360.0, "credits", limit_value=2000.0),
        _abs_point((base + timedelta(hours=1)).isoformat(), 1500.0, "credits", limit_value=2000.0),
    ]
    stat = compute_burn(points, 3600.0, base + timedelta(hours=1), "credits", CFG)
    assert stat.status == t.STATUS_OK
    assert stat.unit == "credits/hour"
    assert stat.value == pytest.approx(280.0, rel=1e-6)


def test_compute_burn_empty_input_returns_insufficient():
    stat = compute_burn([], 3600.0, datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc), None, CFG)
    assert stat.status == t.STATUS_INSUFFICIENT_DATA
    assert stat.value is None


# ---------------------------------------------------------------------------
# compute_burns
# ---------------------------------------------------------------------------


def test_compute_burns_returns_standard_keys():
    base = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    points = [
        _pct_point(base.isoformat(), 20.0),
        _pct_point((base + timedelta(minutes=30)).isoformat(), 25.0),
        _pct_point((base + timedelta(hours=1)).isoformat(), 30.0),
    ]
    burns = compute_burns(points, base + timedelta(hours=1), None, CFG, window_type="five_hour")
    assert set(burns.keys()) >= {"15m", "1h", "3h", "window"}
    assert burns["1h"].status == t.STATUS_OK
    assert burns["window"].status == t.STATUS_OK


def test_compute_burns_weekly_adds_24h_and_3d():
    base = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    # 26 hours of synthetic data — enough for 24h window.
    points = []
    for hour in range(27):
        ts = base + timedelta(hours=hour)
        points.append(_pct_point(ts.isoformat(), 20.0 + hour * 1.0))
    burns = compute_burns(points, base + timedelta(hours=26), None, CFG, window_type="weekly")
    assert "24h" in burns
    assert "3d" in burns
    assert burns["24h"].status == t.STATUS_OK
    assert burns["24h"].value == pytest.approx(1.0, rel=1e-6)


# ---------------------------------------------------------------------------
# compute_acceleration
# ---------------------------------------------------------------------------


def test_acceleration_accelerating_band():
    stat_15 = t.BurnStat(lookback="15m", value=12.4, unit="percentage_points_per_hour",
                         status=t.STATUS_OK)
    stat_1h = t.BurnStat(lookback="1h", value=8.1, unit="percentage_points_per_hour",
                         status=t.STATUS_OK)
    accel = compute_acceleration(stat_15, stat_1h, CFG)
    assert accel.baseline_ok is True
    assert accel.ratio == pytest.approx(12.4 / 8.1, rel=1e-9)
    assert accel.band == "accelerating"


def test_acceleration_decelerating_band():
    stat_15 = t.BurnStat(lookback="15m", value=5.0, unit="percentage_points_per_hour",
                         status=t.STATUS_OK)
    stat_1h = t.BurnStat(lookback="1h", value=8.0, unit="percentage_points_per_hour",
                         status=t.STATUS_OK)
    accel = compute_acceleration(stat_15, stat_1h, CFG)
    assert accel.baseline_ok is True
    assert accel.ratio == pytest.approx(0.625, rel=1e-9)
    assert accel.band == "decelerating"


def test_acceleration_anomaly_band():
    stat_15 = t.BurnStat(lookback="15m", value=100.0, unit="percentage_points_per_hour",
                         status=t.STATUS_OK)
    stat_1h = t.BurnStat(lookback="1h", value=10.0, unit="percentage_points_per_hour",
                         status=t.STATUS_OK)
    accel = compute_acceleration(stat_15, stat_1h, CFG)
    assert accel.band == "anomaly"


def test_acceleration_baseline_guard():
    stat_15 = t.BurnStat(lookback="15m", value=5.0, unit="percentage_points_per_hour",
                         status=t.STATUS_OK)
    stat_1h = t.BurnStat(lookback="1h", value=0.3, unit="percentage_points_per_hour",
                         status=t.STATUS_OK)
    accel = compute_acceleration(stat_15, stat_1h, CFG)
    assert accel.ratio is None
    assert accel.band is None
    assert accel.baseline_ok is False


def test_acceleration_stable_band():
    stat_15 = t.BurnStat(lookback="15m", value=8.0, unit="percentage_points_per_hour",
                         status=t.STATUS_OK)
    stat_1h = t.BurnStat(lookback="1h", value=8.0, unit="percentage_points_per_hour",
                         status=t.STATUS_OK)
    accel = compute_acceleration(stat_15, stat_1h, CFG)
    assert accel.band == "stable"


def test_acceleration_rejects_insufficient_burns():
    stat_15 = t.BurnStat(lookback="15m", value=None, unit="percentage_points_per_hour",
                         status=t.STATUS_INSUFFICIENT_DATA)
    stat_1h = t.BurnStat(lookback="1h", value=8.0, unit="percentage_points_per_hour",
                         status=t.STATUS_OK)
    accel = compute_acceleration(stat_15, stat_1h, CFG)
    assert accel.ratio is None
    assert accel.band is None
    assert accel.baseline_ok is False