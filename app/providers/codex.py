from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from app.models import ProviderSnapshot, QuotaWindow
from app.providers.base import Provider, clamp_percent, epoch_to_iso, utc_now_iso


class CodexProvider(Provider):
    provider_id = "codex"
    label = "OpenAI Codex"

    def __init__(self, auth_path: Path, base_url: str, timeout: float):
        super().__init__(timeout)
        self.auth_path = auth_path
        self.base_url = base_url

    @staticmethod
    def parse(payload: dict, latency_ms: int, source: str = "Codex ChatGPT usage endpoint") -> ProviderSnapshot:
        rate = payload.get("rate_limit") or {}
        windows = []
        for key in ("primary_window", "secondary_window"):
            row = rate.get(key)
            if not isinstance(row, dict):
                continue
            seconds = _int(row.get("limit_window_seconds"))
            name = _window_name(seconds, key)
            used = clamp_percent(row.get("used_percent"))
            windows.append(QuotaWindow(
                name=name,
                used_percent=used,
                remaining_percent=None if used is None else round(100 - used, 2),
                reset_at=epoch_to_iso(row.get("reset_at")),
            ))
        details = {
            "limit_reached": bool(rate.get("limit_reached")),
            "allowed": rate.get("allowed"),
            "source": source,
        }
        if source.endswith("usage endpoint"):
            details["warning"] = "Internal endpoint used by Codex; may change without notice."
        credits = payload.get("credits") or {}
        balances = []
        if isinstance(credits, dict) and credits.get("balance") not in (None, ""):
            try:
                balances.append({"currency": "USD", "total": float(credits.get("balance"))})
            except (TypeError, ValueError):
                pass
        return ProviderSnapshot(
            provider="codex", label="OpenAI Codex", status="ok" if windows else "partial",
            checked_at=utc_now_iso(), latency_ms=latency_ms,
            plan=str(payload.get("plan_type")) if payload.get("plan_type") else None,
            windows=windows, balances=balances, details=details,
        )

    async def fetch(self) -> ProviderSnapshot:
        http_error: str | None = None
        if self.auth_path.exists():
            try:
                return await self._fetch_via_http()
            except Exception as exc:
                http_error = f"{type(exc).__name__}: {exc}"
        # Supported local Codex surface. This also works when credentials are stored
        # in the OS credential store instead of a readable auth.json.
        if shutil.which("codex"):
            try:
                return await self._fetch_via_app_server()
            except Exception as exc:
                app_error = f"{type(exc).__name__}: {exc}"
                message = f"Codex app-server quota query failed: {app_error}"
                if http_error:
                    message += f"; direct usage endpoint also failed: {http_error}"
                return self.error(message)
        if http_error:
            message = f"Codex quota query failed: {http_error}"
            if "401" in http_error or "403" in http_error:
                message += ". Re-run Codex login so credentials are refreshed."
            return self.error(message)
        return self.disabled(
            f"No readable Codex auth at {self.auth_path} and 'codex' is not on PATH. Run Codex and sign in with ChatGPT."
        )

    async def _fetch_via_http(self) -> ProviderSnapshot:
        auth = json.loads(self.auth_path.read_text(encoding="utf-8"))
        tokens = auth.get("tokens") or {}
        access_token = tokens.get("access_token")
        account_id = tokens.get("account_id")
        if not access_token:
            raise RuntimeError("auth.json has no ChatGPT access token")
        url = f"{self.base_url}/wham/usage" if "/backend-api" in self.base_url else f"{self.base_url}/api/codex/usage"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "User-Agent": "codex-cli",
        }
        if account_id:
            headers["ChatGPT-Account-Id"] = str(account_id)
        payload, latency = await self.get_json(url, headers)
        return self.parse(payload, latency)

    async def _fetch_via_app_server(self) -> ProviderSnapshot:
        loop = asyncio.get_running_loop()
        started = loop.time()
        proc = await asyncio.create_subprocess_exec(
            "codex", "app-server", "--stdio",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert proc.stdin is not None and proc.stdout is not None
        try:
            await _send(proc, {
                "method": "initialize",
                "id": 1,
                "params": {"clientInfo": {"name": "ai_provider_observer", "title": "AI Provider Observer", "version": "0.1.0"}},
            })
            await _read_response(proc, 1, self.timeout)
            await _send(proc, {"method": "initialized", "params": {}})
            await _send(proc, {"method": "account/rateLimits/read", "id": 2})
            response = await _read_response(proc, 2, self.timeout)
            if response.get("error"):
                raise RuntimeError(str(response["error"]))
            result = response.get("result") or {}
            rate = _pick_codex_rate_limit(result)
            payload = _app_server_to_payload(rate)
            latency = round((loop.time() - started) * 1000)
            return self.parse(payload, latency, source="Codex app-server account/rateLimits/read")
        finally:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=1)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()


async def _send(proc: asyncio.subprocess.Process, message: dict) -> None:
    assert proc.stdin is not None
    proc.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode())
    await proc.stdin.drain()


async def _read_response(proc: asyncio.subprocess.Process, request_id: int, timeout: float) -> dict:
    assert proc.stdout is not None

    async def read_until() -> dict:
        while True:
            line = await proc.stdout.readline()
            if not line:
                raise RuntimeError("Codex app-server closed stdout")
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("id") == request_id:
                return obj

    return await asyncio.wait_for(read_until(), timeout=timeout)


def _pick_codex_rate_limit(result: dict) -> dict:
    by_id = result.get("rateLimitsByLimitId")
    if isinstance(by_id, dict) and isinstance(by_id.get("codex"), dict):
        return by_id["codex"]
    rate = result.get("rateLimits")
    return rate if isinstance(rate, dict) else {}


def _app_server_to_payload(rate: dict) -> dict:
    def window(row):
        if not isinstance(row, dict):
            return None
        mins = _int(row.get("windowDurationMins"))
        return {
            "used_percent": row.get("usedPercent"),
            "limit_window_seconds": None if mins is None else mins * 60,
            "reset_at": row.get("resetsAt"),
        }

    credits = rate.get("credits") if isinstance(rate.get("credits"), dict) else {}
    return {
        "plan_type": rate.get("planType"),
        "rate_limit": {
            "allowed": None,
            "limit_reached": rate.get("rateLimitReachedType") is not None,
            "primary_window": window(rate.get("primary")),
            "secondary_window": window(rate.get("secondary")),
        },
        "credits": {
            "has_credits": credits.get("hasCredits"),
            "unlimited": credits.get("unlimited"),
            "balance": credits.get("balance"),
        },
    }


def _int(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _window_name(seconds: int | None, fallback: str) -> str:
    if seconds is None:
        return fallback.replace("_window", "")
    if 17_000 <= seconds <= 19_000:
        return "5h"
    if 590_000 <= seconds <= 620_000:
        return "week"
    if seconds % 3600 == 0 and seconds < 7 * 86400:
        return f"{seconds // 3600}h"
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    return fallback.replace("_window", "")
