"""Central data contracts for the R1 analytics layer.

This module is the single source of truth for structures exchanged between
analytics modules (M2: series/burn_rate/forecast/pacing/runway/confidence,
M3: risk/recommendation/events/plans), the engine (M4) and the API.

Rules:
- Every structure is JSON-friendly (`to_dict()` via `asdict`).
- Missing knowledge is `None`, never zero (spec §29).
- Numeric burn/ETA values carry explicit units where relevant.
- All timestamps are ISO 8601 UTC strings; durations are seconds.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# window_type canonical values (spec §2)
WINDOW_TYPES = ("five_hour", "daily", "weekly", "monthly", "balance", "credits", "unknown")

# risk levels (spec §14)
LEVEL_HEALTHY = "HEALTHY"
LEVEL_WATCH = "WATCH"
LEVEL_WARNING = "WARNING"
LEVEL_HIGH = "HIGH"
LEVEL_CRITICAL = "CRITICAL"
RISK_LEVELS = (LEVEL_HEALTHY, LEVEL_WATCH, LEVEL_WARNING, LEVEL_HIGH, LEVEL_CRITICAL)

# confidence bands (spec §27)
CONFIDENCE_LOW = "LOW"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_HIGH = "HIGH"

# computation status instead of fabricated zeros (spec §5, §29)
STATUS_OK = "ok"
STATUS_INSUFFICIENT_DATA = "insufficient_data"

# recommendation actions (spec §25)
ACTION_NO_ACTION = "NO_ACTION"
ACTION_WATCH = "WATCH"
ACTION_REDUCE_LOAD = "REDUCE_LOAD"
ACTION_SHIFT_TRAFFIC = "SHIFT_TRAFFIC"
ACTION_INCREASE_BUDGET = "INCREASE_BUDGET"
ACTION_UPGRADE_PLAN = "UPGRADE_PLAN"

# event types (spec §23)
EVENT_QUOTA_RESET = "quota_reset"
EVENT_HIGH_BURN = "high_burn"
EVENT_QUOTA_WARNING = "quota_warning"
EVENT_QUOTA_CRITICAL = "quota_critical"
EVENT_PREDICTED_EXHAUSTION = "predicted_exhaustion"
EVENT_BALANCE_LOW = "balance_low"
EVENT_PROVIDER_ERROR = "provider_error"
EVENT_PROVIDER_RECOVERED = "provider_recovered"
EVENT_TARIFF_INSUFFICIENT = "tariff_insufficient"


@dataclass(slots=True)
class QuotaPoint:
    """One normalized observation of a quota window (from quota_snapshots)."""

    collected_at: str            # ISO 8601 UTC
    used: float | None = None
    remaining: float | None = None
    limit_value: float | None = None
    used_percent: float | None = None
    unit: str | None = None
    reset_at: str | None = None  # ISO 8601 UTC or None
    reset_estimated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Segment:
    """A contiguous run of points sharing one quota window identity."""

    window_type: str
    account: str
    unit: str | None
    points: list[QuotaPoint] = field(default_factory=list)
    reset_at: str | None = None          # reset_at shared by the segment, if known
    has_reset_boundary: bool = False     # segment started by a detected quota_reset
    excluded_points: int = 0             # suspicious points dropped inside segment

    @property
    def start_at(self) -> str | None:
        return self.points[0].collected_at if self.points else None

    @property
    def end_at(self) -> str | None:
        return self.points[-1].collected_at if self.points else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_type": self.window_type,
            "account": self.account,
            "unit": self.unit,
            "points": [p.to_dict() for p in self.points],
            "reset_at": self.reset_at,
            "has_reset_boundary": self.has_reset_boundary,
            "excluded_points": self.excluded_points,
        }


@dataclass(slots=True)
class BurnStat:
    """Burn rate over one lookback window (spec §4-§6)."""

    lookback: str                    # "10m" | "15m" | "1h" | "3h" | "24h" | "3d" | "7d" | "window"
    value: float | None              # units per hour (signed); None when insufficient
    unit: str                        # e.g. "credits/hour", "USD/hour", "percentage_points_per_hour"
    points_used: int = 0
    span_minutes: float | None = None
    status: str = STATUS_INSUFFICIENT_DATA

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Acceleration:
    """burn_15m / burn_1h with anomaly banding (spec §6)."""

    ratio: float | None
    band: str | None                 # decelerating|stable|accelerating|anomaly
    baseline_ok: bool = False        # False when burn_1h below ACCEL_BASELINE_MIN

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Forecast:
    """ETA variants vs reset and survival margin (spec §7-§9)."""

    eta_current_seconds: float | None = None    # pace of burn_15m
    eta_stable_seconds: float | None = None     # pace of burn_1h
    eta_conservative_seconds: float | None = None  # pace of burn_3h / longest available
    eta_short_seconds: float | None = None      # pace of burn_10m (last ~10 min)
    eta_basis_unit: str | None = None           # unit the ETA is expressed in
    reset_in_seconds: float | None = None
    survival_margin_seconds: float | None = None  # eta_current - reset_in; <0 exhausts before reset
    survival_margin_short_seconds: float | None = None  # eta_short - reset_in; <0 bursts break the window
    recovery_mode: str = "unknown"               # hard_reset|estimated_reset|rolling|unknown
    confidence: str = CONFIDENCE_LOW
    confidence_short: str = CONFIDENCE_LOW       # confidence of the burn_10m regression itself

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Pacing:
    """Weekly pacing and end-of-week forecast (spec §10-§11)."""

    elapsed_percent: float | None = None          # fraction of the week gone
    expected_usage_by_now: float | None = None
    used_percent: float | None = None
    pace_ratio: float | None = None
    pace_band: str | None = None                  # comfortable|normal|elevated|critical|unsustainable
    projected_whole_window: float | None = None   # headline projection, % of weekly quota
    projected_pace_24h: float | None = None
    projected_pace_3d: float | None = None
    confidence: str = CONFIDENCE_LOW

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Runway:
    """Monetary balance runway (spec §16-§17)."""

    currency: str | None = None
    balance_total: float | None = None
    usd_per_hour: float | None = None
    usd_per_day: float | None = None
    usd_per_week: float | None = None
    runway_days: float | None = None
    projected_monthly_spend: float | None = None
    status: str = STATUS_INSUFFICIENT_DATA
    confidence: str = CONFIDENCE_LOW

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class WindowAnalytics:
    """Full analytics block for one (provider, account, window_type)."""

    provider: str
    account: str
    window_type: str
    window_label: str | None = None
    latest_used_percent: float | None = None
    latest_used: float | None = None
    latest_remaining: float | None = None
    latest_limit: float | None = None
    unit: str | None = None
    reset_at: str | None = None
    reset_estimated: bool = False
    burns: dict[str, BurnStat] = field(default_factory=dict)   # keyed by lookback
    burn_acceleration: Acceleration | None = None
    forecast: Forecast = field(default_factory=Forecast)
    pacing: Pacing | None = None        # weekly windows only
    runway: Runway | None = None        # balance/credits windows only
    risk_score: int | None = None
    risk_level: str | None = None
    alert_level: str | None = None      # HEALTHY/WARNING/HIGH/CRITICAL from thresholds (spec §24)
    status: str = STATUS_OK
    history_span_minutes: float | None = None
    points_available: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "account": self.account,
            "window_type": self.window_type,
            "window_label": self.window_label,
            "latest": {
                "used_percent": self.latest_used_percent,
                "used": self.latest_used,
                "remaining": self.latest_remaining,
                "limit": self.latest_limit,
                "unit": self.unit,
                "reset_at": self.reset_at,
                "reset_estimated": self.reset_estimated,
            },
            "burns": {k: v.to_dict() for k, v in sorted(self.burns.items())},
            "burn_acceleration": self.burn_acceleration.to_dict() if self.burn_acceleration else None,
            "forecast": self.forecast.to_dict(),
            "pacing": self.pacing.to_dict() if self.pacing else None,
            "runway": self.runway.to_dict() if self.runway else None,
            "risk_score": self.risk_score,
            "risk_level": self.risk_level,
            "alert_level": self.alert_level,
            "status": self.status,
            "history_span_minutes": self.history_span_minutes,
            "points_available": self.points_available,
        }


@dataclass(slots=True)
class RiskAssessment:
    """Risk scoring result for one provider (spec §14-§15)."""

    score: int                          # 0..100
    level: str                          # RISK_LEVELS
    bottleneck: str                     # five_hour|weekly|monthly|balance|performance|errors|none
    factors: dict[str, float] = field(default_factory=dict)   # named factor contributions
    window_scores: dict[str, int] = field(default_factory=dict)  # window_type -> score
    error_penalty: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Recommendation:
    """Actionable recommendation with mandatory reasoning (spec §12-§13, §25-§26)."""

    provider: str
    action: str                         # ACTION_* constant
    title: str
    reason_lines: list[str] = field(default_factory=list)      # must contain concrete numbers
    required_capacity_ratio: float | None = None
    recommended_capacity_ratio: float | None = None
    plan_headroom: float | None = None
    capacity_source: str | None = None  # configured|provider|none
    next_plan: str | None = None        # only when plan capacities are known
    shift_targets: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EventDraft:
    """Event candidate before persistence (dedup/cooldown applied in events.py/store)."""

    provider: str
    account: str
    window_type: str | None
    event_type: str                     # EVENT_* constant
    severity: str                       # info|warning|high|critical
    created_at: str                     # ISO 8601 UTC
    dedup_key: str                      # unique per transition bucket
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
