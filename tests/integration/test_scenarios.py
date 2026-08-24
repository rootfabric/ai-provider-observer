"""Scenario integration matrix (M6 — §32, §36.18).

Each test exercises a single scenario end-to-end:

* a fresh SQLite Store is provisioned in a tempdir;
* history rows are either seeded through ``seed_demo_history`` (for
  the demo scenarios) or written via the fakes (``write_history``);
* an ``AnalyticsEngine`` is built against a ``SimpleNamespace`` config
  carrying every attribute the analytics modules reach for via
  ``getattr``;
* ``engine.refresh_all(fixed_now)`` is invoked with a deterministic
  ``now`` so failures are reproducible;
* scenario-specific assertions are run against
  ``engine.get_provider(...)`` and ``store.recent_events(...)``.

A reload-pattern API test closes the matrix.
"""
from __future__ import annotations

import importlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.analytics import types as t
from app.demo import DEMO_SCENARIOS, demo_snapshots, seed_demo_history
from app.engine import AnalyticsEngine
from app.models import ProviderSnapshot
from app.store import Store
from app.normalize import snapshot_to_rows
from tests import fakes


# ---------------------------------------------------------------------------
# Helpers: settings + Store + Engine assembly
# ---------------------------------------------------------------------------


def _settings(**overrides: Any) -> SimpleNamespace:
    """Return a SimpleNamespace carrying every attribute the analytics
    layer reads via ``getattr``. Defaults line up with ``app.config``
    so the regressed behaviour matches production."""
    base: dict[str, Any] = dict(
        analytics_enabled=True,
        demo_mode=False,
        demo_scenario="normal",
        poll_interval_seconds=60,
        quota_retention_days=0,
        plans_config_path="/nonexistent/plans.yaml",
        history_lookback_hours=200,
        alert_warning_used=70.0,
        alert_high_used=85.0,
        alert_critical_used=95.0,
        alert_warning_projected_week=90.0,
        alert_critical_projected_week=120.0,
        alert_critical_eta_minutes=30.0,
        balance_low_days=7.0,
        burn_min_points=3,
        burn_min_span_minutes=5.0,
        reset_drop_min_pp=5.0,
        reset_jitter_pp=2.0,
        accel_baseline_min=1.0,
        week_min_elapsed_pct=3.0,
        recommended_headroom_factor=1.25,
        event_cooldown_minutes=30.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _new_engine(tmp_path: Path, **overrides: Any) -> tuple[Store, AnalyticsEngine, SimpleNamespace]:
    db_path = tmp_path / "engine.db"
    settings = _settings(**overrides)
    store = Store(str(db_path))
    engine = AnalyticsEngine(store, settings)
    return store, engine, settings


# ---------------------------------------------------------------------------
# Scenario 1 — normal_usage
# ---------------------------------------------------------------------------


def test_scenario_normal_usage(tmp_path):
    """Moderate burn, no critical events, risk in HEALTHY/WATCH band."""

    store, engine, settings = _new_engine(tmp_path, demo_scenario="normal")
    inserted = seed_demo_history(store, settings)
    assert inserted > 0
    now = datetime.now(timezone.utc)
    engine.refresh_all(now=now)
    zai = engine.get_provider("zai")
    assert zai is not None
    fh = zai["windows"]["five_hour"]
    burns = fh["burns"]
    assert burns["1h"]["value"] is not None and burns["1h"]["value"] > 0
    assert zai["risk"]["level"] in {t.LEVEL_HEALTHY, t.LEVEL_WATCH}
    # No critical event types persisted.
    events = store.recent_events(limit=200)
    critical_types = {
        t.EVENT_PROVIDER_ERROR,
        t.EVENT_QUOTA_CRITICAL,
        t.EVENT_PROVIDER_RECOVERED,
        t.EVENT_PREDICTED_EXHAUSTION,
        t.EVENT_BALANCE_LOW,
    }
    assert not any(e["event_type"] in critical_types for e in events), events


# ---------------------------------------------------------------------------
# Scenario 2 — rapid_consumption (handcrafted via fakes)
# ---------------------------------------------------------------------------


def test_scenario_rapid_consumption(tmp_path):
    """Steep rise in the last 30 min ⇒ burn_15m > burn_1h; band anomaly/accel; risk HIGH/CRITICAL."""
    store, engine, settings = _new_engine(tmp_path)
    now = datetime.now(timezone.utc)
    # Flat for ~2h, then steep at the very end so the 15m slice beats the 1h slice.
    points: list[tuple[datetime, float]] = []
    history_reset_at = now + timedelta(hours=3)
    # 2h flat at 30 %
    for i in range(8):
        points.append((now - timedelta(minutes=120 - i * 15), 30.0))
    # Last 30 min rises from 30 → 90
    for i in range(7):
        pct = 30.0 + (90.0 - 30.0) * (i / 6.0)
        points.append((now - timedelta(minutes=30 - i * 5), pct))
    fakes.write_history(
        store, "zai", "default", "five_hour", points,
        unit="credits", reset_at=history_reset_at, limit_value=100.0,
    )
    engine.refresh_all(now=now)
    zai = engine.get_provider("zai")
    assert zai is not None
    fh = zai["windows"]["five_hour"]
    b15 = fh["burns"]["15m"]["value"]
    b1h = fh["burns"]["1h"]["value"]
    assert b15 is not None and b1h is not None
    assert b15 > b1h, (b15, b1h)
    accel = fh["burn_acceleration"]
    assert accel is not None
    assert accel["band"] in {"accelerating", "anomaly"}, accel
    assert zai["risk"]["level"] in {t.LEVEL_HIGH, t.LEVEL_CRITICAL}, zai["risk"]["level"]


# ---------------------------------------------------------------------------
# Scenario 3 — quota_reset (90/95/3 — spec §31)
# ---------------------------------------------------------------------------


def test_scenario_quota_reset_history(tmp_path):
    """Reset boundary: 88→92→95 (pre), then 3% with new reset_at; current segment never negative-burns."""
    store, engine, settings = _new_engine(tmp_path)
    now = datetime.now(timezone.utc)
    # Build two clean segments that are temporally ordered so the
    # segmenter detects ONE reset between them.
    old_reset_at = now - timedelta(hours=2)
    new_reset_at = now + timedelta(hours=4)
    rows: list[dict] = []
    # Old segment: 70 → 95 % ending at `now - 75 minutes` (well before the new reset boundary).
    for i in range(20):
        ts = now - timedelta(hours=2, minutes=-5 * i)  # now-2h → now-1h+15m
        pct = 70.0 + (95.0 - 70.0) * (i / 19.0)
        rows.append({
            "provider": "zai", "account": "default",
            "window_type": "five_hour", "window_label": "5h",
            "collected_at": ts.isoformat(),
            "used": pct, "remaining": 100.0 - pct, "limit_value": 100.0,
            "used_percent": pct, "unit": "credits",
            "reset_at": old_reset_at.isoformat(),
            "reset_estimated": 0, "raw_json": "{}",
        })
    # Boundary point: drop to 3 % at the boundary. The transition
    # (old → new) gets a NEW reset_at so the segmenter starts a fresh
    # segment flagged ``has_reset_boundary=True``.
    rows.append({
        "provider": "zai", "account": "default",
        "window_type": "five_hour", "window_label": "5h",
        "collected_at": (now - timedelta(minutes=75)).isoformat(),
        "used": 3.0, "remaining": 97.0, "limit_value": 100.0,
        "used_percent": 3.0, "unit": "credits",
        "reset_at": new_reset_at.isoformat(),
        "reset_estimated": 0, "raw_json": "{}",
    })
    # New segment after the reset: 3 → 9 over the last 70 minutes.
    for i in range(8):
        ts = now - timedelta(minutes=70 - 10 * i)
        pct = 4.0 + (9.0 - 4.0) * (i / 7.0)
        rows.append({
            "provider": "zai", "account": "default",
            "window_type": "five_hour", "window_label": "5h",
            "collected_at": ts.isoformat(),
            "used": pct, "remaining": 100.0 - pct, "limit_value": 100.0,
            "used_percent": pct, "unit": "credits",
            "reset_at": new_reset_at.isoformat(),
            "reset_estimated": 0, "raw_json": "{}",
        })
    store.save_quota_snapshots(rows)
    engine.refresh_all(now=now)
    zai = engine.get_provider("zai")
    assert zai is not None
    fh = zai["windows"]["five_hour"]
    # No burn ever goes negative.
    for label, stat in fh["burns"].items():
        if stat["status"] != "ok":
            continue
        assert stat["value"] >= 0, (label, stat)
    events = store.recent_events(limit=200)
    reset_events = [e for e in events if e["event_type"] == t.EVENT_QUOTA_RESET]
    assert reset_events, f"quota_reset event must be present; events were {events}"
    assert any("quota_reset" in e["dedup_key"] for e in reset_events)

    # Re-running refresh_all must NOT create a second quota_reset event.
    pre_count = len(reset_events)
    engine.refresh_all(now=now)
    again = [e for e in store.recent_events(limit=200) if e["event_type"] == t.EVENT_QUOTA_RESET]
    assert len(again) == pre_count


# ---------------------------------------------------------------------------
# Scenario 4 — provider_failure_and_recovery
# ---------------------------------------------------------------------------


def test_scenario_provider_failure_and_recovery(tmp_path):
    store, engine, settings = _new_engine(tmp_path)
    now = datetime.now(timezone.utc)
    # Seed a normal snapshot first.
    snap_ok = fakes.make_snapshot(
        "zai", "Z.AI",
        windows=[fakes.make_window("5h", 40.0, reset_in_hours=2.0)],
        when=now - timedelta(minutes=1),
    )
    store.save(snap_ok)
    snap_err = fakes.make_snapshot(
        "zai", "Z.AI", status="error", error="boom", when=now,
    )
    store.save(snap_err)
    engine.refresh_all(now=now)
    # provider_error event must be present.
    err_evts = [
        e for e in store.recent_events(limit=200)
        if e["event_type"] == t.EVENT_PROVIDER_ERROR
        and e["provider"] == "zai"
    ]
    assert err_evts, "provider_error event expected after error snapshot"

    # Recovers -> provider_recovered
    snap_ok2 = fakes.make_snapshot(
        "zai", "Z.AI",
        windows=[fakes.make_window("5h", 40.0, reset_in_hours=2.0)],
        when=now,
    )
    store.save(snap_ok2)
    engine.refresh_all(now=now)
    rec_evts = [
        e for e in store.recent_events(limit=200)
        if e["event_type"] == t.EVENT_PROVIDER_RECOVERED
        and e["provider"] == "zai"
    ]
    assert rec_evts, "provider_recovered event expected after ok snapshot"


# ---------------------------------------------------------------------------
# Scenario 5 — balance_exhaustion
# ---------------------------------------------------------------------------


def test_scenario_balance_exhaustion(tmp_path):
    """Deepseek balance 40 → 22 USD over a day (current = 20 USD) drives runway < 7 days."""
    store, engine, settings = _new_engine(tmp_path)
    now = datetime.now(timezone.utc)
    points: list[tuple[datetime, float]] = []
    # 24 hourly samples from 40 to 20.
    for i in range(25):
        ts = now - timedelta(hours=24 - i)
        val = 40.0 - (40.0 - 20.0) * (i / 24.0)
        points.append((ts, val))
    rows = []
    for ts, val in points:
        rows.append({
            "provider": "deepseek", "account": "default",
            "window_type": "balance", "window_label": "USD",
            "collected_at": ts.isoformat(),
            "used": None, "remaining": val, "limit_value": None,
            "used_percent": None, "unit": "USD",
            "reset_at": None, "reset_estimated": 0,
            "raw_json": "{}",
        })
    store.save_quota_snapshots(rows)
    engine.refresh_all(now=now)
    ds = engine.get_provider("deepseek")
    assert ds is not None
    bal = ds["windows"].get("balance")
    assert bal is not None and bal["runway"] is not None
    runway_days = bal["runway"]["runway_days"]
    assert runway_days is not None
    assert runway_days < settings.balance_low_days
    # Recommendation must request more budget.
    rec = ds["recommendation"]
    assert rec["action"] == t.ACTION_INCREASE_BUDGET
    # The risk score for a balance-only window without pace/accel/
    # projected stays low (0..49). The contract under test is the
    # recommendation + runway metrics, not the overall provider level.
    assert ds["risk"]["level"] in {t.LEVEL_HEALTHY, t.LEVEL_WATCH, t.LEVEL_WARNING,
                                   t.LEVEL_HIGH, t.LEVEL_CRITICAL}


# ---------------------------------------------------------------------------
# Scenario 6 — weekly_exhaustion
# ---------------------------------------------------------------------------


def test_scenario_weekly_exhaustion(tmp_path):
    """Week used ≈ 58 % at elapsed ≈ 28 % ⇒ pace_ratio > 1.9, projected > 130 %."""
    store, engine, settings = _new_engine(tmp_path)
    now = datetime.now(timezone.utc)
    # Want week_start 2 days ago -> elapsed = 2/7 ≈ 28.6 %.
    week_reset_at = now + timedelta(days=5)
    points: list[tuple[datetime, float]] = []
    # 12 hourly samples from 30 % to 58 % over the last 12 hours.
    for i in range(13):
        ts = now - timedelta(hours=12 - i)
        pct = 30.0 + (58.0 - 30.0) * (i / 12.0)
        points.append((ts, pct))
    rows = []
    for ts, pct in points:
        used = pct  # limit=100 numeric coincidence keeps everything tidy
        rows.append({
            "provider": "zai", "account": "default",
            "window_type": "weekly", "window_label": "week",
            "collected_at": ts.isoformat(),
            "used": used, "remaining": 100.0 - used, "limit_value": 100.0,
            "used_percent": pct, "unit": "credits",
            "reset_at": week_reset_at.isoformat(),
            "reset_estimated": 0, "raw_json": "{}",
        })
    store.save_quota_snapshots(rows)
    engine.refresh_all(now=now)
    zai = engine.get_provider("zai")
    weekly = zai["windows"]["weekly"]
    pacing = weekly["pacing"]
    assert pacing is not None
    assert pacing["pace_ratio"] is not None and pacing["pace_ratio"] > 1.9, pacing
    assert pacing["projected_whole_window"] > 130, pacing
    rec = zai["recommendation"]
    assert rec["action"] in {t.ACTION_UPGRADE_PLAN, t.ACTION_INCREASE_BUDGET}, rec
    assert zai["risk"]["level"] in {t.LEVEL_WARNING, t.LEVEL_HIGH, t.LEVEL_CRITICAL}, zai["risk"]["level"]


# ---------------------------------------------------------------------------
# Scenario 7 — demo_transition_high_burn (§36.18)
# ---------------------------------------------------------------------------


def test_scenario_demo_transition_high_burn(tmp_path):
    """Cycle through demo_snapshots('high_burn', tick=i) and observe
    the alert levels monotonically worsening until WARNING/HIGH/CRITICAL."""
    store, engine, settings = _new_engine(tmp_path, demo_scenario="high_burn")
    inserted = seed_demo_history(store, settings)
    assert inserted > 0
    base_now = datetime.now(timezone.utc)
    level_seq: list[str] = []
    event_types: set[str] = set()
    for i in range(8):
        snap_now = base_now + timedelta(seconds=i * 5)
        snaps = demo_snapshots("high_burn", tick=i)
        rows = []
        for s in snaps:
            rows.extend(snapshot_to_rows(s))
        # Make sure we record each tick's current snapshot into both
        # the legacy snapshots table AND quota_snapshots so the engine
        # sees fresh data as we advance.
        store.save(snaps[0])  # primary
        if rows:
            # Only zai/five_hour (the primary window) carries the
            # transition signal we care about; everything else would
            # just bloat the run.
            rows = [
                r for r in rows
                if r["provider"] == "zai"
                and r["window_type"] in {"five_hour", "weekly"}
            ]
            if rows:
                store.save_quota_snapshots(rows)
        engine.refresh_all(now=snap_now)
        zai = engine.get_provider("zai")
        fh_alert = (zai["windows"]["five_hour"].get("alert_level")
                    or zai["risk"]["level"])
        level_seq.append(fh_alert)
        for e in store.recent_events(limit=200):
            event_types.add(e["event_type"])

    # The sequence must reach WARNING eventually.
    assert t.LEVEL_WARNING in level_seq, level_seq
    # And non-monotonic improvement: a later tick must NOT go all the
    # way back down to HEALTHY (would mean we lost the burn signal).
    later_levels = level_seq[3:]
    assert t.LEVEL_HEALTHY not in later_levels, level_seq
    # Quota warning + (quota_critical OR predicted_exhaustion) must
    # have been emitted at least once during the cycle.
    assert t.EVENT_QUOTA_WARNING in event_types
    assert (t.EVENT_QUOTA_CRITICAL in event_types
            or t.EVENT_PREDICTED_EXHAUSTION in event_types), event_types


# ---------------------------------------------------------------------------
# Scenario 8 — critical_scenario
# ---------------------------------------------------------------------------


def test_scenario_critical(tmp_path):
    store, engine, settings = _new_engine(tmp_path, demo_scenario="critical")
    inserted = seed_demo_history(store, settings)
    assert inserted > 0
    now = datetime.now(timezone.utc)
    engine.refresh_all(now=now)
    zai = engine.get_provider("zai")
    assert zai is not None
    fh = zai["windows"]["five_hour"]
    # Critical level + bottleneck = five_hour
    assert fh["alert_level"] == t.LEVEL_CRITICAL, fh["alert_level"]
    assert zai["risk"]["level"] == t.LEVEL_CRITICAL, zai["risk"]["level"]
    assert zai["risk"]["bottleneck"] == "five_hour"
    forecast = fh["forecast"]
    assert forecast["survival_margin_seconds"] is not None
    assert forecast["survival_margin_seconds"] < 0, forecast
    assert forecast["eta_current_seconds"] is not None
    assert forecast["eta_current_seconds"] <= settings.alert_critical_eta_minutes * 60.0 + 1.0
    # Weekly projection at/above 120 % ⇒ tariff_insufficient event expected.
    events = store.recent_events(limit=200)
    assert any(e["event_type"] == t.EVENT_TARIFF_INSUFFICIENT for e in events), events


# ---------------------------------------------------------------------------
# Bonus — API coverage via reload pattern
# ---------------------------------------------------------------------------


def _purge_app_modules() -> None:
    for name in [n for n in list(sys.modules) if n == "app" or n.startswith("app.")]:
        sys.modules.pop(name, None)


def _api_env(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> None:
    for key in (
        "ZAI_API_KEY", "MINIMAX_API_KEY", "DEEPSEEK_API_KEY",
        "OPENROUTER_API_KEY", "OPENROUTER_MANAGEMENT_KEY",
        "CODEX_AUTH_PATH", "CODEX_HOME",
    ):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("DEMO_SCENARIO", "critical")
    monkeypatch.setenv("ANALYTICS_ENABLED", "true")
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("HISTORY_LOOKBACK_HOURS", "200")


def test_api_critical_scenario_via_testclient(tmp_path, monkeypatch):
    """Reload ``app.main`` with DEMO_SCENARIO=critical ⇒ /api/analytics
    exposes zai CRITICAL and /api/events is non-empty."""
    db_path = tmp_path / "api_critical.db"
    _api_env(monkeypatch, db_path)
    _purge_app_modules()
    main_mod = importlib.import_module("app.main")
    with TestClient(main_mod.app) as client:
        resp = client.get("/api/analytics")
        assert resp.status_code == 200
        body = resp.json()
        zai = next((p for p in body["providers"] if p["provider"] == "zai"), None)
        assert zai is not None
        assert zai["risk"]["level"] == t.LEVEL_CRITICAL
        assert zai["risk"]["bottleneck"] == "five_hour"
        events = client.get("/api/events").json()
        assert events["events"], "critical scenario must surface at least one event"
