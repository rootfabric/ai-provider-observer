from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.models import ProviderSnapshot


class Store:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            db.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_provider_id ON snapshots(provider, id DESC)")
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS quota_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    account TEXT NOT NULL DEFAULT 'default',
                    window_type TEXT NOT NULL,
                    window_label TEXT,
                    collected_at TEXT NOT NULL,
                    used REAL, remaining REAL, limit_value REAL,
                    used_percent REAL, unit TEXT,
                    reset_at TEXT, reset_estimated INTEGER NOT NULL DEFAULT 0,
                    raw_json TEXT NOT NULL
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_quota_series "
                "ON quota_snapshots(provider, account, window_type, collected_at)"
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL, account TEXT NOT NULL DEFAULT 'default',
                    window_type TEXT, event_type TEXT NOT NULL, severity TEXT NOT NULL,
                    created_at TEXT NOT NULL, dedup_key TEXT NOT NULL, payload_json TEXT,
                    UNIQUE(dedup_key)
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_time ON events(provider, created_at)"
            )

    def _connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def save(self, snap: ProviderSnapshot) -> None:
        payload = json.dumps(snap.to_dict(), ensure_ascii=False, separators=(",", ":"))
        with self._connect() as db:
            db.execute(
                "INSERT INTO snapshots(provider, checked_at, payload) VALUES(?,?,?)",
                (snap.provider, snap.checked_at, payload),
            )
            # Keep roughly two weeks at 1-minute polling per provider, plus headroom.
            db.execute(
                """DELETE FROM snapshots WHERE id IN (
                    SELECT id FROM snapshots WHERE provider=? ORDER BY id DESC LIMIT -1 OFFSET 25000
                )""",
                (snap.provider,),
            )

    def latest(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT s.payload FROM snapshots s
                JOIN (SELECT provider, MAX(id) AS max_id FROM snapshots GROUP BY provider) x
                  ON x.max_id=s.id
                ORDER BY s.provider
                """
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def recent(self, provider: str, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT payload FROM snapshots WHERE provider=? ORDER BY id DESC LIMIT ?",
                (provider, max(2, min(limit, 500))),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    # --- M1 quota history -------------------------------------

    _QUOTA_COLUMNS = (
        "provider", "account", "window_type", "window_label", "collected_at",
        "used", "remaining", "limit_value", "used_percent", "unit",
        "reset_at", "reset_estimated", "raw_json",
    )

    def save_quota_snapshots(self, rows: list[dict], retention_days: int = 0) -> int:
        if not rows:
            return 0
        payload: list[tuple] = []
        for row in rows:
            payload.append(tuple(row[col] for col in self._QUOTA_COLUMNS))
        placeholders = ",".join("?" for _ in self._QUOTA_COLUMNS)
        columns = ",".join(self._QUOTA_COLUMNS)
        with self._connect() as db:
            db.executemany(
                f"INSERT INTO quota_snapshots({columns}) VALUES({placeholders})",
                payload,
            )
            if retention_days and retention_days > 0:
                cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
                db.execute("DELETE FROM quota_snapshots WHERE collected_at < ?", (cutoff,))
        return len(rows)

    def load_series(
        self,
        provider: str,
        account: str,
        window_type: str,
        since_iso: str | None = None,
        until_iso: str | None = None,
    ) -> list[dict]:
        clauses = ["provider=?", "account=?", "window_type=?"]
        params: list[Any] = [provider, account, window_type]
        if since_iso is not None:
            clauses.append("collected_at>=?")
            params.append(since_iso)
        if until_iso is not None:
            clauses.append("collected_at<?")
            params.append(until_iso)
        where = " AND ".join(clauses)
        with self._connect() as db:
            rows = db.execute(
                f"SELECT {','.join(self._QUOTA_COLUMNS)} FROM quota_snapshots "
                f"WHERE {where} ORDER BY collected_at ASC, id ASC",
                params,
            ).fetchall()
        result: list[dict] = []
        for row in rows:
            entry = dict(zip(self._QUOTA_COLUMNS, row))
            if isinstance(entry.get("reset_estimated"), int):
                entry["reset_estimated"] = bool(entry["reset_estimated"])
            result.append(entry)
        return result

    def known_quota_providers(self) -> list[str]:
        """Return the list of distinct providers with quota_snapshots rows."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT DISTINCT provider FROM quota_snapshots ORDER BY provider"
            ).fetchall()
        return [row[0] for row in rows]

    def series_identities(
        self, provider: str, since_iso: str | None = None
    ) -> list[tuple[str, str]]:
        """Return distinct (account, window_type) pairs for ``provider``.

        When ``since_iso`` is given only pairs whose latest collected_at
        is >= ``since_iso`` are returned — keeps the engine fast for the
        lookback window.
        """
        if since_iso is not None:
            query = (
                "SELECT account, window_type FROM quota_snapshots "
                "WHERE provider=? AND collected_at>=? "
                "GROUP BY account, window_type "
                "HAVING MAX(collected_at)>=? "
                "ORDER BY account, window_type"
            )
            params: tuple[Any, ...] = (provider, since_iso, since_iso)
        else:
            query = (
                "SELECT account, window_type FROM quota_snapshots "
                "WHERE provider=? "
                "GROUP BY account, window_type "
                "ORDER BY account, window_type"
            )
            params = (provider,)
        with self._connect() as db:
            rows = db.execute(query, params).fetchall()
        return [(row[0], row[1]) for row in rows]

    def latest_quota(self, provider: str, account: str, window_type: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                f"SELECT {','.join(self._QUOTA_COLUMNS)} FROM quota_snapshots "
                "WHERE provider=? AND account=? AND window_type=? "
                "ORDER BY collected_at DESC, id DESC LIMIT 1",
                (provider, account, window_type),
            ).fetchone()
        if row is None:
            return None
        entry = dict(zip(self._QUOTA_COLUMNS, row))
        if isinstance(entry.get("reset_estimated"), int):
            entry["reset_estimated"] = bool(entry["reset_estimated"])
        return entry

    def insert_event(self, event: dict, cooldown_minutes: float = 30.0) -> bool:
        provider = event["provider"]
        account = event.get("account", "default")
        event_type = event["event_type"]
        created_at = event["created_at"]
        payload_value = event.get("payload")
        payload_json = json.dumps(payload_value, ensure_ascii=False, separators=(",", ":")) if payload_value is not None else None
        with self._connect() as db:
            if cooldown_minutes and cooldown_minutes > 0:
                cutoff_dt = _parse_iso(created_at) - timedelta(minutes=cooldown_minutes)
                if cutoff_dt is not None:
                    recent = db.execute(
                        "SELECT created_at FROM events WHERE provider=? AND account=? AND event_type=? "
                        "ORDER BY created_at DESC, id DESC LIMIT 1",
                        (provider, account, event_type),
                    ).fetchone()
                    if recent is not None:
                        recent_dt = _parse_iso(recent[0])
                        if recent_dt is not None and recent_dt >= cutoff_dt:
                            return False
            cur = db.execute(
                "INSERT OR IGNORE INTO events"
                "(provider, account, window_type, event_type, severity, created_at, dedup_key, payload_json)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (
                    provider,
                    account,
                    event.get("window_type"),
                    event_type,
                    event["severity"],
                    created_at,
                    event["dedup_key"],
                    payload_json,
                ),
            )
            return cur.rowcount > 0

    def recent_events(self, provider: str | None = None, limit: int = 50) -> list[dict]:
        limit = max(1, min(limit, 500))
        with self._connect() as db:
            if provider is None:
                rows = db.execute(
                    "SELECT provider, account, window_type, event_type, severity, "
                    "created_at, dedup_key, payload_json FROM events "
                    "ORDER BY created_at DESC, id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT provider, account, window_type, event_type, severity, "
                    "created_at, dedup_key, payload_json FROM events "
                    "WHERE provider=? ORDER BY created_at DESC, id DESC LIMIT ?",
                    (provider, limit),
                ).fetchall()
        result: list[dict] = []
        for row in rows:
            entry = {
                "provider": row[0],
                "account": row[1],
                "window_type": row[2],
                "event_type": row[3],
                "severity": row[4],
                "created_at": row[5],
                "dedup_key": row[6],
            }
            if row[7] is not None:
                try:
                    entry["payload"] = json.loads(row[7])
                except json.JSONDecodeError:
                    entry["payload"] = row[7]
            result.append(entry)
        return result


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt