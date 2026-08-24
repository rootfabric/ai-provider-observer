"""f_runway factor: monetary balance runway must raise window risk (§14/§16)."""
from __future__ import annotations

from types import SimpleNamespace

from app.analytics import risk
from app.analytics import types as t


def _cfg():
    return SimpleNamespace(
        alert_warning_used=70.0,
        alert_high_used=85.0,
        alert_critical_used=95.0,
        balance_low_days=7.0,
    )


def _wa(runway_days: float | None, status: str = "ok") -> t.WindowAnalytics:
    return t.WindowAnalytics(
        provider="deepseek",
        account="default",
        window_type="balance",
        latest_remaining=(20.0 if runway_days is not None else None),
        unit="USD",
        runway=t.Runway(
            currency="USD",
            balance_total=20.0 if runway_days is not None else None,
            usd_per_day=2.0,
            runway_days=runway_days,
            status=status,
        ),
    )


def test_low_runway_raises_score_to_warning_plus():
    score, factors = risk.score_window(_wa(3.0), _cfg())
    assert factors["f_runway"] == 75.0
    assert score >= 50  # at least WARNING band


def test_critical_runway_scores_high():
    score, factors = risk.score_window(_wa(0.8), _cfg())
    assert factors["f_runway"] == 90.0
    assert score >= 70  # HIGH band


def test_unknown_runway_contributes_zero():
    _, factors_none = risk.score_window(_wa(None), _cfg())
    assert factors_none["f_runway"] == 0.0


def test_healthy_runway_is_mild():
    _, factors = risk.score_window(_wa(30.0), _cfg())
    assert factors["f_runway"] == 10.0


def test_bottleneck_balance_when_only_window():
    wa = _wa(2.5)
    assessment = risk.assess_provider([wa], recent_errors=0, recent_polls=10, cfg=_cfg())
    assert assessment.bottleneck == "balance"
    assert assessment.level in ("WARNING", "HIGH")
