"""Restart persistence test (M6 — §36.19).

Drives the collector twice in demo mode, captures the post-run state
(``load_series`` of zai/five_hour + ``engine.refresh_all`` result),
then constructs a fresh ``Store`` + ``AnalyticsEngine`` against the
same DB and proves the persisted history and the recomputed burn
match.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.analytics import types as t
from app.collector import Collector
from app.config import Settings
from app.engine import AnalyticsEngine
from app.store import Store


# IMPORTANT: ``app.config.Settings`` freezes its defaults at module
# import time, so monkeypatch.setenv applied AFTER the first
# ``Settings()`` call has no effect on the dataclass defaults. We use
# a tiny builder that returns a fresh settings instance every call;
# the values come from explicit kwargs (not env), so they survive any
# order of test execution.


def _settings_for_demo(db_path: Path) -> Settings:
    return Settings(
        database_path=str(db_path),
        demo_mode=True,
        demo_scenario="high_burn",
        analytics_enabled=True,
        history_lookback_hours=200,
        poll_interval_seconds=60,
        quota_retention_days=0,
        plans_config_path="/nonexistent.yaml",
    )


def _run_collector_twice(store: Store, settings: Settings) -> None:
    async def _two() -> list[dict]:
        c = Collector(settings, store)
        a = await c.collect()
        b = await c.collect()
        return a + b

    asyncio.run(_two())


def _snapshot_burn(store: Store, settings: Settings) -> tuple[list[dict], float | None]:
    """Return ``(zai/five_hour rows, burn_1h.value)`` from a fresh engine."""
    engine = AnalyticsEngine(store, settings)
    engine.refresh_all(now=datetime.now(timezone.utc))
    rows = store.load_series("zai", "default", "five_hour")
    zai = engine.get_provider("zai") or {}
    fh = (zai.get("windows") or {}).get("five_hour") or {}
    burns = fh.get("burns") or {}
    b1h = burns.get("1h", {}).get("value") if isinstance(burns, dict) else None
    return rows, b1h


def test_history_survives_engine_restart(tmp_path):
    """Collect twice in demo mode, drop the engine, build a new one on
    the same DB and verify series + burn metrics persist (§36.19)."""
    db_path = tmp_path / "restart.db"
    settings = _settings_for_demo(db_path)

    # First "process" — collect twice, snapshot state.
    store1 = Store(str(db_path), include_demo_rows=True)  # demo-mode pipeline
    _run_collector_twice(store1, settings)
    rows_before, burn_before = _snapshot_burn(store1, settings)
    assert rows_before, "history must be present after demo seeding + 2 collect cycles"
    assert burn_before is not None

    # Second "process" — fresh Store + Engine pointing at the same DB.
    settings2 = _settings_for_demo(db_path)
    store2 = Store(str(db_path), include_demo_rows=True)
    rows_after, burn_after = _snapshot_burn(store2, settings2)

    # Same number of rows and identical timestamps + used_percent.
    assert len(rows_after) == len(rows_before)
    keys = ("collected_at", "used_percent", "used", "limit_value", "reset_at")
    for a, b in zip(rows_before, rows_after):
        for k in keys:
            assert a.get(k) == b.get(k), (k, a, b)

    # burn_1h matches within a tiny epsilon (a deterministic OLS should be bitwise).
    assert burn_after is not None
    assert burn_before == pytest.approx(burn_after, rel=0, abs=1e-6)

    # Legacy snapshots table is intact: at least one row per demo cycle.
    snap_count = sum(
        len(store2.recent(provider, limit=50))
        for provider in ("zai", "deepseek", "minimax", "openrouter", "codex")
    )
    assert snap_count >= 10, snap_count
