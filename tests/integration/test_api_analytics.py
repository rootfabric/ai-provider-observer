"""End-to-end coverage of the /api/analytics surface (M4).

We build a temp SQLite database, seed it with synthetic quota history
(zai five_hour linearly growing; zai weekly at ~1.9x pace; deepseek
balance declining) and one synthetic event, then reload ``app.main`` so
it wires ``Collector``/``Engine``/``Store`` against that DB. After
``refresh_all`` the API is queried via ``TestClient``.
"""
from __future__ import annotations

import importlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# The history endpoint takes ``datetime.now()`` as the reference, so we
# anchor the seeded rows to the wall clock (not a fixed past moment) to
# keep ``/api/history`` populated inside the 6h window.
def _anchor_now() -> datetime:
    return datetime.now(timezone.utc)


def _purge_app_modules() -> None:
    for name in [n for n in list(sys.modules) if n == "app" or n.startswith("app.")]:
        sys.modules.pop(name, None)


def _sanitize_env(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> None:
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
    monkeypatch.setenv("HISTORY_LOOKBACK_HOURS", "200")


def _row(
    *,
    provider: str,
    account: str = "default",
    window_type: str = "five_hour",
    window_label: str | None = None,
    collected_at: str,
    used: float | None = None,
    remaining: float | None = None,
    limit_value: float | None = None,
    used_percent: float | None = None,
    unit: str | None = None,
    reset_at: str | None = None,
    reset_estimated: int = 0,
) -> dict:
    return {
        "provider": provider,
        "account": account,
        "window_type": window_type,
        "window_label": window_label or window_type,
        "collected_at": collected_at,
        "used": used,
        "remaining": remaining,
        "limit_value": limit_value,
        "used_percent": used_percent,
        "unit": unit,
        "reset_at": reset_at,
        "reset_estimated": reset_estimated,
        "raw_json": "{}",
    }


def _seed_zai_five_hour(now: datetime) -> list[dict]:
    """12 evenly-spaced points ending at ``now - 5 minutes``.

    The 5-minute offset keeps the seeded series inside the 1h/3h lookback
    windows while leaving room for the demo collector's wall-clock
    row (which uses absolute ``used`` units and would otherwise skew
    the OLS regression) to be removed before the analytics pass.
    """
    series_end = now - timedelta(minutes=5)
    reset_at = (now + timedelta(hours=3)).isoformat()
    rows: list[dict] = []
    for i in range(12):
        ts = (series_end - timedelta(hours=3) + timedelta(minutes=15 * i)).isoformat()
        pct = 40.0 + (52.0 - 40.0) * (i / 11.0)
        rows.append(
            _row(
                provider="zai",
                window_type="five_hour",
                window_label="5h",
                collected_at=ts,
                used=None,
                remaining=None,
                limit_value=100.0,
                used_percent=pct,
                unit="credits",
                reset_at=reset_at,
            )
        )
    return rows


def _seed_zai_weekly(now: datetime) -> list[dict]:
    """Weekly window with very high weekly projection."""
    reset_at = (now + timedelta(hours=92)).isoformat()
    rows: list[dict] = []
    for i in range(12):
        ts = (now - timedelta(hours=36) + timedelta(hours=3 * i)).isoformat()
        pct = 18.0 + 9.0 * i
        rows.append(
            _row(
                provider="zai",
                window_type="weekly",
                window_label="week",
                collected_at=ts,
                used=pct,
                remaining=max(0.0, 100.0 - pct),
                limit_value=100.0,
                used_percent=pct,
                unit="credits",
                reset_at=reset_at,
            )
        )
    return rows


def _seed_deepseek_balance(now: datetime) -> list[dict]:
    """Decreasing USD balance (10 -> 6 over 4 hours)."""
    rows: list[dict] = []
    for i in range(8):
        ts = (now - timedelta(hours=4) + timedelta(minutes=30 * i)).isoformat()
        bal = 10.0 - 0.5 * i
        rows.append(
            _row(
                provider="deepseek",
                window_type="balance",
                window_label="USD",
                collected_at=ts,
                used=None,
                remaining=bal,
                limit_value=None,
                used_percent=None,
                unit="USD",
                reset_at=None,
            )
        )
    return rows


@pytest.fixture
def app_under_test(tmp_path, monkeypatch):
    """Spin up ``app.main`` against a temp DB and seed quota history."""
    db_path = tmp_path / "analytics_api.db"
    _sanitize_env(monkeypatch, db_path)
    from app.store import Store

    # First pass: open Store just so the SQLite file is created and the
    # app sees it via DATABASE_PATH.
    Store(str(db_path))

    _purge_app_modules()
    main_mod = importlib.import_module("app.main")
    engine = main_mod.engine

    # Use the lifespan context manager so the demo ``collect()`` runs
    # (it writes 1 quota row per window/balance per provider).
    with TestClient(main_mod.app) as client:
        # Seed more quota history on top of the demo rows. Anchored to
        # the live wall clock so ``/api/history?hours=6`` keeps them.
        wall_now = _anchor_now()
        seeder = Store(str(db_path))
        # Drop the demo-collector rows for the windows we are going to
        # re-seed so they cannot skew the regression with very different
        # absolute values.
        import sqlite3
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "DELETE FROM quota_snapshots WHERE provider=? AND window_type=?",
                ("zai", "five_hour"),
            )
            conn.execute(
                "DELETE FROM quota_snapshots WHERE provider=? AND window_type=?",
                ("deepseek", "balance"),
            )
            conn.commit()
        seeder.save_quota_snapshots(_seed_zai_five_hour(wall_now))
        seeder.save_quota_snapshots(_seed_zai_weekly(wall_now))
        seeder.save_quota_snapshots(_seed_deepseek_balance(wall_now))
        seeder.insert_event(
            {
                "provider": "zai",
                "account": "default",
                "window_type": "five_hour",
                "event_type": "quota_warning",
                "severity": "warning",
                "created_at": (wall_now - timedelta(hours=1)).isoformat(),
                "dedup_key": "seeded:zai:default:quota_warning:five_hour",
                "payload": {"seeded": True},
            },
            cooldown_minutes=0,
        )
        # Force the engine to recompute against the full history.
        engine.refresh_all(now=wall_now)
        yield client, main_mod, seeder, engine


def test_analytics_returns_provider_blocks(app_under_test):
    client, main_mod, _store, _engine = app_under_test

    resp = client.get("/api/analytics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["analytics_enabled"] is True
    providers = {p["provider"] for p in body["providers"]}
    assert {"zai", "deepseek"}.issubset(providers)

    zai = next(p for p in body["providers"] if p["provider"] == "zai")
    assert zai["status"] == "ok"
    assert "five_hour" in zai["windows"]
    burn_1h = zai["windows"]["five_hour"]["burns"]["1h"]
    assert burn_1h["status"] == "ok"
    assert isinstance(burn_1h["value"], (int, float))
    assert 3.0 < burn_1h["value"] < 6.0

    deepseek = next(p for p in body["providers"] if p["provider"] == "deepseek")
    runway = deepseek["windows"].get("balance", {}).get("runway")
    assert runway is not None
    assert runway["runway_days"] is not None

    summary = body["summary"]
    assert "most_constrained" in summary
    assert "providers_healthy" in summary


def test_analytics_provider_block_and_404(app_under_test):
    client, _main, _store, _engine = app_under_test

    resp = client.get("/api/analytics/zai")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "zai"
    assert "windows" in body

    resp404 = client.get("/api/analytics/nope")
    assert resp404.status_code == 404
    payload = resp404.json()
    assert payload.get("detail", {}).get("error") == "unknown provider"


def test_history_endpoint_returns_points_and_rejects_bad_hours(app_under_test):
    client, _main, _store, _engine = app_under_test

    ok = client.get("/api/history/zai/five_hour?hours=6")
    assert ok.status_code == 200
    body = ok.json()
    assert body["provider"] == "zai"
    assert body["window_type"] == "five_hour"
    assert body["hours"] == 6
    assert len(body["points"]) >= 12
    point = body["points"][0]
    for key in (
        "collected_at",
        "used",
        "remaining",
        "limit_value",
        "used_percent",
        "unit",
        "reset_at",
    ):
        assert key in point

    bad = client.get("/api/history/zai/five_hour?hours=999")
    assert bad.status_code == 400


def test_events_endpoint_includes_seeded_event(app_under_test):
    client, _main, _store, _engine = app_under_test

    resp = client.get("/api/events")
    assert resp.status_code == 200
    body = resp.json()
    assert "events" in body
    seeded = [e for e in body["events"] if e.get("dedup_key", "").startswith("seeded:")]
    assert seeded, "seeded event must be returned by /api/events"


def test_recommendations_include_reason_lines(app_under_test):
    client, _main, _store, _engine = app_under_test

    resp = client.get("/api/recommendations")
    assert resp.status_code == 200
    body = resp.json()
    assert "recommendations" in body
    assert "capacity_overview" in body
    zai_rec = next(
        (r for r in body["recommendations"] if r.get("provider") == "zai"), None
    )
    assert zai_rec is not None
    rec = zai_rec["recommendation"]
    assert rec.get("reason_lines"), "zai recommendation must surface reason_lines"
    joined = " ".join(rec["reason_lines"]).lower()
    assert "projected" in joined or "weekly" in joined

    overview = body["capacity_overview"]
    assert isinstance(overview, list)
    assert any(item["provider"] == "zai" for item in overview)


def test_status_adds_risk_block_without_altering_existing_fields(app_under_test):
    client, _main, _store, _engine = app_under_test

    resp = client.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    for provider in body["providers"]:
        for key in (
            "provider",
            "label",
            "status",
            "checked_at",
            "windows",
            "balances",
            "details",
            "trends",
        ):
            assert key in provider
        assert "risk" in provider


def test_codex_window_insufficient_data(app_under_test):
    """A provider with no seeded quota history surfaces insufficient_data."""
    client, _main, _store, _engine = app_under_test

    body = client.get("/api/analytics").json()
    codex = next((p for p in body["providers"] if p["provider"] == "codex"), None)
    # Demo writes 1 quota row per codex window. With burn_min_points=3,
    # all burns return insufficient_data, status reflects that.
    assert codex is not None
    if codex["windows"]:
        for window in codex["windows"].values():
            assert window["status"] in {"ok", "insufficient_data"}
            if window["status"] == "insufficient_data":
                # risk_score must be None, not a fabricated zero.
                assert window["risk_score"] is None


def test_disabled_flag_short_circuits(tmp_path, monkeypatch):
    """ANALYTICS_ENABLED=false -> /api/analytics returns a flag payload."""
    db_path = tmp_path / "disabled.db"
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
    monkeypatch.setenv("ANALYTICS_ENABLED", "false")
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("HISTORY_LOOKBACK_HOURS", "200")

    _purge_app_modules()
    main_mod = importlib.import_module("app.main")
    with TestClient(main_mod.app) as client:
        resp = client.get("/api/analytics")
        assert resp.status_code == 200
        assert resp.json() == {"analytics_enabled": False}
        # Single-provider endpoint honours the same flag.
        single = client.get("/api/analytics/zai")
        assert single.status_code == 200
        assert single.json() == {"analytics_enabled": False}


def test_history_downsamples_when_too_many_points(app_under_test):
    client, main_mod, store2, _engine = app_under_test
    wall_now = _anchor_now()

    rows = []
    for i in range(1500):
        ts = (wall_now - timedelta(hours=720) + timedelta(minutes=i * 28)).isoformat()
        pct = 50.0 + (i % 5)
        rows.append(
            _row(
                provider="zai",
                window_type="five_hour",
                collected_at=ts,
                used=pct,
                remaining=100.0 - pct,
                limit_value=100.0,
                used_percent=pct,
                unit="credits",
                reset_at=(wall_now + timedelta(hours=3)).isoformat(),
            )
        )
    store2.save_quota_snapshots(rows)
    resp = client.get("/api/history/zai/five_hour?hours=720")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["points"]) <= 1200
    assert len(body["points"]) <= 800
