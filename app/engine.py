"""Analytics engine (M4 — §28).

Bridges the pure analytics modules with the persistence layer and the
HTTP API. The engine owns:

* an in-memory cache refreshed by :meth:`refresh_all` after every
  collector cycle (and once at startup);
* alert-level transition memory between calls so we can emit event
  drafts only when the level actually changed;
* the shape the API surfaces in :meth:`data` and :meth:`get_provider`.

The engine is intentionally not a singleton: the lifespan in
``app/main.py`` constructs exactly one instance and exposes it via the
module-level ``engine`` global.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.analytics import events as events_mod
from app.analytics import plans as plans_mod
from app.analytics import recommendation as rec_mod
from app.analytics import risk as risk_mod
from app.analytics import series as series_mod
from app.analytics import types as t
from app.analytics.burn_rate import compute_acceleration, compute_burns
from app.analytics.forecast import build_forecast
from app.analytics.pacing import compute_pacing
from app.analytics.runway import compute_runway
from app.config import Settings
from app.store import Store

log = logging.getLogger("observer.engine")


_BALANCE_WINDOW_TYPES = {"balance", "credits"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _row_to_point(row: dict[str, Any]) -> t.QuotaPoint:
    used = _safe_float(row.get("used"))
    remaining = _safe_float(row.get("remaining"))
    limit_value = _safe_float(row.get("limit_value"))
    used_percent = _safe_float(row.get("used_percent"))
    unit = row.get("unit") if isinstance(row.get("unit"), str) else None
    collected_at = row.get("collected_at") or ""
    reset_at = row.get("reset_at")
    if not isinstance(reset_at, str):
        reset_at = None
    return t.QuotaPoint(
        collected_at=collected_at,
        used=used,
        remaining=remaining,
        limit_value=limit_value,
        used_percent=used_percent,
        unit=unit,
        reset_at=reset_at,
        reset_estimated=bool(row.get("reset_estimated")),
    )


def _span_minutes_of(points: list[t.QuotaPoint]) -> float | None:
    if len(points) < 2:
        return None
    first = series_mod.parse_iso_utc(points[0].collected_at)
    last = series_mod.parse_iso_utc(points[-1].collected_at)
    if first is None or last is None:
        return None
    delta = (last - first).total_seconds() / 60.0
    return max(0.0, delta)


def _segment_first_last_span_minutes(seg: t.Segment) -> float | None:
    return _span_minutes_of(seg.points)


# ---------------------------------------------------------------------------
# AnalyticsEngine
# ---------------------------------------------------------------------------

# Canonical display order for providers across the analytics payload and the
# dashboard (user-facing preference; unknown providers are appended last).
PROVIDER_DISPLAY_ORDER: tuple[str, ...] = ("zai", "minimax", "deepseek", "codex", "openrouter")


def _display_rank(provider: str) -> tuple[int, str]:
    """Sort key: canonical order first, then unknowns alphabetically."""
    if provider in PROVIDER_DISPLAY_ORDER:
        return PROVIDER_DISPLAY_ORDER.index(provider), ""
    return len(PROVIDER_DISPLAY_ORDER), provider


class AnalyticsEngine:
    """Compose all R1 analytics modules around a Store snapshot."""

    def __init__(self, store: Store, settings: Settings):
        self.store = store
        self.settings = settings
        # Alert levels for the previous refresh cycle, keyed by
        # ``provider:account:window_type`` AND ``snap:provider``.
        self._prev_alert: dict[str, str] = {}
        # Last computed cache (a plain dict, JSON-friendly).
        self._cache: dict[str, Any] = {
            "generated_at": None,
            "analytics_enabled": bool(getattr(settings, "analytics_enabled", True)),
            "providers": [],
            "summary": _empty_summary(),
        }
        # Plans loaded once per refresh.
        self._plans_cache: plans_mod.PlansConfig | None = None

    # ------------------------------------------------------------------ public

    def _collect_providers(self, since_iso: str) -> list[dict[str, Any]]:
        """Union of latest snapshots and quota-only providers.

        Latest snapshots win (their ``label``/``plan`` come from the
        collector). Providers that exist only in ``quota_snapshots`` are
        appended with synthetic placeholders so they still surface in
        the analytics payload.
        """
        latest = self.store.latest()
        merged: dict[str, dict[str, Any]] = {}
        for snap in latest:
            provider = snap.get("provider")
            if not provider:
                continue
            merged[provider] = {
                "provider": provider,
                "label": snap.get("label") or provider,
                "status": snap.get("status") or "ok",
                "plan": snap.get("plan"),
            }
        for provider in self.store.known_quota_providers():
            if provider in merged:
                continue
            merged[provider] = {
                "provider": provider,
                "label": provider,
                "status": "ok",
                "plan": None,
            }
        # Stable ordering: alphabetical.
        return [merged[key] for key in sorted(merged, key=_display_rank)]

    def refresh_all(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        since = now - timedelta(hours=int(self.settings.history_lookback_hours))
        since_iso = since.isoformat()

        providers = self._collect_providers(since_iso)
        plans_info = plans_mod.load_plans(self.settings.plans_config_path)
        self._plans_cache = plans_info

        new_alert: dict[str, str] = {}
        snapshot_status: dict[str, str] = {}
        providers_payload: list[dict[str, Any]] = []

        # First pass: build per-window analytics for every provider so we
        # can pass window lists into assess_provider / recommend.
        per_provider: dict[str, dict[str, Any]] = {}

        for provider_meta in providers:
            provider = provider_meta["provider"]
            status = provider_meta.get("status") or "ok"
            snapshot_status[provider] = status
            label = provider_meta.get("label") or provider
            plan_name = provider_meta.get("plan")

            identities = self.store.series_identities(provider, since_iso)
            if not identities:
                identities = self.store.series_identities(provider, None)

            windows: list[t.WindowAnalytics] = []
            windows_by_type: dict[str, list[t.WindowAnalytics]] = {}
            reset_drafts: list[t.EventDraft] = []
            for account, window_type in identities:
                rows = self.store.load_series(
                    provider, account, window_type, since_iso=since_iso
                )
                points = [_row_to_point(r) for r in rows]
                window_label: str | None = None
                if rows:
                    raw_label = rows[0].get("window_label")
                    if isinstance(raw_label, str):
                        window_label = raw_label
                cleaned, _dropped = series_mod.clean_points(points, self.settings)
                is_balance = window_type in _BALANCE_WINDOW_TYPES
                segments = series_mod.build_segments(
                    cleaned,
                    cfg=self.settings,
                    now=now,
                    is_balance=is_balance,
                    window_type=window_type,
                    account=account,
                )
                if not segments or not segments[-1].points:
                    continue
                current = segments[-1]

                unit = current.unit or (
                    current.points[-1].unit if current.points else None
                )
                burns = compute_burns(
                    current.points, now, unit, self.settings, window_type
                )
                accel: t.Acceleration | None = None
                b15 = burns.get("15m")
                b1h = burns.get("1h")
                if b15 is not None and b1h is not None:
                    accel = compute_acceleration(b15, b1h, self.settings)

                last_point = current.points[-1]
                span_minutes = _segment_first_last_span_minutes(current)
                forecast = build_forecast(
                    last_point, burns, now, self.settings, span_minutes
                )

                pacing: t.Pacing | None = None
                if window_type == "weekly":
                    pacing = compute_pacing(
                        current.points, burns, now, self.settings
                    )

                runway: t.Runway | None = None
                if is_balance:
                    runway = compute_runway(
                        current.points, unit, now, self.settings
                    )

                wa = t.WindowAnalytics(
                    provider=provider,
                    account=account,
                    window_type=window_type,
                    window_label=window_label,
                    latest_used_percent=last_point.used_percent,
                    latest_used=last_point.used,
                    latest_remaining=last_point.remaining,
                    latest_limit=last_point.limit_value,
                    unit=unit,
                    reset_at=last_point.reset_at,
                    reset_estimated=last_point.reset_estimated,
                    burns=burns,
                    burn_acceleration=accel,
                    forecast=forecast,
                    pacing=pacing,
                    runway=runway,
                    history_span_minutes=span_minutes,
                    points_available=len(current.points),
                )
                score, _factors = risk_mod.score_window(wa, self.settings)
                wa.risk_score = score
                wa.risk_level = _band_to_level(score)
                wa.alert_level = risk_mod.alert_level_for(wa, self.settings)
                wa.status = t.STATUS_OK

                windows.append(wa)
                windows_by_type.setdefault(window_type, []).append(wa)

                # quota_reset drafts: current segment started on a
                # detected reset and we know its reset_at.
                if (
                    current.has_reset_boundary
                    and last_point.reset_at is not None
                ):
                    reset_drafts.append(
                        t.EventDraft(
                            provider=provider,
                            account=account,
                            window_type=window_type,
                            event_type=t.EVENT_QUOTA_RESET,
                            severity="info",
                            created_at=_iso(now),
                            dedup_key=f"{provider}:{account}:quota_reset:{last_point.reset_at}",
                            payload={"reset_at": last_point.reset_at},
                        )
                    )

                key = f"{provider}:{account}:{window_type}"
                if wa.alert_level is not None:
                    new_alert[key] = wa.alert_level

            # Provider-level snapshot key for error/recovered events.
            new_alert[f"snap:{provider}"] = status

            # Errors over recent polls.
            recent_snaps = self.store.recent(provider, 15)
            total = len(recent_snaps)
            errors = sum(1 for s in recent_snaps if s.get("status") == "error")

            risk = risk_mod.assess_provider(
                windows, errors, total, self.settings
            )

            windows_block: dict[str, dict[str, Any]] = {}
            for wtype, group in windows_by_type.items():
                # Pick the worst (highest risk_score) per window_type.
                if len(group) == 1:
                    chosen = group[0]
                else:
                    chosen = max(
                        group,
                        key=lambda wa: (
                            wa.risk_score if wa.risk_score is not None else -1
                        ),
                    )
                windows_block[wtype] = chosen.to_dict()

            peer_providers: list[str] = []
            for other in providers:
                if other["provider"] == provider:
                    continue
                other_status = other.get("status") or "ok"
                if other_status not in {"error", "critical"}:
                    peer_providers.append(other["provider"])

            rec = rec_mod.recommend_for_provider(
                provider=provider,
                was=windows,
                risk=risk,
                plans_info=plans_info,
                peer_providers=peer_providers,
                cfg=self.settings,
            )

            providers_payload.append(
                {
                    "provider": provider,
                    "label": label,
                    "status": status,
                    "plan": plan_name,
                    "risk": risk.to_dict(),
                    "windows": windows_block,
                    "recommendation": rec.to_dict(),
                }
            )
            per_provider[provider] = {
                "meta": provider_meta,
                "windows": windows,
                "risk": risk,
                "reset_drafts": reset_drafts,
            }

        # ---- Events --------------------------------------------------------
        drafts: list[t.EventDraft] = []
        for provider, blob in per_provider.items():
            drafts.extend(blob["reset_drafts"])

        eval_drafts = events_mod.evaluate_events(
            prev_alert=dict(self._prev_alert),
            curr=new_alert,
            risk_by_provider={
                provider: blob["risk"] for provider, blob in per_provider.items()
            },
            snapshot_status=snapshot_status,
            now=now,
            cfg=self.settings,
        )
        drafts.extend(eval_drafts)

        # Cooldown / dedup via store.
        recent_events = self.store.recent_events(limit=200)
        allowed = events_mod.filter_cooldown(drafts, recent_events, self.settings)
        cooldown_minutes = float(
            getattr(self.settings, "event_cooldown_minutes", 30.0)
        )
        for draft in allowed:
            try:
                self.store.insert_event(
                    draft.to_dict(), cooldown_minutes=cooldown_minutes
                )
            except Exception:
                log.exception("insert_event failed for %s", draft.dedup_key)

        # Commit alert memory for next cycle.
        self._prev_alert = new_alert

        # ---- Summary -------------------------------------------------------
        summary = _compute_summary(providers_payload)
        # Add the quota_reset drafts' payload for completeness is not
        # required at the cache level — they are persisted above.

        self._cache = {
            "generated_at": _iso(now),
            "analytics_enabled": bool(
                getattr(self.settings, "analytics_enabled", True)
            ),
            "providers": providers_payload,
            "summary": summary,
        }

    # ------------------------------------------------------------------ access

    def data(self) -> dict[str, Any]:
        """Return the cached analytics snapshot (always available)."""
        return self._cache

    def get_provider(self, provider: str) -> dict[str, Any] | None:
        for entry in self._cache.get("providers", []):
            if entry.get("provider") == provider:
                return entry
        return None


# ---------------------------------------------------------------------------
# Helpers (level / summary)
# ---------------------------------------------------------------------------


def _band_to_level(score: int) -> str:
    if score >= 85:
        return t.LEVEL_CRITICAL
    if score >= 70:
        return t.LEVEL_HIGH
    if score >= 50:
        return t.LEVEL_WARNING
    if score >= 30:
        return t.LEVEL_WATCH
    return t.LEVEL_HEALTHY


def _empty_summary() -> dict[str, Any]:
    return {
        "providers_healthy": 0,
        "providers_watch": 0,
        "providers_warning": 0,
        "providers_critical": 0,
        "most_constrained": None,
        "first_expected_exhaustion": None,
        "highest_weekly_overspend": None,
        "lowest_runway": None,
    }


def _compute_summary(providers_payload: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute the global summary block (spec §20).

    Selection rules:

    * ``most_constrained`` — the provider with the highest ``risk.score``
      among providers that report at least one window. Ties break on
      alphabetical provider name for determinism.
    * ``first_expected_exhaustion`` — the minimum *positive*
      ``forecast.eta_current_seconds`` among windows with
      ``recovery_mode != 'unknown'``. Among those, prefer windows whose
      ``forecast.survival_margin_seconds < 0`` (deeply negative margin)
      so the operator sees the urgent one first; ties fall back to the
      smallest positive ETA.
    * ``highest_weekly_overspend`` — provider with the largest
      ``pacing.projected_whole_window > 100``; ``None`` when every
      weekly window is on or under the line.
    * ``lowest_runway`` — provider with the smallest
      ``runway.runway_days`` where ``runway.status == 'ok'``; ``None``
      when no balance windows produced an ``ok`` runway.
    """
    summary = _empty_summary()

    # Providers-with-windows subset (skip no-data providers from counters).
    active = [p for p in providers_payload if p.get("windows")]

    healthy = watch = warning = critical = 0
    for entry in active:
        level = (entry.get("risk") or {}).get("level")
        if level == t.LEVEL_HEALTHY:
            healthy += 1
        elif level == t.LEVEL_WATCH:
            watch += 1
        elif level == t.LEVEL_WARNING:
            warning += 1
        elif level in {t.LEVEL_HIGH, t.LEVEL_CRITICAL}:
            critical += 1
    summary["providers_healthy"] = healthy
    summary["providers_watch"] = watch
    summary["providers_warning"] = warning
    summary["providers_critical"] = critical

    # Most constrained.
    if active:
        ranked = sorted(
            active,
            key=lambda p: (
                -((p.get("risk") or {}).get("score", 0) or 0),
                p.get("provider", ""),
            ),
        )
        top = ranked[0]
        summary["most_constrained"] = {
            "provider": top.get("provider"),
            "bottleneck": (top.get("risk") or {}).get("bottleneck"),
            "score": (top.get("risk") or {}).get("score"),
        }

    # First expected exhaustion.
    candidates: list[tuple[float, bool, dict[str, Any]]] = []
    for entry in active:
        provider = entry.get("provider")
        windows = entry.get("windows") or {}
        for window_type, w in windows.items():
            forecast = (w or {}).get("forecast") or {}
            eta = forecast.get("eta_current_seconds")
            if eta is None or eta <= 0:
                continue
            recovery = forecast.get("recovery_mode")
            if recovery == "unknown":
                continue
            margin = forecast.get("survival_margin_seconds")
            negative_margin = margin is not None and margin < 0
            candidates.append((eta, not negative_margin, {
                "provider": provider,
                "window_type": window_type,
                "eta_seconds": eta,
            }))
    if candidates:
        # Prefer negative-margin entries (sort False first), then smallest ETA.
        candidates.sort(key=lambda item: (item[1], item[0]))
        first = candidates[0][2]
        summary["first_expected_exhaustion"] = {
            "provider": first["provider"],
            "window_type": first["window_type"],
            "eta_seconds": first["eta_seconds"],
        }

    # Highest weekly overspend.
    overspend_candidates: list[tuple[float, dict[str, Any]]] = []
    for entry in active:
        provider = entry.get("provider")
        windows = entry.get("windows") or {}
        weekly = windows.get("weekly")
        if not weekly:
            continue
        projected = ((weekly.get("pacing") or {}).get("projected_whole_window"))
        if projected is None or projected <= 100:
            continue
        overspend_candidates.append((projected, {
            "provider": provider,
            "projected_percent": projected,
        }))
    if overspend_candidates:
        overspend_candidates.sort(key=lambda item: -item[0])
        summary["highest_weekly_overspend"] = overspend_candidates[0][1]

    # Lowest runway.
    runway_candidates: list[tuple[float, dict[str, Any]]] = []
    for entry in active:
        provider = entry.get("provider")
        windows = entry.get("windows") or {}
        for wtype, w in windows.items():
            runway = (w or {}).get("runway")
            if not runway or runway.get("status") != t.STATUS_OK:
                continue
            days = runway.get("runway_days")
            if days is None:
                continue
            runway_candidates.append((days, {
                "provider": provider,
                "runway_days": days,
            }))
    if runway_candidates:
        runway_candidates.sort(key=lambda item: item[0])
        summary["lowest_runway"] = runway_candidates[0][1]

    return summary


__all__ = ["AnalyticsEngine"]
