from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.collector import Collector, add_trends
from app.config import Settings
from app.engine import AnalyticsEngine
from app.store import Store

settings = Settings()
# Demo mode reads back its own demo-marked rows; live mode never mixes them in.
store = Store(settings.database_path, include_demo_rows=settings.demo_mode)
engine = AnalyticsEngine(store, settings)
collector = Collector(settings, store, on_collect=lambda: engine.refresh_all())


@asynccontextmanager
async def lifespan(_: FastAPI):
    await collector.collect()
    task = asyncio.create_task(collector.loop(), name="provider-collector")
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="AI Provider Observer", version="0.1.0", lifespan=lifespan)
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
def dashboard():
    return FileResponse(static_dir / "index.html")


def _analytics_disabled() -> dict[str, Any] | None:
    """Return the disabled-payload when analytics are off, else None."""
    if getattr(settings, "analytics_enabled", True):
        return None
    return {"analytics_enabled": False}


def _attach_risk(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add the cached per-provider ``risk`` block to /api/status payloads.

    Existing keys are preserved verbatim — the ``risk`` field is purely
    additive so existing dashboard contracts keep working.
    """
    cache = engine.data() or {}
    providers_cache = cache.get("providers") or []
    risk_by_provider = {entry.get("provider"): entry.get("risk") for entry in providers_cache}
    for snap in snapshots:
        snap["risk"] = risk_by_provider.get(snap.get("provider"))
    return snapshots


@app.get("/api/status")
def status():
    base = add_trends(store, store.latest())
    return {
        "demo_mode": settings.demo_mode,
        "poll_interval_seconds": settings.poll_interval_seconds,
        "providers": _attach_risk(base),
    }


@app.post("/api/refresh")
async def refresh():
    await collector.collect()
    base = add_trends(store, store.latest())
    return {"ok": True, "providers": _attach_risk(base)}


# ---------------------------------------------------------------------------
# Analytics endpoints (M4 — §28)
# ---------------------------------------------------------------------------


@app.get("/api/analytics")
def analytics():
    disabled = _analytics_disabled()
    if disabled is not None:
        return disabled
    return engine.data()


@app.get("/api/analytics/{provider}")
def analytics_provider(provider: str):
    disabled = _analytics_disabled()
    if disabled is not None:
        return disabled
    entry = engine.get_provider(provider)
    if entry is None:
        raise HTTPException(status_code=404, detail={"error": "unknown provider"})
    return entry


@app.get("/api/events")
def events(limit: int = Query(50, ge=1, le=500), provider: str | None = None):
    return {"events": store.recent_events(provider=provider, limit=limit)}


@app.get("/api/recommendations")
def recommendations():
    cache = engine.data() or {}
    providers = cache.get("providers") or []
    recs: list[dict[str, Any]] = []
    capacity: list[dict[str, Any]] = []
    for entry in providers:
        provider = entry.get("provider")
        risk_level = (entry.get("risk") or {}).get("level")
        available = risk_level in {"HEALTHY", "WATCH"}
        capacity.append(
            {
                "provider": provider,
                "level": risk_level,
                "available_capacity": bool(available),
            }
        )
        rec = entry.get("recommendation")
        if rec:
            recs.append({"provider": provider, "recommendation": rec})
    return {"recommendations": recs, "capacity_overview": capacity}


_ALLOWED_HISTORY_HOURS = (6, 24, 168, 720)


def _downsample_points(points: list[dict[str, Any]], target: int) -> list[dict[str, Any]]:
    """Uniform stride decimation to ``target`` rows.

    Always preserves the first and last point. ``target < 2`` returns
    the input unchanged. ``len(points) <= target`` returns a copy.
    """
    if target < 2 or len(points) <= target:
        return list(points)
    stride = len(points) / float(target)
    picked: list[dict[str, Any]] = []
    last_index = len(points) - 1
    for i in range(target):
        idx = int(round(i * stride))
        if idx > last_index:
            idx = last_index
        if i == target - 1:
            idx = last_index
        if not picked or picked[-1] is not points[idx]:
            picked.append(points[idx])
    return picked


@app.get("/api/history/{provider}/{window_type}")
def history(
    provider: str,
    window_type: str,
    hours: int = Query(6),
):
    if hours not in _ALLOWED_HISTORY_HOURS:
        raise HTTPException(status_code=400, detail={"error": "invalid hours value"})
    identities = store.series_identities(provider)
    now = datetime.now(timezone.utc)
    since_iso = (now - _timedelta(hours)).isoformat()
    merged: list[dict[str, Any]] = []
    for account, wtype in identities:
        if wtype != window_type:
            continue
        rows = store.load_series(provider, account, wtype, since_iso=since_iso)
        merged.extend(_strip_history_point(row) for row in rows)
    merged.sort(key=lambda r: r["collected_at"])
    if len(merged) > 1200:
        merged = _downsample_points(merged, 800)
    return {
        "provider": provider,
        "window_type": window_type,
        "hours": hours,
        "points": merged,
    }


def _strip_history_point(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "collected_at": row.get("collected_at"),
        "used": row.get("used"),
        "remaining": row.get("remaining"),
        "limit_value": row.get("limit_value"),
        "used_percent": row.get("used_percent"),
        "unit": row.get("unit"),
        "reset_at": row.get("reset_at"),
    }


def _timedelta(hours: int):
    # Local helper to avoid pulling timedelta into top-level imports.
    from datetime import timedelta
    return timedelta(hours=hours)


@app.get("/healthz")
def healthz():
    return {"ok": True}
