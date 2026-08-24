from __future__ import annotations

import json

from app.demo import demo_snapshots
from app.models import ProviderSnapshot, QuotaWindow
from app.normalize import redact, snapshot_to_rows


def _ts() -> str:
    return "2026-02-01T12:00:00+00:00"


def _by_type(rows, window_type):
    return [r for r in rows if r["window_type"] == window_type]


def test_zai_maps_5h_and_week():
    snap = ProviderSnapshot(
        "zai", "Z.AI", "ok", _ts(),
        windows=[
            QuotaWindow("5h", 61, 39, "2026-02-01T17:00:00+00:00", 1220, 2000, 780, "credits"),
            QuotaWindow("week", 34, 66, "2026-02-08T12:00:00+00:00", 3400, 10000, 6600, "credits"),
        ],
        details={"api_key": "secret-key"},
    )
    rows = snapshot_to_rows(snap)
    assert len(rows) == 2
    by_type = {r["window_type"] for r in rows}
    assert by_type == {"five_hour", "weekly"}
    assert all(r["window_label"] in {"5h", "week"} for r in rows)
    # raw_json should not contain the leaked api_key
    for row in rows:
        assert "api_key" not in row["raw_json"]
        assert "secret-key" not in row["raw_json"]
        parsed = json.loads(row["raw_json"])
        assert "api_key" not in parsed.get("details", {})


def test_minimax_unknown_window_labels_become_unknown_type():
    snap = ProviderSnapshot(
        "minimax", "MiniMax", "ok", _ts(),
        windows=[
            QuotaWindow("5h", 20, 80, "2026-02-01T17:00:00+00:00"),
            QuotaWindow("week", 40, 60, "2026-02-08T12:00:00+00:00"),
        ],
    )
    rows = snapshot_to_rows(snap)
    assert _by_type(rows, "five_hour") and _by_type(rows, "weekly")
    # Now an unlabeled window should map to unknown but still appear
    snap2 = ProviderSnapshot(
        "minimax", "MiniMax", "partial", _ts(),
        windows=[QuotaWindow("primary_window", 10, 90, None)],
    )
    rows2 = snapshot_to_rows(snap2)
    assert len(rows2) == 1
    assert rows2[0]["window_type"] == "unknown"
    assert rows2[0]["window_label"] == "primary_window"


def test_codex_unknown_window_names():
    snap = ProviderSnapshot(
        "codex", "OpenAI Codex", "ok", _ts(),
        windows=[
            QuotaWindow("primary_window", 44, 56, "2026-02-01T17:00:00+00:00"),
            QuotaWindow("window 7", 80, 20, "2026-02-08T12:00:00+00:00"),
        ],
    )
    rows = snapshot_to_rows(snap)
    assert len(rows) == 2
    assert all(r["window_type"] == "unknown" for r in rows)
    assert {r["window_label"] for r in rows} == {"primary_window", "window 7"}


def test_openrouter_yields_weekly_window_and_separate_balance_row():
    snap = ProviderSnapshot(
        "openrouter", "OpenRouter", "ok", _ts(),
        windows=[QuotaWindow("weekly", 42, 58, "2026-02-08T12:00:00+00:00", 21, 50, 29, "USD")],
        balances=[{"currency": "USD", "total": 36.12, "purchased": 100, "used": 63.88}],
        details={"key_label": "primary-key"},
    )
    rows = snapshot_to_rows(snap)
    assert any(r["window_type"] == "weekly" and r["window_label"] == "weekly" for r in rows)
    balances = _by_type(rows, "balance")
    assert len(balances) == 1
    bal = balances[0]
    assert bal["provider"] == "openrouter"
    assert bal["unit"] == "USD"
    assert bal["remaining"] == 36.12
    assert bal["limit_value"] == 100
    assert bal["used"] is None
    assert bal["reset_at"] is None
    # account resolved from key_label
    assert bal["account"] == "primary-key"


def test_deepseek_balance_row_only():
    snap = ProviderSnapshot(
        "deepseek", "DeepSeek", "ok", _ts(),
        balances=[{"currency": "USD", "total": 18.42, "granted": 0.0, "topped_up": 18.42}],
    )
    rows = snapshot_to_rows(snap)
    assert len(rows) == 1
    row = rows[0]
    assert row["window_type"] == "balance"
    assert row["unit"] == "USD"
    assert row["remaining"] == 18.42
    assert row["limit_value"] == 18.42
    assert row["used_percent"] is None


def test_error_snapshot_returns_empty():
    snap = ProviderSnapshot("zai", "Z.AI", "error", _ts(), error="boom")
    assert snapshot_to_rows(snap) == []


def test_demo_snapshots_round_trip():
    """All non-error demo snapshots must produce at least one row with a
    recognizable window_type or balance."""
    known_types = {"five_hour", "daily", "weekly", "monthly", "balance", "credits", "unknown"}
    for snap in demo_snapshots():
        rows = snapshot_to_rows(snap)
        if snap.status == "error":
            assert rows == []
            continue
        assert rows, f"expected rows for {snap.provider} {snap.status}"
        assert all(r["window_type"] in known_types for r in rows)
        for r in rows:
            assert r["collected_at"] == snap.checked_at
            parsed = json.loads(r["raw_json"])
            assert parsed["provider"] == snap.provider


def test_redact_removes_secret_like_keys_case_insensitive():
    payload = {
        "Authorization": "Bearer sk-xxx",
        "api_key": "k1",
        "API_KEY": "k2",
        "Management-Key": "m1",
        "Token": "t1",
        "secret": "s1",
        "details": {"api_key": "nested", "keep": "yes"},
        "list": [{"token": "x", "ok": 1}],
    }
    out = redact(payload)
    assert "Authorization" not in out
    assert "api_key" not in out
    assert "API_KEY" not in out
    assert "Management-Key" not in out
    assert "Token" not in out
    assert "secret" not in out
    assert out["details"]["keep"] == "yes"
    assert "api_key" not in out["details"]
    assert out["list"][0]["ok"] == 1
    assert "token" not in out["list"][0]


def test_raw_json_is_valid_compact_json():
    snap = ProviderSnapshot(
        "zai", "Z.AI", "ok", _ts(),
        windows=[QuotaWindow("5h", 30, 70, "2026-02-01T17:00:00+00:00")],
    )
    rows = snapshot_to_rows(snap)
    assert len(rows) == 1
    text = rows[0]["raw_json"]
    # ensure_ascii=False separators=(",", ":") -> compact, no spaces
    assert " " not in text
    parsed = json.loads(text)
    assert parsed["provider"] == "zai"
    assert parsed["windows"][0]["name"] == "5h"