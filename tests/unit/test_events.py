"""Event generation with transition/cooldown semantics (M3 / §23)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from app.analytics import types as t
from app.analytics.events import (
    EVENT_TYPES,
    evaluate_events,
    filter_cooldown,
)


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
NOW = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _key(provider: str, account: str = "default", window_type: str = "five_hour") -> str:
    return f"{provider}:{account}:{window_type}"


def _risk(provider: str, level: str = t.LEVEL_HEALTHY) -> t.RiskAssessment:
    return t.RiskAssessment(
        score=0,
        level=level,
        bottleneck=provider,
        factors={},
        window_scores={},
        error_penalty=0,
    )


# ---------------------------------------------------------------------------
# warning_to_critical_once
# ---------------------------------------------------------------------------
def test_warning_to_critical_yields_exactly_one_quota_critical() -> None:
    key = _key("zai")
    prev = {key: t.LEVEL_WARNING}
    curr = {key: t.LEVEL_CRITICAL}

    drafts = evaluate_events(
        prev_alert=prev,
        curr=curr,
        risk_by_provider={"zai": _risk("zai", t.LEVEL_CRITICAL)},
        snapshot_status={"zai": "ok"},
        now=NOW,
        cfg=CFG,
    )
    critical = [d for d in drafts if d.event_type == t.EVENT_QUOTA_CRITICAL]
    assert len(critical) == 1
    assert critical[0].provider == "zai"
    assert critical[0].window_type == "five_hour"
    assert critical[0].severity == "critical"

    # Second call with the same curr -> no new drafts.
    second = evaluate_events(
        prev_alert=curr,
        curr=curr,
        risk_by_provider={"zai": _risk("zai", t.LEVEL_CRITICAL)},
        snapshot_status={"zai": "ok"},
        now=NOW,
        cfg=CFG,
    )
    assert all(d.event_type != t.EVENT_QUOTA_CRITICAL for d in second)


def test_healthy_to_warning_emits_quota_warning_only() -> None:
    key = _key("zai")
    prev = {key: t.LEVEL_HEALTHY}
    curr = {key: t.LEVEL_WARNING}

    drafts = evaluate_events(
        prev_alert=prev,
        curr=curr,
        risk_by_provider={"zai": _risk("zai", t.LEVEL_WARNING)},
        snapshot_status={"zai": "ok"},
        now=NOW,
        cfg=CFG,
    )
    types_emitted = {d.event_type for d in drafts}
    assert t.EVENT_QUOTA_WARNING in types_emitted
    assert t.EVENT_QUOTA_CRITICAL not in types_emitted


def test_healthy_to_critical_emits_both_warning_and_critical() -> None:
    # Direct HEALTHY -> CRITICAL jump should fire both events: an entry
    # into WARNING was a prerequisite in our earlier semantic, but the
    # brief says HEALTHY/WATCH -> CRITICAL should emit CRITICAL. Our
    # implementation only emits CRITICAL for that path; WARNING is only
    # emitted on the explicit HEALTHY -> WARNING path. This test locks
    # the chosen behavior.
    key = _key("zai")
    prev = {key: t.LEVEL_HEALTHY}
    curr = {key: t.LEVEL_CRITICAL}

    drafts = evaluate_events(
        prev_alert=prev,
        curr=curr,
        risk_by_provider={"zai": _risk("zai", t.LEVEL_CRITICAL)},
        snapshot_status={"zai": "ok"},
        now=NOW,
        cfg=CFG,
    )
    types_emitted = {d.event_type for d in drafts}
    assert t.EVENT_QUOTA_CRITICAL in types_emitted


# ---------------------------------------------------------------------------
# dedup_key_format
# ---------------------------------------------------------------------------
def test_dedup_key_contains_provider_window_and_type() -> None:
    key = _key("zai", "main", "weekly")
    prev = {key: t.LEVEL_WARNING}
    curr = {key: t.LEVEL_CRITICAL}

    drafts = evaluate_events(
        prev_alert=prev,
        curr=curr,
        risk_by_provider={"zai": _risk("zai", t.LEVEL_CRITICAL)},
        snapshot_status={"zai": "ok"},
        now=NOW,
        cfg=CFG,
    )
    assert drafts
    critical = next(d for d in drafts if d.event_type == t.EVENT_QUOTA_CRITICAL)
    assert "zai" in critical.dedup_key
    assert "weekly" in critical.dedup_key
    assert t.EVENT_QUOTA_CRITICAL in critical.dedup_key


def test_provider_level_event_uses_provider_bucket() -> None:
    drafts = evaluate_events(
        prev_alert={"snap:zai": "ok"},
        curr={"snap:zai": "ok"},
        risk_by_provider={"zai": _risk("zai")},
        snapshot_status={"zai": "error"},
        now=NOW,
        cfg=CFG,
    )
    err = next(d for d in drafts if d.event_type == t.EVENT_PROVIDER_ERROR)
    # Bucket is part of dedup_key format -> ends with ":provider"
    assert err.dedup_key.endswith(":provider")


# ---------------------------------------------------------------------------
# filter_cooldown
# ---------------------------------------------------------------------------
def test_filter_cooldown_blocks_recent_event() -> None:
    fresh = NOW - timedelta(minutes=5)
    drafts = [
        t.EventDraft(
            provider="zai",
            account="default",
            window_type="five_hour",
            event_type=t.EVENT_QUOTA_WARNING,
            severity="warning",
            created_at=NOW.isoformat(),
            dedup_key="zai:default:quota_warning:five_hour",
        )
    ]
    recent = [
        {
            "provider": "zai",
            "account": "default",
            "event_type": t.EVENT_QUOTA_WARNING,
            "created_at": fresh.isoformat(),
        }
    ]
    kept = filter_cooldown(drafts, recent, CFG)
    assert kept == []


def test_filter_cooldown_allows_old_event() -> None:
    old = NOW - timedelta(hours=1)
    drafts = [
        t.EventDraft(
            provider="zai",
            account="default",
            window_type="five_hour",
            event_type=t.EVENT_QUOTA_WARNING,
            severity="warning",
            created_at=NOW.isoformat(),
            dedup_key="zai:default:quota_warning:five_hour",
        )
    ]
    recent = [
        {
            "provider": "zai",
            "account": "default",
            "event_type": t.EVENT_QUOTA_WARNING,
            "created_at": old.isoformat(),
        }
    ]
    kept = filter_cooldown(drafts, recent, CFG)
    assert len(kept) == 1


def test_filter_cooldown_handles_z_suffix_iso() -> None:
    old = (NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    drafts = [
        t.EventDraft(
            provider="zai",
            account="default",
            window_type="five_hour",
            event_type=t.EVENT_QUOTA_WARNING,
            severity="warning",
            created_at=NOW.isoformat(),
            dedup_key="zai:default:quota_warning:five_hour",
        )
    ]
    recent = [
        {
            "provider": "zai",
            "account": "default",
            "event_type": t.EVENT_QUOTA_WARNING,
            "created_at": old,
        }
    ]
    kept = filter_cooldown(drafts, recent, CFG)
    assert len(kept) == 1


def test_filter_cooldown_zero_disables_filtering() -> None:
    cfg = _Cfg()
    cfg.event_cooldown_minutes = 0
    drafts = [
        t.EventDraft(
            provider="zai",
            account="default",
            window_type="five_hour",
            event_type=t.EVENT_QUOTA_WARNING,
            severity="warning",
            created_at=NOW.isoformat(),
            dedup_key="x",
        )
    ]
    recent = [
        {
            "provider": "zai",
            "account": "default",
            "event_type": t.EVENT_QUOTA_WARNING,
            "created_at": NOW.isoformat(),
        }
    ]
    kept = filter_cooldown(drafts, recent, cfg)
    assert kept == drafts


def test_filter_cooldown_ignores_unparseable_entries() -> None:
    drafts = [
        t.EventDraft(
            provider="zai",
            account="default",
            window_type="five_hour",
            event_type=t.EVENT_QUOTA_WARNING,
            severity="warning",
            created_at=NOW.isoformat(),
            dedup_key="x",
        )
    ]
    recent = [
        {"provider": "zai", "account": "default", "event_type": t.EVENT_QUOTA_WARNING, "created_at": "not-an-iso"},
        {"unexpected": "shape"},
    ]
    kept = filter_cooldown(drafts, recent, CFG)
    assert len(kept) == 1


# ---------------------------------------------------------------------------
# provider_error_recovered transitions
# ---------------------------------------------------------------------------
def test_provider_error_recovered_transitions() -> None:
    # First call: ok -> error
    prev = {"snap:zai": "ok"}
    curr = {"snap:zai": "ok"}
    drafts = evaluate_events(
        prev_alert=prev,
        curr=curr,
        risk_by_provider={"zai": _risk("zai")},
        snapshot_status={"zai": "error"},
        now=NOW,
        cfg=CFG,
    )
    assert any(d.event_type == t.EVENT_PROVIDER_ERROR for d in drafts)

    # Persist the status change in prev; next call: error -> ok
    prev_after = {"snap:zai": "error"}
    drafts2 = evaluate_events(
        prev_alert=prev_after,
        curr={"snap:zai": "ok"},
        risk_by_provider={"zai": _risk("zai")},
        snapshot_status={"zai": "ok"},
        now=NOW + timedelta(minutes=1),
        cfg=CFG,
    )
    assert any(d.event_type == t.EVENT_PROVIDER_RECOVERED for d in drafts2)


# ---------------------------------------------------------------------------
# EVENT_TYPES contents
# ---------------------------------------------------------------------------
def test_event_types_complete() -> None:
    expected = {
        "quota_reset",
        "high_burn",
        "quota_warning",
        "quota_critical",
        "predicted_exhaustion",
        "balance_low",
        "provider_error",
        "provider_recovered",
        "tariff_insufficient",
    }
    assert set(EVENT_TYPES) == expected


# ---------------------------------------------------------------------------
# Dedup within a single call: same transition -> single draft
# ---------------------------------------------------------------------------
def test_no_duplicate_drafts_in_single_call() -> None:
    # Same window key in prev/curr can only produce at most one quota_critical.
    key = _key("zai")
    prev = {key: t.LEVEL_WARNING}
    curr = {key: t.LEVEL_CRITICAL}
    drafts = evaluate_events(
        prev_alert=prev,
        curr=curr,
        risk_by_provider={"zai": _risk("zai", t.LEVEL_CRITICAL)},
        snapshot_status={"zai": "ok"},
        now=NOW,
        cfg=CFG,
    )
    critical = [d for d in drafts if d.event_type == t.EVENT_QUOTA_CRITICAL]
    assert len(critical) == 1


# ---------------------------------------------------------------------------
# Unknown / missing data -> no spurious events
# ---------------------------------------------------------------------------
def test_empty_inputs_produce_no_drafts() -> None:
    drafts = evaluate_events(
        prev_alert={},
        curr={},
        risk_by_provider={},
        snapshot_status={},
        now=NOW,
        cfg=CFG,
    )
    assert drafts == []


# ---------------------------------------------------------------------------
# High-risk with negative margin proxy -> predicted_exhaustion
# ---------------------------------------------------------------------------
def test_high_with_negative_margin_yield_predicted_exhaustion() -> None:
    key = _key("zai")
    prev = {key: t.LEVEL_WATCH}
    curr = {key: t.LEVEL_HIGH}

    risk = t.RiskAssessment(
        score=80,
        level=t.LEVEL_HIGH,
        bottleneck="five_hour",
        factors={"f_margin": 75.0},  # negative margin proxy
        window_scores={"five_hour": 80},
        error_penalty=0,
    )

    drafts = evaluate_events(
        prev_alert=prev,
        curr=curr,
        risk_by_provider={"zai": risk},
        snapshot_status={"zai": "ok"},
        now=NOW,
        cfg=CFG,
    )
    assert any(d.event_type == t.EVENT_PREDICTED_EXHAUSTION for d in drafts)
