"""Forecast / ETA / survival margin computation (spec §4.4, §7-§9).

The forecast combines a ``latest`` :class:`QuotaPoint` with the
pre-computed burn rates to estimate when the quota will exhaust. Three
ETA horizons are exposed:

* ``eta_current_seconds``  – uses ``burn_15m`` (current pace).
* ``eta_stable_seconds``   – uses ``burn_1h`` (stable pace).
* ``eta_conservative_seconds`` – uses ``burn_3h`` (long-term pace).

ETA is only set when the corresponding burn is ``ok`` and strictly
positive (above an epsilon); otherwise the ETA is ``None`` and never
zero (spec §29). The survival margin ``eta_current − reset_in`` is
computed only when both sides are known; it is ``None`` when the
provider never reports a reset (rolling/estimated recovery modes).
"""
from __future__ import annotations

from datetime import datetime

from app.analytics import types as t
from app.analytics.confidence import confidence_from_span
from app.analytics.series import parse_iso_utc


_EPS = 1e-9


def _resolve_remaining(latest: t.QuotaPoint, burn_unit: str | None) -> float | None:
    """Pick the remaining quota in the same unit as the burn rate."""
    absolute = bool(burn_unit) and burn_unit != "percentage_points_per_hour"
    if absolute and latest.remaining is not None:
        try:
            return float(latest.remaining)
        except (TypeError, ValueError):
            return None
    if latest.used_percent is not None:
        try:
            return max(0.0, 100.0 - float(latest.used_percent))
        except (TypeError, ValueError):
            return None
    if latest.remaining is not None and latest.limit_value not in (None, 0):
        try:
            return max(0.0, float(latest.remaining))
        except (TypeError, ValueError):
            return None
    return None


def _eta_seconds(remaining: float | None, burn: t.BurnStat | None) -> float | None:
    if burn is None:
        return None
    if burn.status != t.STATUS_OK:
        return None
    if burn.value is None or burn.value <= _EPS:
        return None
    if remaining is None:
        return None
    return remaining / burn.value * 3600.0


def _recovery_mode(latest: t.QuotaPoint) -> str:
    if latest.reset_at:
        return "estimated_reset" if latest.reset_estimated else "hard_reset"
    return "unknown"


def build_forecast(
    latest: t.QuotaPoint,
    burns: dict[str, t.BurnStat],
    now: datetime,
    cfg,
    segment_span_minutes: float | None = None,
) -> t.Forecast:
    """Return a :class:`Forecast` for one (provider, account, window).

    Parameters
    ----------
    latest:
        Most recent :class:`QuotaPoint` for the window. ``remaining``
        is preferred when burns are absolute; otherwise ``used_percent``
        is used to derive the remaining fraction.
    burns:
        Mapping of lookback label (``15m``/``1h``/``3h``) → :class:`BurnStat`.
    now:
        Reference time (UTC, tz-aware).
    cfg:
        Analytics configuration object.
    segment_span_minutes:
        Optional span of the current segment used for confidence
        resolution; ``None`` ⇒ ``LOW`` confidence.
    """
    # Determine whether the burns work in absolute units.
    sample_burn = None
    for key in ("15m", "1h", "3h"):
        if key in burns:
            sample_burn = burns[key]
            break
    burn_unit = sample_burn.unit if sample_burn is not None else None
    remaining = _resolve_remaining(latest, burn_unit)

    eta_current = _eta_seconds(remaining, burns.get("15m"))
    eta_stable = _eta_seconds(remaining, burns.get("1h"))
    eta_conservative = _eta_seconds(remaining, burns.get("3h"))

    reset_in: float | None = None
    if latest.reset_at:
        reset_dt = parse_iso_utc(latest.reset_at)
        if reset_dt is not None:
            delta = reset_dt - now
            reset_in = max(0.0, delta.total_seconds())

    survival_margin: float | None = None
    if eta_current is not None and reset_in is not None:
        survival_margin = eta_current - reset_in

    confidence = confidence_from_span(segment_span_minutes)
    mode = _recovery_mode(latest)

    return t.Forecast(
        eta_current_seconds=eta_current,
        eta_stable_seconds=eta_stable,
        eta_conservative_seconds=eta_conservative,
        eta_basis_unit=burn_unit,
        reset_in_seconds=reset_in,
        survival_margin_seconds=survival_margin,
        recovery_mode=mode,
        confidence=confidence,
    )


__all__ = ["build_forecast"]