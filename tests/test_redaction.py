"""M7 — secrets never leave the observer (spec §36.20).

Four guards against leakage of API keys / management keys:

* **A. SQLite scan.** After ``TestClient`` lifespan runs the demo
  ``Collector.collect()``, open ``$DATABASE_PATH`` and walk every text
  column of every table (``snapshots.payload``, ``quota_snapshots.raw_json``,
  ``events.payload_json``) — none of the five fake secrets must appear.
* **B. API scan.** GET each public endpoint (``/``, ``/api/status``,
  ``/api/analytics``, ``/api/events``, ``/api/recommendations``,
  ``/api/history/<provider>/<window_type>?hours=6``) and recursively
  serialise the response into a JSON string; assert no secret substring.
* **C. Log capture.** Drive ``Collector.collect()`` under ``caplog`` at
  ``DEBUG`` and verify no secret substring appears in any record.
* **D. Unit guard.** ``normalize.redact`` strips every dict key matching
  the redaction regex from arbitrary nested structures while preserving
  the rest of the payload verbatim.

The setup mirrors ``tests/integration/test_api_analytics.py``:
``monkeypatch.setenv`` happens **before** any ``app.*`` import and the
modules are reloaded after ``sys.modules`` purge so ``Settings`` sees
the fake keys (and ``DEMO_MODE=true`` ensures no network is touched).
"""
from __future__ import annotations

import asyncio
import importlib
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


# Five clearly-distinguishable fake secrets — never collide with real keys.
SECRETS = {
    "ZAI_API_KEY": "sk-zai-SECRETVALUE123",
    "MINIMAX_API_KEY": "sk-cp-MINIMAXSECRET456",
    "DEEPSEEK_API_KEY": "sk-ds-DEEPSEEK789",
    "OPENROUTER_API_KEY": "sk-or-OPENROUTER000",
    "OPENROUTER_MANAGEMENT_KEY": "sk-or-mgmt-111",
}


def _purge_app_modules() -> None:
    """Drop every cached ``app.*`` module so they re-evaluate under a fresh env."""

    for name in [n for n in list(sys.modules) if n == "app" or n.startswith("app.")]:
        sys.modules.pop(name, None)


def _seed_env(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> None:
    """Configure env so ``app.config`` evaluates to demo mode with fake secrets."""

    for key, value in SECRETS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("DEMO_SCENARIO", "normal")
    monkeypatch.setenv("ANALYTICS_ENABLED", "true")
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("HISTORY_LOOKBACK_HOURS", "200")


def _serialise(value: Any) -> str:
    """Recursively dump ``value`` into a single string for substring scanning."""

    return json.dumps(value, ensure_ascii=False, default=str)


def _walk_rows(db_path: Path) -> list[str]:
    """Concatenate every text value from every column of every table."""

    blobs: list[str] = []
    with sqlite3.connect(str(db_path)) as conn:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        for tbl in tables:
            # Skip sqlite-internal tables (sqlite_sequence, sqlite_stat*).
            if tbl.startswith("sqlite_"):
                continue
            try:
                cols = [
                    row[1]
                    for row in conn.execute(f"PRAGMA table_info({tbl})").fetchall()
                ]
            except sqlite3.DatabaseError:
                continue
            for col in cols:
                try:
                    rows = conn.execute(
                        f"SELECT {col} FROM {tbl}"  # noqa: S608 — table/column from PRAGMA
                    ).fetchall()
                except sqlite3.DatabaseError:
                    continue
                for row in rows:
                    value = row[0]
                    if value is None:
                        continue
                    blobs.append(str(value))
    return blobs


def test_unit_redact_strips_secret_keys_and_keeps_others() -> None:
    """D. normalize.redact strips any dict key matching the redaction regex."""

    from app.normalize import redact

    payload = {
        "API_KEY": "sk-zai-SECRETVALUE123",
        "Authorization": "Bearer sk-cp-MINIMAXSECRET456",
        "X-Token": "tok-XYZ",
        "my_secret": "shh",
        "management_key": "mgmt",
        "details": {
            "api-key": "sk-ds-DEEPSEEK789",
            "name": "zai",
            "nested": [{"token": "abc", "value": 42}],
        },
        "list": [{"api_key": "k1", "used": 12}, {"x": 1}],
        "used_percent": 42.0,
        "label": "Z.AI",
    }
    out = redact(payload)
    # Every key matching the redaction regex disappears.
    assert "API_KEY" not in out
    assert "Authorization" not in out
    assert "X-Token" not in out
    assert "my_secret" not in out
    assert "management_key" not in out
    # Nested redaction.
    assert "api-key" not in out["details"]
    assert out["details"]["name"] == "zai"
    assert "token" not in out["details"]["nested"][0]
    assert out["details"]["nested"][0]["value"] == 42
    assert "api_key" not in out["list"][0]
    assert out["list"][0]["used"] == 12
    assert out["list"][1]["x"] == 1
    # Non-redacted data is untouched.
    assert out["used_percent"] == 42.0
    assert out["label"] == "Z.AI"


@pytest.fixture
def app_with_secrets(tmp_path, monkeypatch):
    """Spin up ``app.main`` under ``DEMO_MODE`` with fake secrets set."""

    db_path = tmp_path / "redaction.db"
    _seed_env(monkeypatch, db_path)
    _purge_app_modules()
    config = importlib.import_module("app.config")
    # Settings() reads env at __init__; ensure the fake values are visible.
    settings = config.Settings()
    field_map = {
        "ZAI_API_KEY": "zai_api_key",
        "MINIMAX_API_KEY": "minimax_api_key",
        "DEEPSEEK_API_KEY": "deepseek_api_key",
        "OPENROUTER_API_KEY": "openrouter_api_key",
        "OPENROUTER_MANAGEMENT_KEY": "openrouter_management_key",
    }
    for env_name, attr in field_map.items():
        assert getattr(settings, attr) == SECRETS[env_name], (
            f"Settings.{attr} did not pick up {env_name}"
        )
    main_mod = importlib.import_module("app.main")
    return main_mod, db_path


def test_redaction_a_db_does_not_contain_secrets(app_with_secrets) -> None:
    """A. No secret value lives anywhere in the SQLite file."""

    main_mod, db_path = app_with_secrets
    with TestClient(main_mod.app) as _client:
        # Lifespan runs the demo ``collect()`` once; that's enough to
        # populate snapshots + quota_snapshots + events.
        pass
    blobs = _walk_rows(db_path)
    joined = "\n".join(blobs)
    for name, secret in SECRETS.items():
        assert secret not in joined, (
            f"{name}={secret!r} found in {db_path} (redaction leak)"
        )


def test_redaction_b_api_responses_omit_secrets(app_with_secrets) -> None:
    """B. Every public endpoint serialises without leaking secrets."""

    main_mod, _db_path = app_with_secrets
    with TestClient(main_mod.app) as client:
        # Seed an event so /api/events has something to surface.
        from app.store import Store

        Store(str(_db_path)).insert_event(
            {
                "provider": "zai",
                "account": "default",
                "window_type": "five_hour",
                "event_type": "quota_warning",
                "severity": "warning",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "dedup_key": "redaction-test:zai:default:warning",
                "payload": {"note": "synthetic"},
            },
            cooldown_minutes=0,
        )

        endpoints = [
            "/",
            "/api/status",
            "/api/analytics",
            "/api/events",
            "/api/recommendations",
            "/api/history/zai/five_hour?hours=6",
            "/api/history/deepseek/balance?hours=24",
        ]
        for endpoint in endpoints:
            resp = client.get(endpoint)
            assert resp.status_code == 200, (
                f"{endpoint}: status {resp.status_code} body={resp.text!r}"
            )
            payload_text = _serialise(resp.json()) if resp.headers.get(
                "content-type", ""
            ).startswith("application/json") else resp.text
            for name, secret in SECRETS.items():
                assert secret not in payload_text, (
                    f"{name}={secret!r} leaked into {endpoint}"
                )


def test_redaction_c_logs_do_not_record_secrets(app_with_secrets, caplog) -> None:
    """C. caplog captures DEBUG output during collect(); secrets stay out."""

    main_mod, _db_path = app_with_secrets
    with caplog.at_level("DEBUG"):
        asyncio.run(main_mod.collector.collect())
    joined = "\n".join(record.getMessage() for record in caplog.records)
    # Also scan the formatted log output in case the keys appear in any
    # formatter-side data (exc_info, args, etc.).
    for record in caplog.records:
        joined += "\n" + str(record.args)
    for name, secret in SECRETS.items():
        assert secret not in joined, (
            f"{name}={secret!r} found in log records (redaction leak)"
        )
