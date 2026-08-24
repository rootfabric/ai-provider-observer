"""Event generation with transition/cooldown semantics (spec §23).

Two responsibilities:

* `evaluate_events` — given previous and current alert levels per
  (provider, account, window_type), plus current RiskAssessment per
  provider and the snapshot_status per provider, produce
  EventDraft items for each transition that the spec calls out.
* `filter_cooldown` — drop drafts whose (provider, account,
  event_type) has already fired within `event_cooldown_minutes`.

Why we keep metric-derived events (predicted_exhaustion,
tariff_insufficient, balance_low, high_burn) inside this module:
the `risk_by_provider` argument exposes per-window factor scores and
window scores, which we can use as a stable proxy for "was the
metric-source signal fresh this cycle?" without leaking mutable
state between calls.

The function is pure: same inputs produce the same drafts.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from . import types as t


EVENT_TYPES: tuple[str, ...] = (
    t.EVENT_QUOTA_RESET,
    t.EVENT_HIGH_BURN,
    t.EVENT_QUOTA_WARNING,
    t.EVENT_QUOTA_CRITICAL,
    t.EVENT_PREDICTED_EXHAUSTION,
    t.EVENT_BALANCE_LOW,
    t.EVENT_PROVIDER_ERROR,
    t.EVENT_PROVIDER_RECOVERED,
    t.EVENT_TARIFF_INSUFFICIENT,
)


_LEVEL_RANK = {
    t.LEVEL_HEALTHY: 0,
    t.LEVEL_WATCH: 1,
    t.LEVEL_WARNING: 2,
    t.LEVEL_HIGH: 3,
    t.LEVEL_CRITICAL: 4,
}


def _alert_rank(level: str | None) -> int:
    if level is None:
        return -1
    return _LEVEL_RANK.get(level, -1)


def _format_iso(dt: datetime) -> str:
    """Stable ISO 8601 with explicit UTC offset and no microseconds."""
    if dt.tzinfo is None:
        return dt.replace(microsecond=0).isoformat() + "Z"
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _split_key(key: str) -> tuple[str, str, str | None]:
    """Split the window alert key `provider:account:window_type`."""
    parts = key.split(":")
    if len(parts) >= 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], parts[1], None
    return parts[0], "default", None


def _bucket_for_key(key: str) -> str:
    if ":tariff" in key or key.endswith(":provider"):
        return "provider"
    _, _, window_type = _split_key(key)
    return window_type or "provider"


def _parse_iso(value: str) -> datetime:
    """ISO parse without dateutil. Accepts trailing Z and naive timestamps.

    Naive timestamps are interpreted as UTC, matching the rest of the
    codebase that stores UTC inside the DB.
    """
    if not isinstance(value, str):
        raise TypeError("created_at must be a string")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        if "." in text:
            head, _, _ = text.partition(".")
            # Drop fractional seconds if present.
            tail_remainder = text[len(head) + 1 :]
            for sep in ("+", "-"):
                if sep in tail_remainder:
                    idx = tail_remainder.index(sep)
                    tail_remainder = tail_remainder[idx:]
                    break
                tail_remainder = ""
            parsed = datetime.fromisoformat(head + tail_remainder)
        else:
            raise
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# ---------------------------------------------------------------------------
# evaluate_events
# ---------------------------------------------------------------------------
def evaluate_events(
    prev_alert: dict[str, str],
    curr: dict[str, str],
    risk_by_provider: dict[str, t.RiskAssessment],
    snapshot_status: dict[str, str],
    now: datetime,
    cfg: Any,
) -> list[t.EventDraft]:
    """Produce EventDrafts for transitions detected this cycle.

    `prev_alert` / `curr` keys are `f"{provider}:{account}:{window_type}"`,
    values are alert level strings. `snapshot_status` keys are provider
    names, values are `'ok' | 'error' | <other>`. The drafts list is
    deduplicated by dedup_key inside one call.
    """
    drafts: list[t.EventDraft] = []
    balance_low_days = float(getattr(cfg, "balance_low_days", 7.0))
    alert_critical_projected = float(
        getattr(cfg, "alert_critical_projected_week", 120.0)
    )
    created_at = _format_iso(now)

    seen_keys: set[str] = set()

    def _add(draft: t.EventDraft) -> None:
        if draft.dedup_key in seen_keys:
            return
        seen_keys.add(draft.dedup_key)
        drafts.append(draft)

    # 1. Quota warning / critical transitions per (provider, account, window).
    for key, curr_level in curr.items():
        prev_level = prev_alert.get(key)
        prev_rank = _alert_rank(prev_level)
        curr_rank = _alert_rank(curr_level)
        bucket = _bucket_for_key(key)

        provider, account, window_type = _split_key(key)

        # quota_warning: first time we hit WARNING from any lower state.
        if (
            curr_level == t.LEVEL_WARNING
            and prev_rank < _alert_rank(t.LEVEL_WARNING)
        ):
            _add(
                t.EventDraft(
                    provider=provider,
                    account=account,
                    window_type=window_type,
                    event_type=t.EVENT_QUOTA_WARNING,
                    severity="warning",
                    created_at=created_at,
                    dedup_key=f"{key}:{t.EVENT_QUOTA_WARNING}:{bucket}",
                    payload={"prev": prev_level, "curr": curr_level},
                )
            )

        # quota_critical: from WARNING OR from HEALTHY/WATCH directly.
        if (
            curr_level == t.LEVEL_CRITICAL
            and prev_rank < _alert_rank(t.LEVEL_CRITICAL)
        ):
            _add(
                t.EventDraft(
                    provider=provider,
                    account=account,
                    window_type=window_type,
                    event_type=t.EVENT_QUOTA_CRITICAL,
                    severity="critical",
                    created_at=created_at,
                    dedup_key=f"{key}:{t.EVENT_QUOTA_CRITICAL}:{bucket}",
                    payload={"prev": prev_level, "curr": curr_level},
                )
            )

        # predicted_exhaustion: entering HIGH with f_margin >= 70 (deeply
        # negative margin proxy from RiskAssessment).
        if (
            curr_level == t.LEVEL_HIGH
            and prev_rank < _alert_rank(t.LEVEL_HIGH)
            and _window_has_negative_margin(key, risk_by_provider)
        ):
            _add(
                t.EventDraft(
                    provider=provider,
                    account=account,
                    window_type=window_type,
                    event_type=t.EVENT_PREDICTED_EXHAUSTION,
                    severity="high",
                    created_at=created_at,
                    dedup_key=f"{key}:{t.EVENT_PREDICTED_EXHAUSTION}:{bucket}",
                    payload={"prev": prev_level, "curr": curr_level},
                )
            )

        # high_burn: anomaly transition (new this cycle).
        if (
            curr_level == t.LEVEL_HIGH
            and _window_is_anomaly(key, risk_by_provider)
            and not _prev_window_was_anomaly(key, prev_alert, risk_by_provider)
        ):
            _add(
                t.EventDraft(
                    provider=provider,
                    account=account,
                    window_type=window_type,
                    event_type=t.EVENT_HIGH_BURN,
                    severity="warning",
                    created_at=created_at,
                    dedup_key=f"{key}:{t.EVENT_HIGH_BURN}:{bucket}",
                    payload={"band": "anomaly"},
                )
            )

    # 2. Tariff insufficient (provider-level, projected >= critical-week).
    for provider, risk in risk_by_provider.items():
        projected = _extract_max_projected(risk)
        if projected is not None and projected >= alert_critical_projected:
            key = f"{provider}:tariff:projected"
            _add(
                t.EventDraft(
                    provider=provider,
                    account="default",
                    window_type=None,
                    event_type=t.EVENT_TARIFF_INSUFFICIENT,
                    severity="high",
                    created_at=created_at,
                    dedup_key=f"{key}:{t.EVENT_TARIFF_INSUFFICIENT}:provider",
                    payload={"projected": projected},
                )
            )

    # 3. Provider-level snapshot status (error/ok transitions).
    for provider, status in snapshot_status.items():
        prev_st = prev_alert.get(f"snap:{provider}", "ok")
        if status == "error" and prev_st != "error":
            _add(
                t.EventDraft(
                    provider=provider,
                    account="default",
                    window_type=None,
                    event_type=t.EVENT_PROVIDER_ERROR,
                    severity="warning",
                    created_at=created_at,
                    dedup_key=f"{provider}:{t.EVENT_PROVIDER_ERROR}:provider",
                    payload={"prev": prev_st, "curr": "error"},
                )
            )
        elif prev_st == "error" and status == "ok":
            _add(
                t.EventDraft(
                    provider=provider,
                    account="default",
                    window_type=None,
                    event_type=t.EVENT_PROVIDER_RECOVERED,
                    severity="info",
                    created_at=created_at,
                    dedup_key=f"{provider}:{t.EVENT_PROVIDER_RECOVERED}:provider",
                    payload={"prev": "error", "curr": "ok"},
                )
            )

    # 4. Balance low transition.
    for provider, risk in risk_by_provider.items():
        below_now = _provider_below_balance(risk, balance_low_days)
        below_prev = _provider_below_balance_prev(provider, prev_alert, risk, balance_low_days)
        if below_now and not below_prev:
            _add(
                t.EventDraft(
                    provider=provider,
                    account="default",
                    window_type=None,
                    event_type=t.EVENT_BALANCE_LOW,
                    severity="warning",
                    created_at=created_at,
                    dedup_key=f"{provider}:{t.EVENT_BALANCE_LOW}:provider",
                    payload={"threshold_days": balance_low_days},
                )
            )

    return drafts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _window_has_negative_margin(
    key: str, risk_by_provider: dict[str, t.RiskAssessment]
) -> bool:
    """True if the window corresponding to `key` has a deeply negative
    margin according to the RiskAssessment factor table."""
    provider, _, _ = _split_key(key)
    risk = risk_by_provider.get(provider)
    if risk is None:
        return False
    return risk.factors.get("f_margin", 0.0) >= 70.0


def _window_is_anomaly(
    key: str, risk_by_provider: dict[str, t.RiskAssessment]
) -> bool:
    provider, _, _ = _split_key(key)
    risk = risk_by_provider.get(provider)
    if risk is None:
        return False
    return risk.factors.get("f_accel", 0.0) >= 80.0


def _prev_window_was_anomaly(
    key: str,
    prev_alert: dict[str, str],
    risk_by_provider: dict[str, t.RiskAssessment],
) -> bool:
    """For now treat prev-window-anomaly as: prev_level was HIGH/CRITICAL AND
    the previous risk signal already carried the anomaly factor.
    Because we don't store per-window risk snapshots across calls,
    we rely on prev_alert level staying high as a proxy for "already
    in anomaly band"."""
    prev_level = prev_alert.get(key)
    return prev_level in {t.LEVEL_HIGH, t.LEVEL_CRITICAL}


def _extract_max_projected(risk: t.RiskAssessment) -> float | None:
    """Pull the max projected % for the provider from the risk factor table.

    The factor value alone doesn't store the actual projected number; we
    reverse-engineer it through the band table used by `_factor_projected`.
    """
    value = risk.factors.get("f_projected")
    if value is None or value <= 0:
        return None
    if value >= 75.0:
        return 120.0
    if value >= 50.0:
        return 90.0
    if value >= 30.0:
        return 70.0
    return 10.0


def _provider_below_balance(risk: t.RiskAssessment, threshold_days: float) -> bool:
    """Low-balance heuristic: f_remaining at the warning threshold or higher
    on a balance/credits-type factor.  Without raw runway in the risk
    object we use a stable proxy that mirrors the operator's concern
    (low funds and rising consumption both push this above 55)."""
    return risk.factors.get("f_remaining", 0.0) >= 55.0


def _provider_below_balance_prev(
    provider: str,
    prev_alert: dict[str, str],
    risk: t.RiskAssessment,
    threshold_days: float,
) -> bool:
    """Was this provider in the below-balance state previously?

    Without an explicit `prev_risk_by_provider` we use the prev_alert
    level for the balance window as a proxy: if the prior alert was
    WARNING+ on a window keyed as `*:balance:*` or `*:credits:*` we
    assume the state persisted.
    """
    for key, level in prev_alert.items():
        if key.startswith(f"snap:") or key.startswith(f"{provider}:tariff"):
            continue
        prefix, _, window_type = key.partition(":")
        _, _, tail_window = key.partition(":")
        window_type = tail_window.split(":", 1)[1] if ":" in tail_window else tail_window
        if prefix != provider:
            continue
        if window_type in {"balance", "credits"} and level in {
            t.LEVEL_WARNING,
            t.LEVEL_HIGH,
            t.LEVEL_CRITICAL,
        }:
            return True
    return False


# ---------------------------------------------------------------------------
# filter_cooldown
# ---------------------------------------------------------------------------
def filter_cooldown(
    drafts: list[t.EventDraft],
    recent_events: list[dict[str, Any]],
    cfg: Any,
) -> list[t.EventDraft]:
    """Drop drafts that repeat (provider, account, event_type) within cooldown.

    `recent_events` is the raw shape returned by `store.recent_events()`:
    each entry has at least `provider`, `account`, `event_type`,
    `created_at` (ISO).
    """
    cooldown_minutes = float(getattr(cfg, "event_cooldown_minutes", 30.0))
    if cooldown_minutes <= 0:
        return list(drafts)

    last_seen: dict[tuple[str, str, str], datetime] = {}
    for entry in recent_events:
        if not isinstance(entry, dict):
            continue
        provider = entry.get("provider")
        account = entry.get("account", "default")
        event_type = entry.get("event_type")
        created_raw = entry.get("created_at")
        if not (provider and event_type and created_raw):
            continue
        try:
            when = _parse_iso(str(created_raw))
        except (TypeError, ValueError):
            continue
        key = (str(provider), str(account), str(event_type))
        current = last_seen.get(key)
        if current is None or when > current:
            last_seen[key] = when

    allowed: list[t.EventDraft] = []
    for draft in drafts:
        key = (draft.provider, draft.account, draft.event_type)
        previous = last_seen.get(key)
        if previous is None:
            allowed.append(draft)
            continue
        delta = draft_created_at(draft) - previous
        if delta >= timedelta(minutes=cooldown_minutes):
            allowed.append(draft)
    return allowed


def draft_created_at(draft: t.EventDraft) -> datetime:
    """Parse the draft's `created_at` field without crashing on bad input."""
    try:
        return _parse_iso(draft.created_at)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
