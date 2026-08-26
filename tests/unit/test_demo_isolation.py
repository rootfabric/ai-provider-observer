"""Demo/live data isolation.

A demo run pointed at the production database once poisoned the live Codex
panel: demo snapshots (fake 44%/71% quota) landed in the same SQLite file
and, being the newest rows, won every analytics read while the real
collector was failing. These tests pin the guards:

1. live store reads (default) never return demo-marked rows — even when
   they are the newest ones;
2. the demo store (``include_demo_rows=True``) still sees them;
3. demo mode defaults to its own database file;
4. ``app.main`` wires the live store with the filter enabled.
"""
from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.demo import demo_snapshots
from app.normalize import snapshot_to_rows
from app.store import DEMO_ROW_MARKER, Store

from tests.fakes import make_snapshot, make_window


def _write_poisoned_db(path: Path) -> None:
    """Real codex row first, newer fake demo row second (the poisoning shape)."""
    now = datetime.now(timezone.utc)
    store = Store(str(path))  # writes are mode-agnostic
    real = make_snapshot(
        "codex", "OpenAI Codex",
        windows=[make_window("week", 91.0)],
        when=now - timedelta(minutes=10),
    )
    fake = make_snapshot(
        "codex", "OpenAI Codex",
        windows=[make_window("week", 71.0)],
        demo=True,
        when=now,
    )
    for snap in (real, fake):
        store.save(snap)
        store.save_quota_snapshots(snapshot_to_rows(snap))


def test_live_reads_skip_newer_demo_rows(tmp_path) -> None:
    _write_poisoned_db(tmp_path / "mix.db")
    store = Store(str(tmp_path / "mix.db"))  # live mode (default)

    latest = store.latest()
    assert len(latest) == 1
    assert latest[0]["windows"][0]["used_percent"] == 91.0

    assert [s["windows"][0]["used_percent"] for s in store.recent("codex")] == [91.0]
    assert [r["used_percent"] for r in store.load_series("codex", "default", "weekly")] == [91.0]
    assert store.latest_quota("codex", "default", "weekly")["used_percent"] == 91.0
    assert store.series_identities("codex") == [("default", "weekly")]
    assert store.known_quota_providers() == ["codex"]


def test_demo_store_still_reads_demo_rows(tmp_path) -> None:
    _write_poisoned_db(tmp_path / "mix.db")
    store = Store(str(tmp_path / "mix.db"), include_demo_rows=True)

    assert [s["windows"][0]["used_percent"] for s in store.recent("codex")] == [71.0, 91.0]
    assert store.latest_quota("codex", "default", "weekly")["used_percent"] == 71.0


def test_demo_snapshots_carry_the_marker() -> None:
    """``app.demo`` snapshots must stay detectable for the live filter."""
    for snap in demo_snapshots():
        raw = snap.to_dict()
        assert raw.get("details", {}).get("demo") is True, snap.provider
        rows = snapshot_to_rows(snap)
        assert rows, snap.provider
        assert DEMO_ROW_MARKER in rows[0]["raw_json"], snap.provider


def test_pure_error_snapshots_stay_visible_in_live_mode(tmp_path) -> None:
    """Disabled/error snapshots (no demo marker) are never filtered."""
    now = datetime.now(timezone.utc)
    store = Store(str(tmp_path / "err.db"))
    snap = make_snapshot(
        "codex", "OpenAI Codex", status="disabled",
        error="No readable Codex auth", when=now,
    )
    store.save(snap)
    assert len(store.latest()) == 1
    assert store.latest()[0]["status"] == "disabled"


def test_demo_mode_defaults_to_separate_database(monkeypatch) -> None:
    import app.config as cfg

    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    try:
        reloaded = importlib.reload(cfg)
        assert reloaded.Settings().database_path.endswith("observer-demo.db")
    finally:
        monkeypatch.undo()
        importlib.reload(cfg)


def test_explicit_database_path_beats_demo_default(monkeypatch) -> None:
    import app.config as cfg

    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("DATABASE_PATH", "/tmp/explicit-live.db")
    try:
        reloaded = importlib.reload(cfg)
        assert reloaded.Settings().database_path == "/tmp/explicit-live.db"
    finally:
        monkeypatch.undo()
        importlib.reload(cfg)


def test_main_wires_live_store_with_demo_filter() -> None:
    """The live entrypoint must construct the store with the filter enabled."""
    text = (Path(__file__).resolve().parents[2] / "app" / "main.py").read_text(encoding="utf-8")
    assert "include_demo_rows=settings.demo_mode" in text
