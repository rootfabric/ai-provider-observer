"""Latest-snapshot details must reach the analytics provider payload.

Regression: providers report a rich non-secret parameter surface in
``ProviderSnapshot.details`` (codex credits flags, spend control, additional
metered limits), but ``AnalyticsEngine`` dropped it, so the dashboard could
not render the parameters at all.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.engine import AnalyticsEngine
from app.models import ProviderSnapshot
from app.store import Store


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


def test_snapshot_details_are_exposed_in_analytics_payload(tmp_path):
    store = Store(str(tmp_path / "details.db"))
    snap = ProviderSnapshot(
        provider="codex",
        label="OpenAI Codex",
        status="ok",
        checked_at="2026-08-27T02:00:00+00:00",
        plan="plus",
        details={
            "has_credits": False,
            "unlimited": False,
            "spend_control": {"reached": False, "individual_limit": None},
            "rate_limit_reset_credits": {"available_count": 1},
            "additional_rate_limits": [
                {"name": "gpt-reserve", "windows": [{"period": "week", "used_percent": 0.0}]},
            ],
            "source": "internal",  # technical key must still pass through; UI filters it
        },
    )
    store.save(snap)

    engine = AnalyticsEngine(store, _settings())
    engine.refresh_all()

    payload = engine.get_provider("codex")
    assert payload["details"]["has_credits"] is False
    assert payload["details"]["spend_control"] == {"reached": False, "individual_limit": None}
    assert payload["details"]["additional_rate_limits"][0]["name"] == "gpt-reserve"
