"""Risk scoring, levels, bottleneck detection (M3 / §14-§15, §24)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.analytics import types as t
from app.analytics.risk import (
    alert_level_for,
    assess_provider,
    score_window,
)


# ---------------------------------------------------------------------------
# Local test config (does not import app.config to keep this unit pure).
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class _Cfg:
    alert_warning_used: float = 70.0
    alert_high_used: float = 85.0
    alert_critical_used: float = 95.0
    alert_warning_projected_week: float = 90.0
    alert_critical_projected_week: float = 120.0
    alert_critical_eta_minutes: float = 30.0
    balance_low_days: float = 7.0
    recommended_headroom_factor: float = 1.25


CFG = _Cfg()


# ---------------------------------------------------------------------------
# Builders for WindowAnalytics with controllable factors.
# ---------------------------------------------------------------------------
def _wa(
    *,
    provider: str = "zai",
    account: str = "default",
    window_type: str = "five_hour",
    used_percent: float | None = None,
    eta_current_seconds: float | None = None,
    survival_margin_seconds: float | None = None,
    accel_band: str | None = None,
    accel_ratio: float | None = None,
    pace_band: str | None = None,
    projected_whole_window: float | None = None,
    used_percent_value: float | None = None,  # alias for used_percent
) -> t.WindowAnalytics:
    return t.WindowAnalytics(
        provider=provider,
        account=account,
        window_type=window_type,
        latest_used_percent=(
            used_percent if used_percent is not None else used_percent_value
        ),
        forecast=t.Forecast(
            eta_current_seconds=eta_current_seconds,
            survival_margin_seconds=survival_margin_seconds,
        ),
        burn_acceleration=(
            t.Acceleration(ratio=accel_ratio, band=accel_band, baseline_ok=True)
            if accel_band
            else None
        ),
        pacing=(
            t.Pacing(
                pace_band=pace_band,
                projected_whole_window=projected_whole_window,
            )
            if (pace_band or projected_whole_window is not None)
            else None
        ),
    )


# ---------------------------------------------------------------------------
# alert_level_for — boundary tests
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "used, expected",
    [
        (40.0, t.LEVEL_HEALTHY),
        (69.9, t.LEVEL_HEALTHY),
        (70.0, t.LEVEL_WARNING),
        (84.9, t.LEVEL_WARNING),
        (85.0, t.LEVEL_HIGH),
        (94.9, t.LEVEL_HIGH),
        (95.0, t.LEVEL_CRITICAL),
        (100.0, t.LEVEL_CRITICAL),
    ],
)
def test_alert_level_thresholds_by_used(used: float, expected: str) -> None:
    wa = _wa(used_percent=used)
    assert alert_level_for(wa, CFG) == expected


def test_alert_critical_from_negative_margin() -> None:
    wa = _wa(used_percent=50.0, survival_margin_seconds=-1.0)
    # margin<0 should still drop to HIGH (not CRITICAL) unless also used>=95
    # or eta <= 30 min or projected >= 120.
    assert alert_level_for(wa, CFG) == t.LEVEL_HIGH


def test_alert_critical_from_short_eta() -> None:
    wa = _wa(used_percent=50.0, eta_current_seconds=10 * 60)
    assert alert_level_for(wa, CFG) == t.LEVEL_CRITICAL


def test_alert_critical_from_project() -> None:
    wa = _wa(
        used_percent=50.0,
        pace_band="comfortable",
        projected_whole_window=125.0,
    )
    assert alert_level_for(wa, CFG) == t.LEVEL_CRITICAL


def test_alert_warning_from_project() -> None:
    wa = _wa(
        used_percent=50.0,
        projected_whole_window=92.0,
    )
    assert alert_level_for(wa, CFG) == t.LEVEL_WARNING


def test_alert_no_data_returns_healthy() -> None:
    wa = t.WindowAnalytics(provider="zai", account="default", window_type="five_hour")
    assert alert_level_for(wa, CFG) == t.LEVEL_HEALTHY


# ---------------------------------------------------------------------------
# score_window + assess_provider — level bands (§14)
# ---------------------------------------------------------------------------
def _score_at_used(used: float, **kwargs: Any) -> int:
    wa = _wa(used_percent=used, **kwargs)
    score, _ = score_window(wa, CFG)
    return score


def test_window_score_band_just_below_critical() -> None:
    # used=94.9 -> only f_remaining near 80 (high band), no other factor -> ~80
    assert _score_at_used(94.9) == 80


def test_window_score_band_at_critical_threshold() -> None:
    # used=95.1 -> f_remaining jumps to 95+; no bonus (only one factor >= 55)
    assert _score_at_used(95.1) == 95


def test_window_score_band_below_warning() -> None:
    # used 40% -> ~36 in f_remaining linear band, others zero.
    score = _score_at_used(40.0)
    assert 1 <= score <= 45


def test_window_score_band_warning_level() -> None:
    # used 75% -> f_remaining 55 (just into warning band); no other factor
    assert _score_at_used(75.0) == 55


def test_window_score_band_high_level() -> None:
    # used 90% -> f_remaining 80 (high band); no other factor
    assert _score_at_used(90.0) == 80


def test_window_score_band_critical_level() -> None:
    # used 96% -> f_remaining 95, no other factor -> 95
    assert _score_at_used(96.0) == 95


def test_window_score_factor_with_cap() -> None:
    # used 96% (f_remaining 95) AND anomaly band (f_accel 80) -> two factors >= 55,
    # bonus +5, capped at 100.
    wa = _wa(used_percent=96.0, accel_band="anomaly", accel_ratio=2.5)
    score, factors = score_window(wa, CFG)
    assert factors["f_remaining"] == 95.0
    assert factors["f_accel"] == 80.0
    # base 95 + bonus 5 = 100
    assert score == 100


# ---------------------------------------------------------------------------
# assess_provider — level bands and bottleneck
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "used, expected_level",
    [
        (10.0, t.LEVEL_HEALTHY),
        (29.0, t.LEVEL_HEALTHY),
        (33.0, t.LEVEL_WATCH),
        (50.0, t.LEVEL_WATCH),
        (75.0, t.LEVEL_WARNING),
        (90.0, t.LEVEL_HIGH),
        (96.0, t.LEVEL_CRITICAL),
    ],
)
def test_provider_level_for_single_window(used: float, expected_level: str) -> None:
    wa = _wa(used_percent=used)
    ra = assess_provider([wa], recent_errors=0, recent_polls=10, cfg=CFG)
    assert ra.level == expected_level
    assert ra.bottleneck == "five_hour"


def test_provider_level_30_boundary() -> None:
    # used 40 -> score ~31 -> WATCH (>=30)
    ra = assess_provider([_wa(used_percent=40.0)], 0, 10, CFG)
    assert ra.score >= 30
    assert ra.level == t.LEVEL_WATCH


def test_provider_level_29_boundary_healthy() -> None:
    # used 30 -> f_remaining linear at ~18.6 -> HEALTHY
    ra = assess_provider([_wa(used_percent=30.0)], 0, 10, CFG)
    assert ra.score <= 29
    assert ra.level == t.LEVEL_HEALTHY


def test_provider_no_windows_returns_healthy() -> None:
    ra = assess_provider([], recent_errors=0, recent_polls=0, cfg=CFG)
    assert ra.score == 0
    assert ra.level == t.LEVEL_HEALTHY
    assert ra.bottleneck == "none"
    assert ra.error_penalty == 0
    assert ra.window_scores == {}


def test_provider_bottleneck_five_hour_wins_over_weekly() -> None:
    was = [
        _wa(window_type="five_hour", used_percent=96.0),    # 95
        _wa(window_type="weekly", used_percent=75.0),         # 55
    ]
    ra = assess_provider(was, 0, 10, CFG)
    assert ra.bottleneck == "five_hour"


def test_provider_bottleneck_weekly_wins_over_daily_on_tie() -> None:
    # Both score the same (same used%); weekly beats daily on preference.
    was = [
        _wa(window_type="daily", used_percent=90.0),
        _wa(window_type="weekly", used_percent=90.0),
    ]
    ra = assess_provider(was, 0, 10, CFG)
    assert ra.bottleneck == "weekly"


def test_provider_bottleneck_balance_over_unknown_on_tie() -> None:
    was = [
        _wa(window_type="unknown", used_percent=90.0),
        _wa(window_type="balance", used_percent=90.0),
    ]
    ra = assess_provider(was, 0, 10, CFG)
    assert ra.bottleneck == "balance"


def test_provider_bottleneck_errors_when_errors_dominate() -> None:
    was = [_wa(used_percent=30.0)]
    # 8 errors / 10 polls = 80% -> penalty 30 (cap). Window score ~18, so
    # errors win.
    ra = assess_provider(was, recent_errors=8, recent_polls=10, cfg=CFG)
    assert ra.error_penalty == 30
    assert ra.bottleneck == "errors"


def test_provider_error_penalty_needs_min_polls() -> None:
    was = [_wa(used_percent=30.0)]
    # Only 4 polls -> ignore errors entirely.
    ra = assess_provider(was, recent_errors=2, recent_polls=4, cfg=CFG)
    assert ra.error_penalty == 0
    assert ra.bottleneck == "five_hour"


# ---------------------------------------------------------------------------
# Z.AI example scenario from the brief.
# ---------------------------------------------------------------------------
def test_zai_example_five_hour_high_weekly_calm_bottleneck_five_hour() -> None:
    five_hour = _wa(
        window_type="five_hour",
        used_percent=74.0,
        survival_margin_seconds=-2100.0,
        accel_band="anomaly",
        accel_ratio=2.5,
    )
    weekly = _wa(
        window_type="weekly",
        used_percent=43.0,
        pace_band="elevated",
        projected_whole_window=110.0,
    )
    five_score, five_factors = score_window(five_hour, CFG)
    # Brief expected ~82 (HIGH or above); our factors land in [80, 90].
    assert 75 <= five_score <= 90
    assert five_factors["f_margin"] >= 70.0
    assert five_factors["f_accel"] == 80.0

    weekly_score, _ = score_window(weekly, CFG)
    assert weekly_score < five_score

    ra = assess_provider([five_hour, weekly], 0, 10, CFG)
    assert ra.score >= 70
    assert ra.bottleneck == "five_hour"
    assert ra.level in {t.LEVEL_HIGH, t.LEVEL_CRITICAL}


def test_unsafe_margin_drives_high_risk() -> None:
    # eta 60 minutes (< reset at 3 hours) -> margin = -7200 s
    wa = _wa(
        used_percent=50.0,
        eta_current_seconds=3600.0,
        survival_margin_seconds=-7200.0,
    )
    ra = assess_provider([wa], 0, 10, CFG)
    assert ra.level in {t.LEVEL_HIGH, t.LEVEL_CRITICAL}
    assert ra.bottleneck == "five_hour"


def test_safe_margin_low_used_keeps_low_risk() -> None:
    # Large positive margin, used 40%: should land in WATCH or HEALTHY band.
    wa = _wa(
        used_percent=40.0,
        eta_current_seconds=86400.0,
        survival_margin_seconds=72000.0,
    )
    ra = assess_provider([wa], 0, 10, CFG)
    assert ra.level in {t.LEVEL_HEALTHY, t.LEVEL_WATCH}


def test_factors_aggregated_from_worst() -> None:
    # Two windows; factors dict should reflect the worst per factor.
    was = [
        _wa(window_type="five_hour", used_percent=50.0, accel_band="anomaly", accel_ratio=2.5),
        _wa(window_type="weekly", used_percent=96.0),
    ]
    ra = assess_provider(was, 0, 10, CFG)
    assert ra.factors["f_accel"] == 80.0  # from the anomaly window
    assert ra.factors["f_remaining"] == 95.0  # from the weekly window


def test_window_score_with_insufficient_data() -> None:
    wa = t.WindowAnalytics(
        provider="zai",
        account="default",
        window_type="five_hour",
        latest_used_percent=None,
    )
    score, factors = score_window(wa, CFG)
    # f_margin falls back to 15 when survival_margin is None and no
    # ETA is present (brief: "margin None -> 15 если forecast неизвестен").
    # All other factors are 0 in this scenario.
    assert factors["f_remaining"] == 0.0
    assert factors["f_margin"] == 15.0
    assert factors["f_accel"] == 0.0
    assert factors["f_pace"] == 0.0
    assert factors["f_projected"] == 0.0
    # No factors >= 55 -> no bonus.
    assert score == 15
