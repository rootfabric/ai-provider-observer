"""Recommendation engine (spec §12-§13, §25-§26).

Pure function. Consumes `RiskAssessment`, the provider's
WindowAnalytics list and the PlansConfig from plans.py. No file I/O,
no network — the caller's `now` is just there for symmetry with future
implementations; the engine itself does not need it today.
"""

from __future__ import annotations

from typing import Any

from . import plans as plans_mod
from . import types as t


# Window types that get aggregated for capacity planning
# (weekly/monthly are the only ones that have full-week projections).
_WEEKLY_LIKE = {"weekly", "monthly"}


def _projected_from_windows(was: list[t.WindowAnalytics]) -> tuple[float | None, str | None]:
    """Return (max_projected, window_type that produced it)."""
    best: float | None = None
    best_window: str | None = None
    for wa in was:
        if wa.window_type not in _WEEKLY_LIKE:
            continue
        if wa.pacing is None or wa.pacing.projected_whole_window is None:
            continue
        projected = wa.pacing.projected_whole_window
        if best is None or projected > best:
            best = projected
            best_window = wa.window_type
    return best, best_window


def _required_capacity_ratio(max_projected: float | None) -> float | None:
    if max_projected is None:
        return None
    return max(1.0, max_projected / 100.0)


def _recommend_from_capacity(required: float | None, headroom: float) -> float | None:
    if required is None:
        return None
    return required * headroom


def recommend_for_provider(
    provider: str,
    was: list[t.WindowAnalytics],
    risk: t.RiskAssessment,
    plans_info: plans_mod.PlansConfig,
    peer_providers: list[str],
    cfg: Any,
) -> t.Recommendation:
    """Decide one of NO_ACTION/WATCH/SHIFT_TRAFFIC/UPGRADE_PLAN/INCREASE_BUDGET."""
    headroom = getattr(cfg, "recommended_headroom_factor", 1.25)

    max_projected, projected_window = _projected_from_windows(was)
    required_ratio = _required_capacity_ratio(max_projected)
    recommended_ratio = _recommend_from_capacity(required_ratio, headroom)

    next_plan = plans_mod.next_sufficient_plan(plans_info, provider, required_ratio or 1.0)

    # Walk through the decision ladder so each branch can return its
    # own reason lines without duplicating the `if/elifs` of the next.
    action, title, reasons, shift_targets = _decide(
        provider=provider,
        was=was,
        risk=risk,
        plans_info=plans_info,
        peer_providers=peer_providers,
        max_projected=max_projected,
        required_ratio=required_ratio,
        recommended_ratio=recommended_ratio,
        headroom=headroom,
        cfg=cfg,
    )

    # Always include a capacity line so callers can show concrete numbers
    # in the UI, even for NO_ACTION.
    if required_ratio is not None:
        reasons = _ensure_capacity_reason(
            reasons,
            max_projected=max_projected,
            required_ratio=required_ratio,
            recommended_ratio=recommended_ratio,
            headroom=headroom,
        )

    plan_headroom = recommended_ratio

    return t.Recommendation(
        provider=provider,
        action=action,
        title=title,
        reason_lines=reasons,
        required_capacity_ratio=required_ratio,
        recommended_capacity_ratio=recommended_ratio,
        plan_headroom=plan_headroom,
        capacity_source=plans_info.source if plans_info.source in {"configured", "none"} else plans_info.source,
        next_plan=next_plan,
        shift_targets=shift_targets,
    )


def _ensure_capacity_reason(
    reasons: list[str],
    max_projected: float | None,
    required_ratio: float,
    recommended_ratio: float | None,
    headroom: float,
) -> list[str]:
    if max_projected is None:
        return reasons
    if not any("projected" in line.lower() for line in reasons):
        reasons = list(reasons) + [
            f"Weekly usage is projected at {max_projected:.0f}% of current quota"
        ]
    if recommended_ratio is not None and not any(
        "headroom" in line.lower() for line in reasons
    ):
        reasons = list(reasons) + [
            f"Recommended headroom: {recommended_ratio:.2f}x current plan (1.00x baseline × {headroom:.2f} factor)"
        ]
    return reasons


def _decide(
    *,
    provider: str,
    was: list[t.WindowAnalytics],
    risk: t.RiskAssessment,
    plans_info: plans_mod.PlansConfig,
    peer_providers: list[str],
    max_projected: float | None,
    required_ratio: float | None,
    recommended_ratio: float | None,
    headroom: float,
    cfg: Any,
) -> tuple[str, str, list[str], list[str]]:
    """Internal ladder returning (action, title, reason_lines, shift_targets)."""
    balance_low_days = getattr(cfg, "balance_low_days", 7.0)

    if risk.bottleneck == "errors":
        err_rate = (
            risk.error_penalty
            / 100.0
        )
        return (
            t.ACTION_WATCH,
            "Provider errors observed",
            [
                f"Recent error rate: {err_rate:.0%} over last polls",
                "Investigate connectivity or upstream provider status before depending on this provider.",
            ],
            [],
        )

    # CRITICAL/HIGH from negative five-hour margin + peers -> SHIFT_TRAFFIC.
    five_hour_wa = next((w for w in was if w.window_type == "five_hour"), None)
    if (
        five_hour_wa is not None
        and five_hour_wa.forecast.survival_margin_seconds is not None
        and five_hour_wa.forecast.survival_margin_seconds < 0
        and risk.level in {t.LEVEL_HIGH, t.LEVEL_CRITICAL}
        and peer_providers
    ):
        targets = list(peer_providers)
        return (
            t.ACTION_SHIFT_TRAFFIC,
            "Shift traffic to peer providers",
            [
                f"five_hour window projected to exhaust before reset (margin {five_hour_wa.forecast.survival_margin_seconds:.0f}s)",
                f"Available peers: {', '.join(targets)}",
            ],
            targets,
        )

    projected_value = max_projected
    pace_band = None
    for w in was:
        if w.pacing is not None:
            pace_band = w.pacing.pace_band
            break

    unsustainable = pace_band == "unsustainable"

    if projected_value is not None and (projected_value >= 120.0 or unsustainable):
        if plans_mod.next_sufficient_plan(plans_info, provider, required_ratio or 1.0):
            return (
                t.ACTION_UPGRADE_PLAN,
                "Upgrade to a sufficient plan",
                [
                    f"Weekly projected at {projected_value:.0f}% (over quota)",
                    f"Recommended headroom: {recommended_ratio:.2f}x current plan" if recommended_ratio else "Plan search performed",
                ],
                [],
            )
        return (
            t.ACTION_INCREASE_BUDGET,
            "Increase budget or contact provider",
            [
                f"Weekly projected at {projected_value:.0f}% (over quota)",
                "No configured plans with known capacity — request a quota raise.",
            ],
            [],
        )

    if projected_value is not None and projected_value >= 90.0:
        if plans_mod.next_sufficient_plan(plans_info, provider, required_ratio or 1.0):
            return (
                t.ACTION_UPGRADE_PLAN,
                "Plan upgrade recommended",
                [
                    f"Weekly projected at {projected_value:.0f}% (approaching limit)",
                    f"Recommended headroom: {recommended_ratio:.2f}x current plan" if recommended_ratio else "Plan search performed",
                ],
                [],
            )
        return (
            t.ACTION_WATCH,
            "Watch weekly projections",
            [
                f"Weekly projected at {projected_value:.0f}%",
                "No configured plans with known capacity; monitor before next cycle.",
            ],
            [],
        )

    # Runway check (balance window) -> INCREASE_BUDGET.
    balance_low = _runway_below_threshold(was, balance_low_days)
    if balance_low is not None:
        return (
            t.ACTION_INCREASE_BUDGET,
            "Top up balance",
            [
                f"Runway {balance_low:.1f} days (threshold {balance_low_days:.1f})",
            ],
            [],
        )

    if risk.level == t.LEVEL_WATCH:
        return (
            t.ACTION_WATCH,
            "Monitor consumption",
            [
                "Risk is in the WATCH band; review next cycle.",
            ],
            [],
        )

    return (
        t.ACTION_NO_ACTION,
        "No action required",
        [
            "All windows within expected thresholds.",
        ],
        [],
    )


def _runway_below_threshold(
    was: list[t.WindowAnalytics], threshold_days: float
) -> float | None:
    """Return the lowest runway_days across balance/credits windows if it is
    below the configured threshold."""
    lowest: float | None = None
    for w in was:
        if w.runway is None or w.runway.runway_days is None:
            continue
        if w.runway.runway_days >= threshold_days:
            continue
        if lowest is None or w.runway.runway_days < lowest:
            lowest = w.runway.runway_days
    return lowest
