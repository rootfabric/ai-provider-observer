"""Recommendation engine (M3 / §12-§13, §25-§26)."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.analytics import types as t
from app.analytics.plans import (
    PlanInfo,
    PlansConfig,
    ProviderPlans,
    next_sufficient_plan,
)
from app.analytics.recommendation import recommend_for_provider
from app.analytics import risk as risk_mod


# ---------------------------------------------------------------------------
# Local cfg stand-in.
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
    event_cooldown_minutes: float = 30.0


CFG = _Cfg()


def _wa(
    window_type: str,
    *,
    used_percent: float | None = None,
    eta_current_seconds: float | None = None,
    survival_margin_seconds: float | None = None,
    pace_band: str | None = None,
    projected_whole_window: float | None = None,
    runway_days: float | None = None,
    accel_band: str | None = None,
    accel_ratio: float | None = None,
) -> t.WindowAnalytics:
    return t.WindowAnalytics(
        provider="zai",
        account="default",
        window_type=window_type,
        latest_used_percent=used_percent,
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
        runway=(
            t.Runway(runway_days=runway_days) if runway_days is not None else None
        ),
    )


def _no_plans() -> PlansConfig:
    return PlansConfig(source="none", providers={})


def _plans_configured() -> PlansConfig:
    return PlansConfig(
        source="configured",
        providers={
            "zai": ProviderPlans(
                provider="zai",
                current_plan="lite",
                plans=[
                    PlanInfo(name="lite", weekly_capacity=10000.0),
                    PlanInfo(name="pro", weekly_capacity=50000.0),
                ],
            )
        },
    )


# ---------------------------------------------------------------------------
# reason_contains_numbers
# ---------------------------------------------------------------------------
def test_reason_contains_numbers_for_143_percent_projection() -> None:
    was = [
        _wa(
            "weekly",
            projected_whole_window=143.0,
            pace_band="unsustainable",
        )
    ]
    risk = risk_mod.assess_provider(was, recent_errors=0, recent_polls=10, cfg=CFG)

    rec = recommend_for_provider(
        provider="zai",
        was=was,
        risk=risk,
        plans_info=_no_plans(),
        peer_providers=[],
        cfg=CFG,
    )

    assert rec.required_capacity_ratio == pytest_approx(1.43)
    assert rec.recommended_capacity_ratio == pytest_approx(1.7875)
    assert rec.plan_headroom == pytest_approx(1.7875)
    assert rec.action == t.ACTION_INCREASE_BUDGET  # no plans -> budget

    joined = "\n".join(rec.reason_lines)
    assert "143" in joined
    assert "1.79" in joined


# Local pytest_approx shim to avoid importing pytest at top of file for tests
# that don't need it (works with or without pytest installed).
class _Approx:
    def __init__(self, value: float, rel: float = 1e-3) -> None:
        self.value = value
        self.rel = rel

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, (int, float)):
            return False
        return abs(other - self.value) <= max(self.rel, 1e-9) * max(abs(self.value), abs(other))


def pytest_approx(value: float) -> _Approx:
    return _Approx(value)


# ---------------------------------------------------------------------------
# upgrade_vs_budget
# ---------------------------------------------------------------------------
def test_projection_130_with_no_plans_yields_increase_budget() -> None:
    was = [
        _wa(
            "weekly",
            projected_whole_window=130.0,
            pace_band="unsustainable",
        )
    ]
    risk = risk_mod.assess_provider(was, recent_errors=0, recent_polls=10, cfg=CFG)

    rec = recommend_for_provider(
        provider="zai",
        was=was,
        risk=risk,
        plans_info=_no_plans(),
        peer_providers=[],
        cfg=CFG,
    )
    assert rec.action == t.ACTION_INCREASE_BUDGET
    assert rec.next_plan is None


def test_projection_130_with_configured_plans_yields_upgrade_plan() -> None:
    was = [
        _wa(
            "weekly",
            projected_whole_window=130.0,
            pace_band="unsustainable",
        )
    ]
    risk = risk_mod.assess_provider(was, recent_errors=0, recent_polls=10, cfg=CFG)

    pc = _plans_configured()
    # Sanity: we'd need 1.30 * 10000 = 13000; "pro" has 50000 -> enough.
    assert next_sufficient_plan(pc, "zai", 1.30) == "pro"

    rec = recommend_for_provider(
        provider="zai",
        was=was,
        risk=risk,
        plans_info=pc,
        peer_providers=[],
        cfg=CFG,
    )
    assert rec.action == t.ACTION_UPGRADE_PLAN
    assert rec.next_plan == "pro"
    assert rec.capacity_source == "configured"


# ---------------------------------------------------------------------------
# shift_traffic
# ---------------------------------------------------------------------------
def test_shift_traffic_when_five_hour_critical_with_negative_margin() -> None:
    five_hour = _wa(
        "five_hour",
        used_percent=96.0,
        eta_current_seconds=3600.0,
        survival_margin_seconds=-7200.0,
    )
    was = [five_hour]
    risk = risk_mod.assess_provider(was, recent_errors=0, recent_polls=10, cfg=CFG)
    assert risk.level in {t.LEVEL_HIGH, t.LEVEL_CRITICAL}

    rec = recommend_for_provider(
        provider="zai",
        was=was,
        risk=risk,
        plans_info=_no_plans(),
        peer_providers=["minimax", "codex"],
        cfg=CFG,
    )
    assert rec.action == t.ACTION_SHIFT_TRAFFIC
    assert rec.shift_targets == ["minimax", "codex"]


def test_no_shift_when_no_peers() -> None:
    five_hour = _wa(
        "five_hour",
        used_percent=96.0,
        eta_current_seconds=3600.0,
        survival_margin_seconds=-7200.0,
    )
    was = [five_hour]
    risk = risk_mod.assess_provider(was, recent_errors=0, recent_polls=10, cfg=CFG)

    rec = recommend_for_provider(
        provider="zai",
        was=was,
        risk=risk,
        plans_info=_no_plans(),
        peer_providers=[],
        cfg=CFG,
    )
    assert rec.action != t.ACTION_SHIFT_TRAFFIC


# ---------------------------------------------------------------------------
# runway_low
# ---------------------------------------------------------------------------
def test_runway_low_yields_increase_budget() -> None:
    was = [
        _wa("credits", runway_days=3.2),
        _wa("weekly", used_percent=40.0),
    ]
    risk = risk_mod.assess_provider(was, recent_errors=0, recent_polls=10, cfg=CFG)

    rec = recommend_for_provider(
        provider="zai",
        was=was,
        risk=risk,
        plans_info=_no_plans(),
        peer_providers=[],
        cfg=CFG,
    )
    assert rec.action == t.ACTION_INCREASE_BUDGET
    assert any("3.2" in line for line in rec.reason_lines)


def test_runway_above_threshold_does_not_trigger() -> None:
    was = [
        _wa("credits", runway_days=14.0),
    ]
    risk = risk_mod.assess_provider(was, recent_errors=0, recent_polls=10, cfg=CFG)

    rec = recommend_for_provider(
        provider="zai",
        was=was,
        risk=risk,
        plans_info=_no_plans(),
        peer_providers=[],
        cfg=CFG,
    )
    assert rec.action != t.ACTION_INCREASE_BUDGET


# ---------------------------------------------------------------------------
# no_action
# ---------------------------------------------------------------------------
def test_no_action_when_everything_calm() -> None:
    was = [
        _wa("five_hour", used_percent=30.0, eta_current_seconds=86400.0, survival_margin_seconds=72000.0),
        _wa("weekly", used_percent=20.0, pace_band="comfortable", projected_whole_window=40.0),
    ]
    risk = risk_mod.assess_provider(was, recent_errors=0, recent_polls=10, cfg=CFG)

    rec = recommend_for_provider(
        provider="zai",
        was=was,
        risk=risk,
        plans_info=_no_plans(),
        peer_providers=["minimax", "codex"],
        cfg=CFG,
    )
    assert rec.action == t.ACTION_NO_ACTION


# ---------------------------------------------------------------------------
# error path
# ---------------------------------------------------------------------------
def test_recommendation_watch_for_error_bottleneck() -> None:
    was = [_wa("five_hour", used_percent=20.0)]
    # Force errors bottleneck by feeding many recent errors.
    risk = risk_mod.assess_provider(
        was, recent_errors=9, recent_polls=10, cfg=CFG
    )
    assert risk.bottleneck == "errors"

    rec = recommend_for_provider(
        provider="zai",
        was=was,
        risk=risk,
        plans_info=_no_plans(),
        peer_providers=[],
        cfg=CFG,
    )
    assert rec.action == t.ACTION_WATCH
    assert any("error rate" in line.lower() for line in rec.reason_lines)


# ---------------------------------------------------------------------------
# WATCH band from low risk
# ---------------------------------------------------------------------------
def test_watch_band_recommendation() -> None:
    was = [_wa("five_hour", used_percent=33.0, eta_current_seconds=86400.0, survival_margin_seconds=72000.0)]
    risk = risk_mod.assess_provider(was, recent_errors=0, recent_polls=10, cfg=CFG)
    assert risk.level == t.LEVEL_WATCH

    rec = recommend_for_provider(
        provider="zai",
        was=was,
        risk=risk,
        plans_info=_no_plans(),
        peer_providers=[],
        cfg=CFG,
    )
    assert rec.action == t.ACTION_WATCH


# ---------------------------------------------------------------------------
# Required / recommended ratios when projection is below 100%
# ---------------------------------------------------------------------------
def test_required_ratio_floored_to_one_when_projection_low() -> None:
    was = [_wa("weekly", projected_whole_window=40.0, pace_band="comfortable")]
    risk = risk_mod.assess_provider(was, recent_errors=0, recent_polls=10, cfg=CFG)
    rec = recommend_for_provider(
        provider="zai",
        was=was,
        risk=risk,
        plans_info=_no_plans(),
        peer_providers=[],
        cfg=CFG,
    )
    # Required floor is 1.0 (i.e. current plan is enough).
    assert rec.required_capacity_ratio == pytest_approx(1.0)
    assert rec.recommended_capacity_ratio == pytest_approx(1.25)


# ---------------------------------------------------------------------------
# Empty windows -> NO_ACTION with valid envelope.
# ---------------------------------------------------------------------------
def test_recommendation_with_no_windows() -> None:
    risk = t.RiskAssessment(score=0, level=t.LEVEL_HEALTHY, bottleneck="none")
    rec = recommend_for_provider(
        provider="zai",
        was=[],
        risk=risk,
        plans_info=_no_plans(),
        peer_providers=[],
        cfg=CFG,
    )
    assert rec.action == t.ACTION_NO_ACTION
    assert rec.required_capacity_ratio is None
    assert rec.recommended_capacity_ratio is None
    assert rec.capacity_source == "none"
    assert rec.provider == "zai"
    assert rec.shift_targets == []


# ---------------------------------------------------------------------------
# Reason lines have concrete numbers in 90-120% projection (WATCH/upgrade).
# ---------------------------------------------------------------------------
def test_reason_lines_for_95_percent_projection() -> None:
    was = [_wa("weekly", projected_whole_window=95.0, pace_band="elevated")]
    risk = risk_mod.assess_provider(was, recent_errors=0, recent_polls=10, cfg=CFG)
    rec = recommend_for_provider(
        provider="zai",
        was=was,
        risk=risk,
        plans_info=_plans_configured(),
        peer_providers=[],
        cfg=CFG,
    )
    assert rec.action == t.ACTION_UPGRADE_PLAN
    joined = "\n".join(rec.reason_lines)
    assert "95" in joined
    # Required ratio is floored at 1.0 (already at quota but not over),
    # so headroom = 1.0 * 1.25 = 1.25.
    assert "1.25" in joined
