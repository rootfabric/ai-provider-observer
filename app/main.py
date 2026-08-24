from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.collector import Collector, add_trends
from app.config import Settings
from app.store import Store

settings = Settings()
store = Store(settings.database_path)
collector = Collector(settings, store)


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


@app.get("/api/status")
def status():
    return {
        "demo_mode": settings.demo_mode,
        "poll_interval_seconds": settings.poll_interval_seconds,
        "providers": add_trends(store, store.latest()),
    }


@app.post("/api/refresh")
async def refresh():
    await collector.collect()
    return {"ok": True, "providers": add_trends(store, store.latest())}


@app.get("/healthz")
def healthz():
    return {"ok": True}
