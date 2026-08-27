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
        credits = payload.get("credits") or {}
        details = {
            "limit_reached": bool(rate.get("limit_reached")),
            "allowed": rate.get("allowed"),
            "source": source,
        }
        if source.endswith("usage endpoint"):
            details["warning"] = "Internal endpoint used by Codex; may change without notice."
        # Full parameter surface: keep everything the usage surface reports so
        # the UI parameter block can render it (all values are non-secret).
        details.update(_credit_params(credits))
        spend = payload.get("spend_control")
        if isinstance(spend, dict) and any(v is not None for v in spend.values()):
            details["spend_control"] = {
                "reached": bool(spend.get("reached")),
                "individual_limit": spend.get("individual_limit"),
            }
        promo = payload.get("promo")
        if promo is not None:
            details["promo"] = promo
        reset_credits = _reset_credits(payload)
        if reset_credits is not None:
            details["rate_limit_reset_credits"] = reset_credits
        extras = _additional_limits(payload)
        if extras:
            details["additional_rate_limits"] = extras
        code_review = _code_review_limits(payload)
        if code_review is not None:
            details["code_review_rate_limit"] = code_review
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
            payload = _app_server_to_payload(result)
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


def _app_server_to_payload(result: dict) -> dict:
    """Map the app-server ``account/rateLimits/read`` result to the snake_case
    payload shape of the HTTP usage endpoint, preserving every parameter the
    result carries (extra metered limits, spend control, reset credits)."""

    def window(row):
        if not isinstance(row, dict):
            return None
        mins = _int(row.get("windowDurationMins"))
        return {
            "used_percent": row.get("usedPercent"),
            "limit_window_seconds": None if mins is None else mins * 60,
            "reset_at": row.get("resetsAt"),
        }

    rate = _pick_codex_rate_limit(result)
    credits = rate.get("credits") if isinstance(rate.get("credits"), dict) else {}
    payload = {
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
            "overage_limit_reached": None,
            "balance": credits.get("balance"),
        },
    }
    spend = {"reached": rate.get("spendControlReached"), "individual_limit": rate.get("individualLimit")}
    if any(v is not None for v in spend.values()):
        payload["spend_control"] = spend
    by_id = result.get("rateLimitsByLimitId")
    extras: list[dict] = []
    if isinstance(by_id, dict):
        for limit_id, row in by_id.items():
            if limit_id == "codex" or not isinstance(row, dict):
                continue
            extras.append({
                "limit_name": row.get("limitName") or limit_id,
                "metered_feature": limit_id,
                "rate_limit": {
                    "primary_window": window(row.get("primary")),
                    "secondary_window": window(row.get("secondary")),
                },
            })
    if extras:
        payload["additional_rate_limits"] = extras
    reset = result.get("rateLimitResetCredits")
    if isinstance(reset, dict):
        payload["rate_limit_reset_credits"] = {
            "available_count": reset.get("availableCount"),
            "applicable_available_count": None,
            "credits": [
                {"title": entry.get("title")}
                for entry in reset.get("credits", [])
                if isinstance(entry, dict) and entry.get("title")
            ],
        }
    return payload


def _credit_params(credits: dict) -> dict:
    out: dict = {}
    for key in ("has_credits", "unlimited", "overage_limit_reached"):
        value = credits.get(key)
        if isinstance(value, bool):
            out[key] = value
    return out


def _limit_row(label: str, inner: object) -> dict | None:
    if not isinstance(inner, dict):
        return None

    def window_row(row) -> dict | None:
        if not isinstance(row, dict):
            return None
        seconds = _int(row.get("limit_window_seconds"))
        return {
            "period": _window_name(seconds, "window"),
            "used_percent": clamp_percent(row.get("used_percent")),
            "reset_at": epoch_to_iso(row.get("reset_at")),
        }

    rows = [r for r in (window_row(inner.get("primary_window")),
                        window_row(inner.get("secondary_window"))) if r]
    if not rows:
        return None
    return {"name": label, "windows": rows}


def _additional_limits(payload: dict) -> list[dict]:
    out: list[dict] = []
    for extra in payload.get("additional_rate_limits") or []:
        if not isinstance(extra, dict):
            continue
        label = str(extra.get("limit_name") or extra.get("metered_feature") or "").strip()
        if not label:
            continue
        row = _limit_row(label, extra.get("rate_limit"))
        if row is not None:
            out.append(row)
    return out


def _code_review_limits(payload: dict) -> dict | None:
    code_review = payload.get("code_review_rate_limit")
    if not isinstance(code_review, dict):
        return None
    return _limit_row("code review", code_review)


def _reset_credits(payload: dict) -> dict | None:
    reset = payload.get("rate_limit_reset_credits")
    if not isinstance(reset, dict):
        return None
    out = {
        "available_count": reset.get("available_count"),
        "applicable_available_count": reset.get("applicable_available_count"),
    }
    titles = [
        entry.get("title")
        for entry in reset.get("credits", [])
        if isinstance(entry, dict) and entry.get("title")
    ]
    if titles:
        out["titles"] = titles
    if all(v is None for v in out.values()):
        return None
    return out


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
