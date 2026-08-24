"""Deterministic demo providers and history seeding (M6 — §32, §36.18).

`demo_snapshots()` is the single entry point the collector keeps
calling between its history seed and the live poll cycle. Its
`scenario` argument selects one of four profiles; `tick` advances the
clock so transition tests can compare snapshots from successive polls.

`seed_demo_history()` populates ``quota_snapshots`` with a deterministic
3-hour history (capped at ~120 points per window) so the analytics
engine has enough points to compute burns, accelerations and ETAs on
the very first refresh.

Both functions are pure: given the same ``(scenario, tick, now)``
triple they produce the same outputs. Idempotent seeding is built into
``seed_demo_history`` so it can be invoked from the collector without
duplicating work on every cycle.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from app.models import ProviderSnapshot, QuotaWindow


DEMO_SCENARIOS: tuple[str, ...] = (
    "normal",
    "high_burn",
    "weekly_exhaustion",
    "critical",
)


# ---------------------------------------------------------------------------
# Configuration helpers (deterministic, no randomness)
# ---------------------------------------------------------------------------


_HISTORY_HOURS = 3.0  # seed window length
_POINT_CAP = 120      # hard cap on points-per-window per profile


@dataclass(frozen=True, slots=True)
class _WindowSpec:
    """One window inside a profile — controls burn shape and reset
    behaviour for the seeded history."""

    name: str
    window_type: str
    used_percent: float
    reset_hours_from_now: float
    unit: str | None = "credits"
    used: float | None = None
    limit: float | None = None
    # Used by the per-tick demo_snapshots() to nudge latest values.
    used_per_tick: float = 0.0


@dataclass(frozen=True, slots=True)
class _BalanceSpec:
    """One balance row inside a profile."""

    currency: str
    total: float
    granted: float | None = None
    topped_up: float | None = None
    purchased: float | None = None
    used_balance: float | None = None


@dataclass(frozen=True, slots=True)
class _Profile:
    """Provider shape for the seeded history + per-tick snapshots."""

    provider: str
    label: str
    plan: str | None
    windows: tuple[_WindowSpec, ...]
    balances: tuple[_BalanceSpec, ...] = ()

    def details(self, scenario: str) -> dict:
        return {"demo": True, "scenario": scenario}


# ---------------------------------------------------------------------------
# Profile registry — one per DEMO_SCENARIO (zai only — secondary
# providers stay constant across scenarios).
# ---------------------------------------------------------------------------


def _primary_profile(scenario: str) -> _Profile:
    if scenario == "high_burn":
        return _Profile(
            provider="zai",
            label="Z.AI",
            plan="lite",
            windows=(
                _WindowSpec("5h", "five_hour", used_percent=78.0, reset_hours_from_now=2.0,
                            used=1950.0, limit=2500.0, used_per_tick=0.0),
                _WindowSpec("week", "weekly", used_percent=42.0, reset_hours_from_now=80.0,
                            used=4200.0, limit=10000.0, used_per_tick=0.0),
            ),
        )
    if scenario == "weekly_exhaustion":
        return _Profile(
            provider="zai",
            label="Z.AI",
            plan="lite",
            windows=(
                _WindowSpec("5h", "five_hour", used_percent=22.0, reset_hours_from_now=4.0,
                            used=440.0, limit=2000.0, used_per_tick=0.0),
                _WindowSpec("week", "weekly", used_percent=58.0, reset_hours_from_now=120.0,
                            used=5800.0, limit=10000.0, used_per_tick=0.0),
            ),
        )
    if scenario == "critical":
        return _Profile(
            provider="zai",
            label="Z.AI",
            plan="lite",
            windows=(
                _WindowSpec("5h", "five_hour", used_percent=96.0, reset_hours_from_now=2.0,
                            used=960.0, limit=1000.0, used_per_tick=0.0),
                _WindowSpec("week", "weekly", used_percent=72.0, reset_hours_from_now=80.0,
                            used=7200.0, limit=10000.0, used_per_tick=0.0),
            ),
        )
    # default / "normal"
    return _Profile(
        provider="zai",
        label="Z.AI",
        plan="lite",
        windows=(
            _WindowSpec("5h", "five_hour", used_percent=40.0, reset_hours_from_now=3.0,
                        used=1200.0, limit=2000.0, used_per_tick=0.0),
            _WindowSpec("week", "weekly", used_percent=18.0, reset_hours_from_now=92.0,
                        used=1800.0, limit=10000.0, used_per_tick=0.0),
        ),
    )


_DEEPSEEK_PROFILE = _Profile(
    provider="deepseek",
    label="DeepSeek",
    plan=None,
    windows=(),
    balances=(
        _BalanceSpec(currency="USD", total=18.42, granted=0.0, topped_up=18.42),
    ),
)


_MINIMAX_PROFILE = _Profile(
    provider="minimax",
    label="MiniMax",
    plan="Plus",
    windows=(
        _WindowSpec("5h", "five_hour", used_percent=27.0, reset_hours_from_now=3.0),
        _WindowSpec("week", "weekly", used_percent=48.0, reset_hours_from_now=110.0),
    ),
)


_OPENROUTER_PROFILE = _Profile(
    provider="openrouter",
    label="OpenRouter",
    plan=None,
    windows=(
        _WindowSpec("weekly", "weekly", used_percent=42.0, reset_hours_from_now=73.0,
                    used=21.0, limit=50.0),
    ),
    balances=(
        _BalanceSpec(currency="USD", total=36.12, purchased=100.0, used_balance=63.88),
    ),
)


_CODEX_PROFILE = _Profile(
    provider="codex",
    label="OpenAI Codex",
    plan="plus",
    windows=(
        _WindowSpec("5h", "five_hour", used_percent=44.0, reset_hours_from_now=2.0),
        _WindowSpec("week", "weekly", used_percent=71.0, reset_hours_from_now=61.0),
    ),
)


def _profile_for(scenario: str) -> tuple[_Profile, ...]:
    """Return the list of profiles that participate in ``scenario``."""
    if scenario not in DEMO_SCENARIOS:
        scenario = "normal"
    return (
        _primary_profile(scenario),
        _DEEPSEEK_PROFILE,
        _MINIMAX_PROFILE,
        _OPENROUTER_PROFILE,
        _CODEX_PROFILE,
    )


# ---------------------------------------------------------------------------
# Per-tick snapshot generation
# ---------------------------------------------------------------------------


def _windows_for(profile: _Profile, tick: int, ref_now: datetime) -> list[QuotaWindow]:
    tick = max(0, int(tick))
    out: list[QuotaWindow] = []
    for w in profile.windows:
        used_percent = min(100.0, w.used_percent + w.used_per_tick * tick)
        used: float | None = None
        limit: float | None = None
        remaining: float | None = None
        if w.used is not None:
            used = w.used + w.used_per_tick * tick
        if w.limit:
            limit = float(w.limit)
            if used is not None and limit:
                remaining = max(0.0, limit - used)
        out.append(
            QuotaWindow(
                name=w.name,
                used_percent=used_percent,
                remaining_percent=max(0.0, 100.0 - used_percent),
                reset_at=(ref_now + timedelta(hours=w.reset_hours_from_now)).isoformat(),
                used=used,
                limit=limit,
                remaining=remaining,
                unit=w.unit,
            )
        )
    return out


def _balances_for(profile: _Profile, tick: int) -> list[dict]:
    tick = max(0, int(tick))
    out: list[dict] = []
    for b in profile.balances:
        entry: dict = {"currency": b.currency}
        if b.total is not None:
            entry["total"] = b.total
        if b.granted is not None:
            entry["granted"] = b.granted
        if b.topped_up is not None:
            entry["topped_up"] = b.topped_up
        if b.purchased is not None:
            entry["purchased"] = b.purchased
        if b.used_balance is not None:
            entry["used"] = b.used_balance
        out.append(entry)
    return out


def demo_snapshots(scenario: str = "normal", tick: int = 0) -> list[ProviderSnapshot]:
    """Return deterministic provider snapshots for the chosen ``scenario``.

    ``tick`` evolves the snapshot between calls (tests use it to
    observe transitions). Latest ``used_percent`` reflects ``base +
    used_per_tick * tick`` (already capped at 100); reset_at is anchored
    against ``now`` so callers see reset windows that look "real".
    """
    profiles = _profile_for(scenario)
    ref_now = datetime.now(timezone.utc)
    snaps: list[ProviderSnapshot] = []
    for profile in profiles:
        ts_iso = ref_now.isoformat()
        snap = ProviderSnapshot(
            provider=profile.provider,
            label=profile.label,
            status="ok",
            checked_at=ts_iso,
            latency_ms=180,
            plan=profile.plan,
            windows=_windows_for(profile, tick, ref_now),
            balances=_balances_for(profile, tick),
            details=profile.details(scenario),
        )
        snaps.append(snap)
    return snaps


# ---------------------------------------------------------------------------
# History seeding
# ---------------------------------------------------------------------------


def _quota_row(
    *,
    provider: str,
    window_type: str,
    window_label: str,
    collected_at: datetime,
    used_percent: float | None,
    used: float | None,
    remaining: float | None,
    limit_value: float | None,
    unit: str | None,
    reset_at: str | None,
) -> dict:
    return {
        "provider": provider,
        "account": "default",
        "window_type": window_type,
        "window_label": window_label,
        "collected_at": collected_at.isoformat(),
        "used": used,
        "remaining": remaining,
        "limit_value": limit_value,
        "used_percent": used_percent,
        "unit": unit,
        "reset_at": reset_at,
        "reset_estimated": 0 if reset_at else 0,
        "raw_json": "{}",
    }


def _tail_slope_per_min(scenario: str) -> float:
    """Per-minute percent-points slope for the LAST 30 minutes of seed."""
    if scenario == "high_burn":
        return 60.0 / 30.0   # 60 p.p. rise over 30 min
    if scenario == "critical":
        return 40.0 / 30.0
    if scenario == "weekly_exhaustion":
        return 4.0 / 60.0
    return 8.0 / 60.0  # normal: 8 p.p./h


def _history_window_rows(
    *,
    provider: str,
    spec: _WindowSpec,
    now: datetime,
    poll_step_seconds: int,
    tail_slope_per_min: float,
    extra_old_reset_in_hours: float | None = None,
) -> list[dict]:
    """Build a single window's quota_snapshots rows for the seed.

    The current segment runs from ``now - HISTORY_HOURS`` to ``now``
    with a fixed ``reset_at`` (so the segment stays intact for
    regression). ``pct(t)`` is monotonically non-decreasing towards
    ``now`` so that the computed burn is positive (consumption rises
    with time).
    """
    step = max(60, int(poll_step_seconds))
    span_minutes = int(_HISTORY_HOURS * 60)
    n_points = min(_POINT_CAP, max(6, span_minutes // max(1, step // 60)))
    base_pct = max(0.0, min(100.0, spec.used_percent or 0.0))
    limit = float(spec.limit) if spec.limit else 0.0
    inserted_reset_at = now + timedelta(hours=spec.reset_hours_from_now)
    reset_iso = inserted_reset_at.isoformat()

    # Tail region: last 30 minutes, slope dictated by scenario.
    tail_minutes = 30.0
    # Older region: gentle climb from a low pct_start to pct_at_tail.
    pct_at_tail = max(0.0, base_pct - max(0.0, tail_slope_per_min * tail_minutes))
    pct_start = max(0.0, pct_at_tail - max(0.5, pct_at_tail * 0.25))

    rows: list[dict] = []
    for i in range(n_points):
        # minutes_back: 0 for the most recent sample, ~span for the oldest.
        minutes_back = float(n_points - 1 - i) * (step / 60.0)
        ts = now - timedelta(minutes=minutes_back)
        if minutes_back <= tail_minutes:
            # Freshest half-hour: rises to base_pct at t=0.
            fraction = (tail_minutes - minutes_back) / tail_minutes
            pct = max(0.0, pct_at_tail + (base_pct - pct_at_tail) * fraction)
        else:
            # Older region: linear rise from pct_start (at t=span) to pct_at_tail.
            beyond = minutes_back - tail_minutes
            old_span = max(1.0, span_minutes - tail_minutes)
            fraction = (old_span - beyond) / old_span
            pct = max(0.0, pct_start + (pct_at_tail - pct_start) * fraction)
        pct = min(100.0, pct)
        used: float | None = None
        remaining: float | None = None
        if limit > 0:
            used = round(limit * pct / 100.0, 2)
            remaining = max(0.0, limit - used)
        rows.append(
            _quota_row(
                provider=provider,
                window_type=spec.window_type,
                window_label=spec.name,
                collected_at=ts,
                used_percent=pct,
                used=used,
                remaining=remaining,
                limit_value=limit or None,
                unit=spec.unit or "credits",
                reset_at=reset_iso,
            )
        )

    # Older head with a different reset_at — a previous segment whose
    # boundary the segmenter will detect as quota_reset.
    older_ts = now - timedelta(hours=_HISTORY_HOURS)
    rows.insert(
        0,
        _quota_row(
            provider=provider,
            window_type=spec.window_type,
            window_label=spec.name,
            collected_at=older_ts,
            used_percent=max(0.0, pct_start - 5.0),
            used=None,
            remaining=None,
            limit_value=None,
            unit=spec.unit or "credits",
            reset_at=(inserted_reset_at - timedelta(days=7)).isoformat(),
        ),
    )

    # Optional mid-segment older reset to make the high_burn scenario
    # produce an explicit quota_reset event draft after refresh_all.
    if extra_old_reset_in_hours is not None:
        rows.append(
            _quota_row(
                provider=provider,
                window_type=spec.window_type,
                window_label=spec.name,
                collected_at=now - timedelta(hours=extra_old_reset_in_hours),
                used_percent=min(100.0, pct_at_tail),
                used=None,
                remaining=None,
                limit_value=None,
                unit=spec.unit or "credits",
                reset_at=(inserted_reset_at + timedelta(hours=4)).isoformat(),
            )
        )
    return rows


def _deepseek_history_rows(now: datetime) -> list[dict]:
    """Linear hourly decline for the deepseek balance (last 24 hours)."""
    rows: list[dict] = []
    bal = _DEEPSEEK_PROFILE.balances[0]
    latest_total = bal.total or 0.0
    for i in range(24):
        ts = now - timedelta(hours=23 - i)
        frac = i / 23.0
        # Linear walk from 0.97x → 1.00x latest_total.
        bal_now = round(latest_total * (0.97 + 0.03 * frac), 4)
        rows.append(
            _quota_row(
                provider="deepseek",
                window_type="balance",
                window_label=bal.currency,
                collected_at=ts,
                used_percent=None,
                used=None,
                remaining=bal_now,
                limit_value=bal.topped_up,
                unit=bal.currency,
                reset_at=None,
            )
        )
    return rows


def seed_demo_history(store, settings, now: datetime | None = None, force: bool = False) -> int:
    """Seed deterministic history into ``quota_snapshots``.

    Idempotency: when ``store.latest_quota('zai','default','five_hour')``
    already exists and ``force`` is ``False`` the function returns 0
    and touches nothing.
    """
    scenario = getattr(settings, "demo_scenario", "normal")
    if scenario not in DEMO_SCENARIOS:
        scenario = "normal"

    if now is None:
        now = datetime.now(timezone.utc)

    if not force and store.latest_quota("zai", "default", "five_hour") is not None:
        return 0

    poll_step = max(30, int(getattr(settings, "poll_interval_seconds", 60) or 60))
    tail_slope = _tail_slope_per_min(scenario)
    primary = _primary_profile(scenario)

    rows: list[dict] = []
    for spec in primary.windows:
        extra_old_reset = 1.5 if scenario == "high_burn" else None
        rows.extend(
            _history_window_rows(
                provider=primary.provider,
                spec=spec,
                now=now,
                poll_step_seconds=poll_step,
                tail_slope_per_min=tail_slope,
                extra_old_reset_in_hours=extra_old_reset,
            )
        )

    # Always include a deterministic deepseek balance history.
    rows.extend(_deepseek_history_rows(now))

    if not rows:
        return 0
    return store.save_quota_snapshots(rows, retention_days=getattr(settings, "quota_retention_days", 0))


__all__ = [
    "DEMO_SCENARIOS",
    "demo_snapshots",
    "seed_demo_history",
]
