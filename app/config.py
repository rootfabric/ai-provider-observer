from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


@dataclass(frozen=True, slots=True)
class Settings:
    poll_interval_seconds: int = max(30, int(os.getenv("POLL_INTERVAL_SECONDS", "60")))
    request_timeout_seconds: float = max(2.0, float(os.getenv("REQUEST_TIMEOUT_SECONDS", "12")))
    # Demo mode never writes into the production database by default: a demo
    # run pointed at the live DB once poisoned the Codex panel with fake
    # quota numbers. Override explicitly via DATABASE_PATH if you really want
    # a shared file.
    database_path: str = (
        os.getenv("DATABASE_PATH", "").strip()
        or ("./data/observer-demo.db" if _bool("DEMO_MODE") else "./data/observer.db")
    )
    demo_mode: bool = _bool("DEMO_MODE")

    # R1 analytics surface
    analytics_enabled: bool = _bool("ANALYTICS_ENABLED", True)
    demo_scenario: str = os.getenv("DEMO_SCENARIO", "normal").strip().lower()
    plans_config_path: str = os.getenv("PLANS_CONFIG_PATH", "./config/plans.yaml")
    quota_retention_days: int = int(os.getenv("QUOTA_RETENTION_DAYS", "0"))
    history_lookback_hours: int = int(os.getenv("HISTORY_LOOKBACK_HOURS", "200"))

    # Alert thresholds (spec §24)
    alert_warning_used: float = _float("ALERT_WARNING_USED", 70.0)
    alert_high_used: float = _float("ALERT_HIGH_USED", 85.0)
    alert_critical_used: float = _float("ALERT_CRITICAL_USED", 95.0)
    alert_warning_projected_week: float = _float("ALERT_WARNING_PROJECTED_WEEK", 90.0)
    alert_critical_projected_week: float = _float("ALERT_CRITICAL_PROJECTED_WEEK", 120.0)
    alert_critical_eta_minutes: float = _float("ALERT_CRITICAL_ETA_MINUTES", 30.0)
    balance_low_days: float = _float("BALANCE_LOW_DAYS", 7.0)

    # Analytics constants (tunable via env, sensible defaults)
    burn_min_points: int = int(os.getenv("BURN_MIN_POINTS", "3"))
    burn_min_span_minutes: float = _float("BURN_MIN_SPAN_MINUTES", 5.0)
    reset_drop_min_pp: float = _float("RESET_DROP_MIN_PP", 5.0)
    reset_jitter_pp: float = _float("RESET_JITTER_PP", 2.0)
    accel_baseline_min: float = _float("ACCEL_BASELINE_MIN", 1.0)
    week_min_elapsed_pct: float = _float("WEEK_MIN_ELAPSED_PCT", 3.0)
    recommended_headroom_factor: float = _float("RECOMMENDED_HEADROOM_FACTOR", 1.25)
    event_cooldown_minutes: float = _float("EVENT_COOLDOWN_MINUTES", 30.0)

    zai_api_key: str = os.getenv("ZAI_API_KEY", "").strip()
    zai_base_url: str = os.getenv("ZAI_BASE_URL", "https://api.z.ai").rstrip("/")

    minimax_api_key: str = os.getenv("MINIMAX_API_KEY", "").strip()
    minimax_base_url: str = os.getenv("MINIMAX_BASE_URL", "https://www.minimax.io").rstrip("/")

    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "").strip()
    deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")

    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "").strip()
    openrouter_management_key: str = os.getenv("OPENROUTER_MANAGEMENT_KEY", "").strip()
    openrouter_base_url: str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai").rstrip("/")

    codex_home: str = os.getenv("CODEX_HOME", "").strip()
    codex_auth_path: str = os.getenv("CODEX_AUTH_PATH", "").strip()
    codex_base_url: str = os.getenv("CODEX_BASE_URL", "https://chatgpt.com/backend-api").rstrip("/")

    def resolve_codex_auth_path(self) -> Path:
        if self.codex_auth_path:
            return Path(self.codex_auth_path).expanduser()
        home = Path(self.codex_home).expanduser() if self.codex_home else Path.home() / ".codex"
        return home / "auth.json"
