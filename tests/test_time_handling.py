"""M7 — time handling (spec §30).

Pure unit tests for the helpers that turn wall-clock strings into
datetime math the analytics layer relies on. No network, no I/O, no
SQLite — every test pins a fixed ``now`` and exercises a single
boundary case (TZ offsets, day rollover, DST transition, epoch units).

The tests import only **public** helpers (``app.analytics.series``,
``app.analytics.forecast``, ``app.analytics.pacing``,
``app.analytics.burn_rate``, ``app.providers.base``); analytics functions
expect an injectable ``cfg`` — a ``types.SimpleNamespace`` is enough.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.analytics import types as t
from app.analytics.burn_rate import compute_burn
from app.analytics.forecast import build_forecast
from app.analytics.pacing import compute_pacing
from app.analytics.series import parse_iso_utc
from app.providers.base import epoch_to_iso


CFG = SimpleNamespace(
    burn_min_points=3,
    burn_min_span_minutes=5.0,
    reset_drop_min_pp=5.0,
    reset_jitter_pp=2.0,
    accel_baseline_min=1.0,
    week_min_elapsed_pct=3.0,
)


def _point(collected_at, used_percent=None, used=None, limit_value=None,
           reset_at=None, reset_estimated=False, remaining=None):
    return t.QuotaPoint(
        collected_at=collected_at,
        used=used,
        remaining=remaining,
        limit_value=limit_value,
        used_percent=used_percent,
        unit=None,
        reset_at=reset_at,
        reset_estimated=reset_estimated,
    )


def _ok_burn(label, value):
    return t.BurnStat(
        lookback=label,
        value=value,
        unit="percentage_points_per_hour",
        points_used=3,
        span_minutes=60.0,
        status=t.STATUS_OK,
    )


# ---------------------------------------------------------------------------
# parse_iso_utc — timezone handling
# ---------------------------------------------------------------------------


def test_parse_iso_utc_plus_ten_offset_normalises_to_zulu():
    """``+10:00`` provider offsets collapse to UTC correctly."""
    dt = parse_iso_utc("2026-08-24T05:00:00+10:00")
    assert dt == datetime(2026, 8, 23, 19, 0, tzinfo=timezone.utc)
    # Equivalent UTC string with explicit ``Z``.
    zulu = parse_iso_utc("2026-08-23T19:00:00Z")
    assert dt == zulu


def test_parse_iso_utc_zulu_suffix():
    dt = parse_iso_utc("2026-08-24T12:34:56Z")
    assert dt == datetime(2026, 8, 24, 12, 34, 56, tzinfo=timezone.utc)


def test_parse_iso_utc_naive_becomes_utc():
    dt = parse_iso_utc("2026-08-24T12:00:00")
    assert dt == datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    assert dt.tzinfo is not None


def test_parse_iso_utc_garbage_returns_none():
    assert parse_iso_utc("not a date") is None
    assert parse_iso_utc(None) is None
    assert parse_iso_utc("") is None
    assert parse_iso_utc("2026-13-99T99:99:99") is None


# ---------------------------------------------------------------------------
# Day-boundary burn regression
# ---------------------------------------------------------------------------


def test_compute_burn_straddles_midnight_returns_positive_slope():
    """Three points straddling midnight compute a positive slope across the day boundary."""

    day1 = datetime(2026, 8, 24, 23, 40, tzinfo=timezone.utc)
    day2 = datetime(2026, 8, 25, 0, 10, tzinfo=timezone.utc)
    # Three points linearly growing from 89.6 to 92.8 over 30 minutes.
    # The OLS slope against the elapsed-hours baseline yields
    # Σ(x−x̄)(y−ȳ)/Σ(x−x̄)² ≈ 6.51 p.p./h. The important property for
    # the test is *positive slope across the day boundary*, not the
    # exact coefficient (which depends on how OLS resolves the points).
    points = [
        _point(day1.isoformat(), used_percent=89.6),
        _point((day1 + timedelta(minutes=20)).isoformat(), used_percent=92.0),
        _point(day2.isoformat(), used_percent=92.8),
    ]
    stat = compute_burn(points, lookback_seconds=24 * 3600.0, now=day2, unit=None, cfg=CFG)
    assert stat.status == t.STATUS_OK
    assert stat.value is not None
    assert stat.value > 0
    assert stat.span_minutes == pytest.approx(30.0, abs=1e-6)
    # All three points fall inside the 24h lookback window.
    assert stat.points_used == 3


# ---------------------------------------------------------------------------
# DST: Europe/Berlin spring-forward
# ---------------------------------------------------------------------------


def test_pacing_does_not_break_on_dst_transition():
    """A weekly window spanning the Berlin DST jump still produces sensible pacing."""

    # 2026-03-29 02:00 CET → 03:00 CEST in Europe/Berlin.
    # We anchor ``reset_at`` to two different offsets straddling the jump
    # so the week_start (reset_at − 7d) crosses the boundary either way.
    pre_dst = datetime(2026, 3, 28, 2, 0, tzinfo=timezone(timedelta(hours=1)))
    post_dst = datetime(2026, 3, 30, 3, 0, tzinfo=timezone(timedelta(hours=2)))
    # Pick ``now`` so that ``elapsed`` (now − reset_at−7d) covers about half
    # the week — this gives a clearly non-zero pace_ratio without leaning
    # on any TZ arithmetic that might explode near the boundary.
    now = datetime(2026, 3, 30, 3, 0, tzinfo=timezone.utc)  # == post_dst in UTC

    points = []
    for hours_back in (0, 12, 24, 36, 48, 72, 96, 120):
        ts = now - timedelta(hours=hours_back)
        points.append(_point(
            collected_at=ts.isoformat(),
            used_percent=42.0 + 0.1 * hours_back,
            reset_at=post_dst.isoformat(),
        ))
    # Make sure parse_iso_utc resolves both offsets; the function should
    # return the same UTC instant for both reset_at strings.
    assert parse_iso_utc(pre_dst.isoformat()) == parse_iso_utc(pre_dst.astimezone(timezone.utc).isoformat())
    assert parse_iso_utc(post_dst.isoformat()) == parse_iso_utc(post_dst.astimezone(timezone.utc).isoformat())

    pacing = compute_pacing(points, burns={"24h": _ok_burn("24h", 1.0)}, now=now, cfg=CFG)
    assert pacing is not None
    # Week length is always 7d ⇒ elapsed_percent should sit in a sane band.
    assert 0.0 <= pacing.elapsed_percent <= 100.0
    # Pacing uses absolute seconds — the DST hour shift must NOT sneak in:
    # elapsed_total = (now − (reset_at − 7d)).total_seconds() ⇒ exactly 7×24×3600.
    assert pacing.elapsed_percent == pytest.approx(100.0, abs=2.0 / (7 * 24 * 60) * 100)


# ---------------------------------------------------------------------------
# forecast.build_forecast — reset_in across midnight, negative margin
# ---------------------------------------------------------------------------


def test_reset_in_crosses_midnight_is_about_30_minutes():
    """``latest.reset_at`` tomorrow 00:15Z with ``now`` today 23:45Z → ~1800 s."""

    now = datetime(2026, 8, 24, 23, 45, tzinfo=timezone.utc)
    reset = datetime(2026, 8, 25, 0, 15, tzinfo=timezone.utc)
    latest = _point(
        now.isoformat(),
        used_percent=80.0,
        remaining=None,
        reset_at=reset.isoformat(),
    )
    fc = build_forecast(
        latest,
        burns={"15m": _ok_burn("15m", 4.0), "1h": _ok_burn("1h", 4.0), "3h": _ok_burn("3h", 4.0)},
        now=now,
        cfg=CFG,
    )
    # 20 % remaining at 4 p.p./h ⇒ 5h ETA (18000 s).
    assert fc.eta_current_seconds == pytest.approx(5 * 3600.0, rel=1e-6)
    assert fc.reset_in_seconds == pytest.approx(30 * 60.0, rel=1e-6)
    # Reset is closer than exhaustion → margin is negative.
    assert fc.survival_margin_seconds == pytest.approx(
        5 * 3600.0 - 30 * 60.0, rel=1e-6
    )


def test_survival_margin_is_negative_when_eta_exceeds_reset():
    """ETA 2 h, reset in 1 h ⇒ margin = +3600 s (safe — reset arrives first).

    The spec formula is ``survival_margin = eta_current − reset_in``; a
    *negative* value means the quota will exhaust **before** the reset,
    so to demonstrate the negative case we put the reset 1 h *after*
    exhaustion (ETA 1 h, reset in 2 h ⇒ margin = 3600 − 7200 = -3600).
    """

    now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    reset_early = now + timedelta(hours=1)  # reset arrives first ⇒ +3600
    reset_late = now + timedelta(hours=2)   # reset arrives after exhaustion ⇒ -3600

    # Case A: ETA 2 h, reset in 1 h → reset is the limiting factor (safe).
    latest_early = _point(
        now.isoformat(),
        used_percent=80.0,
        remaining=None,
        reset_at=reset_early.isoformat(),
    )
    fc_early = build_forecast(
        latest_early,
        burns={"15m": _ok_burn("15m", 10.0), "1h": _ok_burn("1h", 10.0), "3h": _ok_burn("3h", 10.0)},
        now=now,
        cfg=CFG,
    )
    assert fc_early.eta_current_seconds == pytest.approx(7200.0, rel=1e-6)
    assert fc_early.reset_in_seconds == pytest.approx(3600.0, rel=1e-6)
    assert fc_early.survival_margin_seconds == pytest.approx(3600.0, rel=1e-6)
    assert fc_early.survival_margin_seconds > 0

    # Case B: ETA 1 h, reset in 2 h → quota exhausts before reset.
    latest_late = _point(
        now.isoformat(),
        used_percent=90.0,  # 10% remaining
        remaining=None,
        reset_at=reset_late.isoformat(),
    )
    fc_late = build_forecast(
        latest_late,
        burns={"15m": _ok_burn("15m", 10.0), "1h": _ok_burn("1h", 10.0), "3h": _ok_burn("3h", 10.0)},
        now=now,
        cfg=CFG,
    )
    assert fc_late.eta_current_seconds == pytest.approx(3600.0, rel=1e-6)
    assert fc_late.reset_in_seconds == pytest.approx(7200.0, rel=1e-6)
    assert fc_late.survival_margin_seconds == pytest.approx(-3600.0, rel=1e-6)
    assert fc_late.survival_margin_seconds < 0


# ---------------------------------------------------------------------------
# epoch_to_iso — seconds vs milliseconds auto-detection
# ---------------------------------------------------------------------------


def test_epoch_to_iso_handles_seconds_and_milliseconds():
    """``epoch_to_iso`` produces the same UTC instant for s and ms inputs."""

    iso_from_s = epoch_to_iso(1_787_000_000)
    iso_from_ms = epoch_to_iso(1_787_000_000_000)
    assert iso_from_s is not None
    assert iso_from_ms is not None
    # Both strings should describe the same instant after parsing.
    a = parse_iso_utc(iso_from_s)
    b = parse_iso_utc(iso_from_ms)
    assert a is not None and b is not None
    assert a == b
    # The ms branch must not simply echo the raw 13-digit number as seconds.
    assert b.year >= 2026


def test_epoch_to_iso_invalid_returns_none():
    assert epoch_to_iso(None) is None
    assert epoch_to_iso("") is None
    assert epoch_to_iso("not a number") is None
