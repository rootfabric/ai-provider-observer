"""Forecast / ETA / survival margin tests (spec §4.4, §7-§9)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.analytics import types as t
from app.analytics.forecast import build_forecast


CFG = SimpleNamespace(
    burn_min_points=3,
    burn_min_span_minutes=5.0,
    reset_drop_min_pp=5.0,
    reset_jitter_pp=2.0,
    accel_baseline_min=1.0,
    week_min_elapsed_pct=3.0,
)


def _ok_burn(label, value):
    return t.BurnStat(
        lookback=label,
        value=value,
        unit="percentage_points_per_hour",
        points_used=3,
        span_minutes=60.0,
        status=t.STATUS_OK,
    )


def _insufficient(label):
    return t.BurnStat(
        lookback=label,
        value=None,
        unit="percentage_points_per_hour",
        points_used=0,
        span_minutes=None,
        status=t.STATUS_INSUFFICIENT_DATA,
    )


def test_eta_two_hours_when_remaining_and_burn_match():
    now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    reset = now + timedelta(hours=10)
    latest = t.QuotaPoint(
        collected_at=now.isoformat(),
        used=None,
        remaining=20.0,
        limit_value=100.0,
        used_percent=None,
        unit=None,
        reset_at=reset.isoformat(),
        reset_estimated=False,
    )
    burns = {"15m": _ok_burn("15m", 30.0),
             "1h": _ok_burn("1h", 10.0),
             "3h": _ok_burn("3h", 8.0)}
    fc = build_forecast(latest, burns, now, CFG, segment_span_minutes=180.0)
    # 20 / 10 * 3600 = 7200 seconds (2h).
    assert fc.eta_stable_seconds == pytest.approx(7200.0, rel=1e-9)
    assert fc.eta_current_seconds == pytest.approx(20.0 / 30.0 * 3600.0, rel=1e-9)
    assert fc.eta_conservative_seconds == pytest.approx(20.0 / 8.0 * 3600.0, rel=1e-9)
    assert fc.reset_in_seconds == pytest.approx(10 * 3600.0, rel=1e-9)
    assert fc.survival_margin_seconds == pytest.approx(2400.0 - 36000.0, rel=1e-9)


def test_margin_negative_when_exhaust_first():
    now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    reset = now + timedelta(hours=3)
    latest = t.QuotaPoint(
        collected_at=now.isoformat(),
        used=None,
        remaining=10.0,
        limit_value=100.0,
        used_percent=None,
        unit=None,
        reset_at=reset.isoformat(),
        reset_estimated=False,
    )
    burns = {"15m": _ok_burn("15m", 10.0),
             "1h": _ok_burn("1h", 10.0),
             "3h": _ok_burn("3h", 10.0)}
    fc = build_forecast(latest, burns, now, CFG, segment_span_minutes=180.0)
    # 10 / 10 * 3600 = 3600s (1h); reset in 3h → margin = -7200.
    assert fc.eta_current_seconds == pytest.approx(3600.0, rel=1e-9)
    assert fc.reset_in_seconds == pytest.approx(3 * 3600.0, rel=1e-9)
    assert fc.survival_margin_seconds == pytest.approx(-7200.0, rel=1e-9)


def test_margin_positive_safe():
    now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    reset = now + timedelta(hours=2)
    latest = t.QuotaPoint(
        collected_at=now.isoformat(),
        used=None,
        remaining=40.0,
        limit_value=100.0,
        used_percent=None,
        unit=None,
        reset_at=reset.isoformat(),
        reset_estimated=False,
    )
    burns = {"15m": _ok_burn("15m", 10.0),
             "1h": _ok_burn("1h", 10.0),
             "3h": _ok_burn("3h", 10.0)}
    fc = build_forecast(latest, burns, now, CFG, segment_span_minutes=180.0)
    # 40 / 10 * 3600 = 14400s (4h); reset in 2h → margin = +7200.
    assert fc.eta_current_seconds == pytest.approx(14400.0, rel=1e-9)
    assert fc.reset_in_seconds == pytest.approx(7200.0, rel=1e-9)
    assert fc.survival_margin_seconds == pytest.approx(7200.0, rel=1e-9)
    assert fc.recovery_mode == "hard_reset"


def test_no_reset_unknown_recovery():
    now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    latest = t.QuotaPoint(
        collected_at=now.isoformat(),
        used=None,
        remaining=20.0,
        limit_value=100.0,
        used_percent=None,
        unit=None,
        reset_at=None,
        reset_estimated=False,
    )
    burns = {"15m": _ok_burn("15m", 10.0),
             "1h": _ok_burn("1h", 10.0),
             "3h": _ok_burn("3h", 10.0)}
    fc = build_forecast(latest, burns, now, CFG, segment_span_minutes=60.0)
    assert fc.recovery_mode == "unknown"
    assert fc.reset_in_seconds is None
    assert fc.survival_margin_seconds is None


def test_zero_burn_no_eta():
    now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    latest = t.QuotaPoint(
        collected_at=now.isoformat(),
        used=None,
        remaining=20.0,
        limit_value=100.0,
        used_percent=None,
        unit=None,
        reset_at=None,
    )
    burns = {"15m": _insufficient("15m"),
             "1h": _insufficient("1h"),
             "3h": _insufficient("3h")}
    fc = build_forecast(latest, burns, now, CFG, segment_span_minutes=None)
    assert fc.eta_current_seconds is None
    assert fc.eta_stable_seconds is None
    assert fc.eta_conservative_seconds is None


def test_confidence_bands_by_segment_span():
    now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    latest = t.QuotaPoint(
        collected_at=now.isoformat(),
        used=None,
        remaining=20.0,
        limit_value=100.0,
        used_percent=None,
        unit=None,
        reset_at=None,
    )
    burns = {"15m": _ok_burn("15m", 10.0),
             "1h": _ok_burn("1h", 10.0),
             "3h": _ok_burn("3h", 10.0)}
    fc_low = build_forecast(latest, burns, now, CFG, segment_span_minutes=10.0)
    fc_med = build_forecast(latest, burns, now, CFG, segment_span_minutes=60.0)
    fc_high = build_forecast(latest, burns, now, CFG, segment_span_minutes=180.0)
    fc_none = build_forecast(latest, burns, now, CFG, segment_span_minutes=None)
    assert fc_low.confidence == t.CONFIDENCE_LOW
    assert fc_med.confidence == t.CONFIDENCE_MEDIUM
    assert fc_high.confidence == t.CONFIDENCE_HIGH
    assert fc_none.confidence == t.CONFIDENCE_LOW


def test_estimated_reset_sets_recovery_mode():
    now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    latest = t.QuotaPoint(
        collected_at=now.isoformat(),
        used=None,
        remaining=20.0,
        limit_value=100.0,
        used_percent=None,
        unit=None,
        reset_at=(now + timedelta(hours=5)).isoformat(),
        reset_estimated=True,
    )
    burns = {"15m": _ok_burn("15m", 10.0),
             "1h": _ok_burn("1h", 10.0),
             "3h": _ok_burn("3h", 10.0)}
    fc = build_forecast(latest, burns, now, CFG, segment_span_minutes=180.0)
    assert fc.recovery_mode == "estimated_reset"