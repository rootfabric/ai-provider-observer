from __future__ import annotations

import json
import sqlite3
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
