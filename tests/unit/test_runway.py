"""Balance runway tests (spec §4.6, §16-§17)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.analytics import types as t
from app.analytics.runway import compute_runway


CFG = SimpleNamespace(
    burn_min_points=3,
    burn_min_span_minutes=5.0,
    reset_drop_min_pp=5.0,
    reset_jitter_pp=2.0,
    accel_baseline_min=1.0,
    week_min_elapsed_pct=3.0,
)


def _balance(at, balance, currency="USD"):
    return t.QuotaPoint(
        collected_at=at.isoformat(),
        used=None,
        remaining=balance,
        limit_value=None,
        used_percent=None,
        unit=currency,
        reset_at=None,
        reset_estimated=False,
    )


def test_linear_spend_runway_and_monthly():
    base = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    # Balance goes 42.3 → 35.5 linearly over 24h (hourly points, 25 total).
    points = []
    start_balance = 42.3
    end_balance = 35.5
    span_hours = 24
    n = span_hours + 1
    delta = (end_balance - start_balance) / span_hours
    for hour in range(n):
        ts = base + timedelta(hours=hour)
        bal = start_balance + delta * hour
        points.append(_balance(ts, bal))
    now = base + timedelta(hours=24)
    runway = compute_runway(points, currency="USD", now=now, cfg=CFG)
    assert runway.status == t.STATUS_OK
    assert runway.balance_total == pytest.approx(35.5, rel=1e-9)
    # Spend per day ~ 6.8 USD (42.3 − 35.5).
    assert runway.usd_per_day is not None
    assert runway.usd_per_day == pytest.approx(6.8, rel=1e-3)
    expected_runway = runway.balance_total / runway.usd_per_day
    assert runway.runway_days == pytest.approx(expected_runway, rel=1e-9)
    assert runway.projected_monthly_spend == pytest.approx(
        runway.usd_per_day * 30.4375, rel=1e-9
    )


def test_stable_balance_returns_no_runway():
    base = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    points = [_balance(base + timedelta(hours=h), 100.0 + h * 0.1)
              for h in range(25)]  # balance grows
    runway = compute_runway(points, currency="USD", now=base + timedelta(hours=24), cfg=CFG)
    assert runway.status == t.STATUS_OK
    assert runway.runway_days is None
    assert runway.projected_monthly_spend is None


def test_insufficient_data_when_too_few_points():
    base = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    points = [_balance(base, 50.0), _balance(base + timedelta(hours=1), 49.0)]
    runway = compute_runway(points, currency="USD", now=base + timedelta(hours=1), cfg=CFG)
    assert runway.status == t.STATUS_INSUFFICIENT_DATA
    assert runway.usd_per_day is None
    assert runway.runway_days is None
    assert runway.balance_total == pytest.approx(49.0, rel=1e-9)


def test_confidence_low_when_short_span():
    base = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    # 10 minutes span, three points.
    points = [
        _balance(base, 100.0),
        _balance(base + timedelta(minutes=5), 99.5),
        _balance(base + timedelta(minutes=10), 99.0),
    ]
    runway = compute_runway(points, currency="USD", now=base + timedelta(minutes=10), cfg=CFG)
    assert runway.status == t.STATUS_OK
    assert runway.confidence == t.CONFIDENCE_LOW


def test_confidence_high_when_long_span():
    base = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    points = [_balance(base + timedelta(hours=h), 100.0 - h * 0.1)
              for h in range(26)]  # ~26h span
    runway = compute_runway(points, currency="USD",
                            now=base + timedelta(hours=25), cfg=CFG)
    assert runway.confidence == t.CONFIDENCE_HIGH


def test_currency_propagates():
    base = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    points = [_balance(base + timedelta(hours=h), 50.0 - h * 0.5)
              for h in range(25)]
    runway = compute_runway(points, currency="EUR",
                            now=base + timedelta(hours=24), cfg=CFG)
    assert runway.currency == "EUR"
    assert runway.status == t.STATUS_OK