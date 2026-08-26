"""Characterization tests for the current /api/status data contract.

These tests lock down the shape of the provider snapshot assembly pipeline
(Collector + add_trends + demo_snapshots + Store) so that R1 analytics
work can extend the contract additively without breaking the dashboard.

We deliberately avoid importing ``app.main`` (which constructs the global
``Store``/``Collector`` and can make network calls if real API keys are
present in the environment). Instead we build a Collector against a temp
SQLite DB under DEMO_MODE=true, with all API keys blanked, and inspect the
assembled dicts that ``/api/status`` would return.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from datetime import datetime, timezone

import pytest


EXPECTED_PROVIDERS = {"zai", "minimax", "deepseek", "openrouter", "codex"}
WINDOW_KEYS = {
    "name",
    "used_percent",
    "remaining_percent",
    "reset_at",
    "used",
    "limit",
    "remaining",
    "unit",
    "unlimited",
}
PROVIDER_KEYS = {
    "provider",
    "label",
    "status",
    "checked_at",
    "windows",
    "balances",
    "details",
}


def _purge_app_modules() -> None:
    """Drop every cached ``app.*`` module so they re-evaluate under a fresh env."""

    for name in [n for n in list(sys.modules) if n == "app" or n.startswith("app.")]:
        sys.modules.pop(name, None)


def _sanitize_env(monkeypatch: pytest.MonkeyPatch, db_path) -> None:
    """Configure env so ``app.config`` evaluates to a demo-mode, key-less Settings."""

    for key in (
        "ZAI_API_KEY",
        "MINIMAX_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENROUTER_MANAGEMENT_KEY",
        "CODEX_AUTH_PATH",
        "CODEX_HOME",
    ):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("DEMO_SCENARIO", "normal")
    monkeypatch.setenv("ANALYTICS_ENABLED", "true")
    monkeypatch.setenv("DATABASE_PATH", str(db_path))


@pytest.fixture
def settings_for_test(tmp_path, monkeypatch):
    db_path = tmp_path / "contract.db"
    _sanitize_env(monkeypatch, db_path)
    _purge_app_modules()

    # Re-import under the sanitized env. Order matters: config first, then
    # the modules that read its field defaults.
    config = importlib.import_module("app.config")
    store_mod = importlib.import_module("app.store")
    collector_mod = importlib.import_module("app.collector")

    settings = config.Settings()
    # The contract test drives the demo collector; the store must read back
    # its demo-marked rows (same wiring as app.main).
    store = store_mod.Store(settings.database_path, include_demo_rows=settings.demo_mode)
    collector = collector_mod.Collector(settings, store)
    return settings, store, collector, collector_mod


def _collect_once(collector) -> list[dict]:
    return asyncio.run(collector.collect())


def test_status_provider_shape_is_frozen(settings_for_test) -> None:
    settings, store, collector, collector_mod = settings_for_test
    assert settings.demo_mode is True

    snapshots = _collect_once(collector)
    assert {s["provider"] for s in snapshots} == EXPECTED_PROVIDERS

    with_trends = collector_mod.add_trends(store, store.latest())
    assert len(with_trends) == len(EXPECTED_PROVIDERS)

    for snap in with_trends:
        # Top-level provider record must carry the documented keys.
        missing = PROVIDER_KEYS - set(snap.keys())
        assert not missing, f"provider {snap.get('provider')!r} missing keys: {missing}"
        # Status/checked_at must be populated even for happy-path demo snapshots.
        assert snap["status"] == "ok"
        assert isinstance(snap["checked_at"], str)
        # checked_at must be a parseable ISO 8601 timestamp.
        parsed = datetime.fromisoformat(snap["checked_at"].replace("Z", "+00:00"))
        assert parsed.tzinfo is not None
        assert (datetime.now(timezone.utc) - parsed).total_seconds() < 60
        # details is a free-form mapping; just assert it is a dict.
        assert isinstance(snap["details"], dict)
        # trends is always present (may be empty when there is no prior observation).
        assert "trends" in snap
        assert isinstance(snap["trends"], dict)
        # When there is enough history, trends exposes windows/balances buckets.
        if snap["trends"]:
            assert set(snap["trends"].keys()) == {"windows", "balances"}


def test_status_windows_have_documented_keys(settings_for_test) -> None:
    _settings, store, collector, _collector_mod = settings_for_test
    snapshots = _collect_once(collector)

    for snap in snapshots:
        assert isinstance(snap["windows"], list)
        for window in snap["windows"]:
            missing = WINDOW_KEYS - set(window.keys())
            assert not missing, (
                f"window {window.get('name')!r} of {snap['provider']} missing: {missing}"
            )
            assert isinstance(window["name"], str)
            assert isinstance(window["unlimited"], bool)
            # reset_at is either None or an ISO 8601 string.
            if window["reset_at"] is not None:
                assert isinstance(window["reset_at"], str)
                datetime.fromisoformat(window["reset_at"].replace("Z", "+00:00"))


def test_demo_zai_has_5h_and_week_windows_with_numeric_used(settings_for_test) -> None:
    _settings, _store, collector, _collector_mod = settings_for_test
    snapshots = _collect_once(collector)
    zai = next(s for s in snapshots if s["provider"] == "zai")
    names = {w["name"] for w in zai["windows"]}
    assert {"5h", "week"}.issubset(names)
    for window in zai["windows"]:
        assert isinstance(window["used_percent"], (int, float))
        assert 0.0 <= window["used_percent"] <= 100.0
        # remaining_percent must round-trip with used_percent when both are numeric.
        assert isinstance(window["remaining_percent"], (int, float))
        assert abs((window["used_percent"] + window["remaining_percent"]) - 100.0) < 0.5


def test_history_grows_after_repeated_collect(settings_for_test) -> None:
    _settings, store, collector, _collector_mod = settings_for_test
    first = _collect_once(collector)
    second = _collect_once(collector)

    assert len(first) == len(EXPECTED_PROVIDERS)
    assert len(second) == len(EXPECTED_PROVIDERS)

    for snap in second:
        history = store.recent(snap["provider"], 10)
        assert len(history) >= 2, f"{snap['provider']}: expected >=2 history rows, got {len(history)}"
        latest = history[0]
        assert latest["provider"] == snap["provider"]
        assert latest["checked_at"] == snap["checked_at"]
