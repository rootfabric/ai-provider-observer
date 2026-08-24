"""Balance windows must be split per currency (spec §16/§17: never mix balances).

DeepSeek returns CNY and USD sub-balances; merging them into one series
fabricates a zig-zag "spend" (runway ≈ 0, monthly spend in the tens of
thousands) and false CRITICAL risk.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.engine import AnalyticsEngine
from app.store import Store

from tests.fakes import write_history


def _settings():
    return SimpleNamespace(
        poll_interval_seconds=60,
        history_lookback_hours=200,
        burn_min_points=3,
        burn_min_span_minutes=5.0,
        reset_drop_min_pp=5.0,
        reset_jitter_pp=2.0,
        accel_baseline_min=1.0,
        week_min_elapsed_pct=3.0,
        alert_warning_used=70.0,
        alert_high_used=85.0,
        alert_critical_used=95.0,
        alert_warning_projected_week=90.0,
        alert_critical_projected_week=120.0,
        alert_critical_eta_minutes=30.0,
        balance_low_days=7.0,
        recommended_headroom_factor=1.25,
        event_cooldown_minutes=30.0,
        plans_config_path="/nonexistent.yaml",
    )


def _balance_row(provider, unit, total, ts):
    return {
        "provider": provider,
        "account": "default",
        "window_type": "balance",
        "window_label": None,
        "collected_at": ts.isoformat(),
        "used": None,
        "remaining": total,
        "limit_value": None,
        "used_percent": None,
        "unit": unit,
        "reset_at": None,
        "reset_estimated": 0,
        "raw_json": "{}",
    }


def test_balance_currencies_are_separate_windows(tmp_path):
    store = Store(str(tmp_path / "cc.db"))
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=2)
    # Interleaved CNY (depleted, stable) and USD (real money, stable).
    rows = []
    for i in range(0, 121, 2):
        ts = start + timedelta(minutes=i)
        rows.append(_balance_row("deepseek", "CNY", -0.27, ts))
        rows.append(_balance_row("deepseek", "USD", 21.01, ts + timedelta(seconds=1)))
    store.save_quota_snapshots(rows)

    engine = AnalyticsEngine(store, _settings())
    engine.refresh_all(now=now)

    provider = engine.get_provider("deepseek")
    windows = provider["windows"]
    # Primary currency (largest latest balance = USD 21.01) keeps the plain key.
    assert "balance" in windows
    assert windows["balance"]["latest"]["unit"] == "USD"
    assert windows["balance"]["latest"]["remaining"] == 21.01
    # The depleted CNY sub-balance is preserved as a secondary window.
    assert "balance:CNY" in windows
    assert windows["balance:CNY"]["latest"]["unit"] == "CNY"

    # No fabricated spend: stable series must not produce a huge burn.
    usd_runway = windows["balance"]["runway"]
    assert usd_runway is not None
    spend = usd_runway.get("usd_per_day")
    assert spend is None or abs(spend) < 5.0, f"fabricated spend: {spend}"

    # Provider risk must not be CRITICAL from a stable balance.
    assert provider["risk"]["level"] in {"HEALTHY", "WATCH"}


def test_quota_windows_keep_absolute_unit(tmp_path):
    """Regression: quota windows must keep their own unit (credits), not None."""
    store = Store(str(tmp_path / "q.db"))
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=2)
    pts = [(start + timedelta(minutes=i), 40.0 + i * 0.1) for i in range(0, 121, 2)]
    write_history(store, "zai", "default", "five_hour", pts, unit="credits", limit_value=2000.0)

    engine = AnalyticsEngine(store, _settings())
    engine.refresh_all(now=now)
    fh = engine.get_provider("zai")["windows"]["five_hour"]
    assert fh["latest"]["unit"] == "credits"
    assert fh["burns"]["1h"]["unit"] == "credits/hour"
