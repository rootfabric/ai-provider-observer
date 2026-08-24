"""Factor-based risk scoring, levels, bottleneck detection (spec §14-§15, §24).

The score a single window gets in `score_window` is the worst factor
(the most-dangerous limit dominates). The provider-level score in
`assess_provider` is the worst window plus an error penalty capped at 100.
The bottleneck is the window that produced the worst score, with a
strict preference order so ties break to the most time-sensitive
window (five-hour before weekly before monthly ...).

All thresholds live in cfg (already loaded in `app/config.py`); this
module does not import M2 logic — it consumes WindowAnalytics only.
"""

from __future__ import annotations

from typing import Any

from . import types as t


# ---------------------------------------------------------------------------
# Band -> level thresholds (spec §14 table). Inclusive upper bound per band:
# 0..29 HEALTHY, 30..49 WATCH, 50..69 WARNING, 70..84 HIGH, 85..100 CRITICAL.
# ---------------------------------------------------------------------------
def _band_to_level(score: int) -> str:
    if score >= 85:
        return t.LEVEL_CRITICAL
    if score >= 70:
        return t.LEVEL_HIGH
    if score >= 50:
        return t.LEVEL_WARNING
    if score >= 30:
        return t.LEVEL_WATCH
    return t.LEVEL_HEALTHY


# Preference order for breaking ties when two windows have the same score.
# More time-sensitive / more critical windows beat broader budget windows.
_BOTTLENECK_ORDER = (
    "five_hour",
    "weekly",
    "monthly",
    "daily",
    "balance",
    "credits",
    "unknown",
)


def _bottleneck_rank(window_type: str) -> int:
    try:
        return _BOTTLENECK_ORDER.index(window_type)
    except ValueError:
        return len(_BOTTLENECK_ORDER)


def alert_level_for(wa: t.WindowAnalytics, cfg: Any) -> str:
    """Threshold-based alert per spec §24.

    Priority: CRITICAL > HIGH > WARNING > HEALTHY. Insufficient data
    (missing forecast and missing usage) yields HEALTHY to avoid
    spurious alerts.
    """
    used = wa.latest_used_percent
    crit_used = getattr(cfg, "alert_critical_used", 95.0)
    high_used = getattr(cfg, "alert_high_used", 85.0)
    warn_used = getattr(cfg, "alert_warning_used", 70.0)
    crit_projected = getattr(cfg, "alert_critical_projected_week", 120.0)
    warn_projected = getattr(cfg, "alert_warning_projected_week", 90.0)
    crit_eta_minutes = getattr(cfg, "alert_critical_eta_minutes", 30.0)

    def _critical_reasons() -> bool:
        if used is not None and used >= crit_used:
            return True
        if (
            wa.forecast.eta_current_seconds is not None
            and wa.forecast.eta_current_seconds <= crit_eta_minutes * 60.0
        ):
            return True
        projected = _projected(wa)
        if projected is not None and projected >= crit_projected:
            return True
        return False

    def _high_reasons() -> bool:
        if used is not None and used >= high_used:
            return True
        margin = wa.forecast.survival_margin_seconds
        if margin is not None and margin < 0:
            return True
        return False

    def _warning_reasons() -> bool:
        if used is not None and used >= warn_used:
            return True
        projected = _projected(wa)
        if projected is not None and projected >= warn_projected:
            return True
        return False

    if _critical_reasons():
        return t.LEVEL_CRITICAL
    if _high_reasons():
        return t.LEVEL_HIGH
    if _warning_reasons():
        return t.LEVEL_WARNING
    return t.LEVEL_HEALTHY


def _projected(wa: t.WindowAnalytics) -> float | None:
    if wa.pacing is None:
        return None
    return wa.pacing.projected_whole_window


def _pace_band(band: str | None) -> str:
    return band or ""


def _accel_band(ratio: float | None) -> str:
    if wa_accel := ratio:
        # The caller already gave us the ratio here, but we keep an
        # explicit band derivation to make the threshold table local.
        if wa_accel < 0.7:
            return "decelerating"
        if wa_accel < 1.3:
            return "stable"
        if wa_accel < 2.0:
            return "accelerating"
        return "anomaly"
    return ""


def score_window(wa: t.WindowAnalytics, cfg: Any) -> tuple[int, dict[str, float]]:
    """Compute factor scores for one window, returning (window_score, factors).

    The score is `max(factors)` with a +5 bonus when two or more
    factors are at or above 55 (multi-factor danger). Cap is 100.
    """
    crit_used = getattr(cfg, "alert_critical_used", 95.0)
    high_used = getattr(cfg, "alert_high_used", 85.0)
    warn_used = getattr(cfg, "alert_warning_used", 70.0)

    factors: dict[str, float] = {}
    factors["f_remaining"] = _factor_remaining(wa.latest_used_percent, warn_used, high_used, crit_used)
    factors["f_margin"] = _factor_margin(wa.forecast.survival_margin_seconds, wa.forecast.eta_current_seconds)
    factors["f_accel"] = _factor_accel(wa.burn_acceleration)
    factors["f_pace"] = _factor_pace(wa.pacing)
    factors["f_projected"] = _factor_projected(wa.pacing)

    base_max = max(factors.values()) if factors else 0
    elevated = sum(1 for v in factors.values() if v >= 55)
    bonus = 5 if elevated >= 2 else 0
    score = int(round(min(100, base_max + bonus)))
    return score, factors


def _factor_remaining(
    used_percent: float | None,
    warn_used: float,
    high_used: float,
    crit_used: float,
) -> float:
    if used_percent is None:
        return 0.0
    if used_percent >= crit_used:
        return 95.0
    if used_percent >= high_used:
        return 80.0
    if used_percent >= warn_used:
        return 55.0
    # Linear in [0, warn_used) -> 10..55
    if used_percent <= 0:
        return 10.0
    fraction = min(1.0, used_percent / warn_used)
    return 10.0 + fraction * (55.0 - 10.0)


def _factor_margin(
    survival_margin_seconds: float | None,
    eta_current_seconds: float | None,
) -> float:
    if survival_margin_seconds is None:
        # Forecast unknown / rolling -> small contribution, not zero.
        if eta_current_seconds is None:
            return 15.0
        return 15.0
    if survival_margin_seconds < 0:
        # Negative margin: bigger depth -> bigger risk. Cap bonus at 25.
        depth_hours = abs(survival_margin_seconds) / 3600.0
        return 70.0 + min(25.0, depth_hours * 10.0)
    if survival_margin_seconds < 1800:
        return 55.0
    overage_hours = (survival_margin_seconds - 1800.0) / 3600.0
    score = 40.0 - overage_hours * 10.0
    return max(5.0, score)


def _factor_accel(accel: t.Acceleration | None) -> float:
    if accel is None:
        return 0.0
    band = accel.band
    if band == "anomaly":
        return 80.0
    if band == "accelerating":
        return 45.0
    if band in {"stable", "decelerating"}:
        return 10.0
    return 0.0


def _factor_pace(pacing: t.Pacing | None) -> float:
    if pacing is None:
        return 0.0
    band = pacing.pace_band
    if band == "unsustainable":
        return 85.0
    if band == "critical":
        return 65.0
    if band == "elevated":
        return 40.0
    if band == "normal":
        return 15.0
    if band == "comfortable":
        return 5.0
    return 0.0


def _factor_projected(pacing: t.Pacing | None) -> float:
    if pacing is None:
        return 0.0
    projected = pacing.projected_whole_window
    if projected is None:
        return 0.0
    if projected >= 120.0:
        return 75.0
    if projected >= 90.0:
        return 50.0
    if projected >= 70.0:
        return 30.0
    return 10.0


def _min_polls_required() -> int:
    # Spec doesn't fix the floor; we keep this conservative so a noisy
    # single error never inflates the provider score.
    return 5


def assess_provider(
    was: list[t.WindowAnalytics],
    recent_errors: int,
    recent_polls: int,
    cfg: Any,
) -> t.RiskAssessment:
    """Aggregate per-provider score, level, bottleneck.

    Empty input -> healthy sentinel (level HEALTHY, bottleneck 'none').
    """
    if not was:
        return t.RiskAssessment(
            score=0,
            level=t.LEVEL_HEALTHY,
            bottleneck="none",
            factors={},
            window_scores={},
            error_penalty=0,
        )

    window_scores: dict[str, int] = {}
    window_factors: dict[str, dict[str, float]] = {}
    best_score = 0
    best_window_type = "none"

    for wa in was:
        score, factors = score_window(wa, cfg)
        window_scores[wa.window_type] = score
        window_factors[wa.window_type] = factors
        if score > best_score or (
            score == best_score
            and _bottleneck_rank(wa.window_type) < _bottleneck_rank(best_window_type)
        ):
            best_score = score
            best_window_type = wa.window_type

    error_rate = recent_errors / max(1, recent_polls)
    error_penalty = 0
    if recent_polls >= _min_polls_required():
        error_penalty = int(round(min(30.0, error_rate * 100.0)))

    if error_penalty > best_score:
        # Provider is failing more than the windows suggest: name it.
        provider_score = min(100, max(best_score, error_penalty))
        bottleneck = "errors"
    else:
        provider_score = min(100, best_score + error_penalty)
        bottleneck = best_window_type

    return t.RiskAssessment(
        score=provider_score,
        level=_band_to_level(provider_score),
        bottleneck=bottleneck,
        factors=_top_factors(window_factors),
        window_scores=window_scores,
        error_penalty=error_penalty,
    )


def _top_factors(per_window: dict[str, dict[str, float]]) -> dict[str, float]:
    """Combine per-window factors by taking the worst per name.

    This way the Recommendation record can show the worst
    `f_remaining`, `f_margin`, etc. across all windows without
    hiding dominance.
    """
    aggregate: dict[str, float] = {}
    for _, factors in per_window.items():
        for name, value in factors.items():
            current = aggregate.get(name, -1.0)
            if value > current:
                aggregate[name] = value
    return aggregate
