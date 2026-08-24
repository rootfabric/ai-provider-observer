from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Awaitable, Callable, Optional

from app.config import Settings
from app.demo import demo_snapshots
from app.models import ProviderSnapshot
from app.normalize import snapshot_to_rows
from app.providers import CodexProvider, DeepSeekProvider, MiniMaxProvider, OpenRouterProvider, ZaiProvider
from app.store import Store

log = logging.getLogger("observer.collector")

# Type alias: post-collect callback (sync or async, both supported).
OnCollect = Optional[Callable[[], Optional[Awaitable[None]]]]


class Collector:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        on_collect: OnCollect = None,
    ):
        self.settings = settings
        self.store = store
        self.on_collect = on_collect
        self._lock = asyncio.Lock()
        self.providers = [
            ZaiProvider(settings.zai_api_key, settings.zai_base_url, settings.request_timeout_seconds),
            MiniMaxProvider(settings.minimax_api_key, settings.minimax_base_url, settings.request_timeout_seconds),
            DeepSeekProvider(settings.deepseek_api_key, settings.deepseek_base_url, settings.request_timeout_seconds),
            OpenRouterProvider(settings.openrouter_api_key, settings.openrouter_management_key, settings.openrouter_base_url, settings.request_timeout_seconds),
            CodexProvider(settings.resolve_codex_auth_path(), settings.codex_base_url, settings.request_timeout_seconds),
        ]

    async def collect(self) -> list[dict]:
        async with self._lock:
            if self.settings.demo_mode:
                snaps = demo_snapshots()
            else:
                results = await asyncio.gather(*(p.fetch() for p in self.providers), return_exceptions=True)
                snaps: list[ProviderSnapshot] = []
                for provider, result in zip(self.providers, results):
                    if isinstance(result, Exception):
                        snaps.append(provider.error(f"Unhandled collector error: {type(result).__name__}: {result}"))
                    else:
                        snaps.append(result)
            for snap in snaps:
                self.store.save(snap)
                try:
                    rows = snapshot_to_rows(snap)
                    if rows:
                        self.store.save_quota_snapshots(rows, retention_days=self.settings.quota_retention_days)
                except Exception:
                    log.exception("quota history persistence failed")
        result = [s.to_dict() for s in snaps]
        await self._fire_on_collect()
        return result

    async def _fire_on_collect(self) -> None:
        if self.on_collect is None:
            return
        try:
            outcome = self.on_collect()
        except Exception:
            log.exception("on_collect callback raised")
            return
        if asyncio.iscoroutine(outcome):
            try:
                await outcome
            except Exception:
                log.exception("on_collect awaitable raised")

    async def loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.poll_interval_seconds)
            try:
                await self.collect()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("collector cycle failed")


def add_trends(store: Store, snapshots: list[dict]) -> list[dict]:
    for snap in snapshots:
        history = store.recent(snap["provider"], 20)
        snap["trends"] = _compute_trends(history)
    return snapshots


def _compute_trends(history: list[dict]) -> dict:
    if len(history) < 2:
        return {}
    latest = history[0]
    latest_time = _dt(latest.get("checked_at"))
    if not latest_time:
        return {}
    trends = {"windows": {}, "balances": {}}
    # Prefer an observation at least 5 minutes old, otherwise oldest available.
    previous = history[-1]
    for candidate in history[1:]:
        t = _dt(candidate.get("checked_at"))
        if t and (latest_time - t).total_seconds() >= 300:
            previous = candidate
            break
    previous_time = _dt(previous.get("checked_at"))
    if not previous_time:
        return {}
    hours = (latest_time - previous_time).total_seconds() / 3600
    if hours <= 0:
        return {}
    prev_windows = {w.get("name"): w for w in previous.get("windows", [])}
    for current in latest.get("windows", []):
        old = prev_windows.get(current.get("name"))
        if not old:
            continue
        a, b = current.get("used_percent"), old.get("used_percent")
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            # Do not compare across explicit reset boundaries.
            if current.get("reset_at") and old.get("reset_at") and current.get("reset_at") != old.get("reset_at"):
                continue
            trends["windows"][current.get("name")] = {"used_percent_per_hour": round((a - b) / hours, 2)}
    prev_balances = {b.get("currency"): b for b in previous.get("balances", [])}
    for current in latest.get("balances", []):
        old = prev_balances.get(current.get("currency"))
        a = current.get("total")
        b = old.get("total") if old else None
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            trends["balances"][current.get("currency")] = {"spend_per_hour": round((b - a) / hours, 6)}
    return trends


def _dt(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
