"""Confidence band resolution tests (spec §27)."""
from __future__ import annotations

from app.analytics import types as t
from app.analytics.confidence import confidence_from_span


def test_none_is_low():
    assert confidence_from_span(None) == t.CONFIDENCE_LOW


def test_below_15_minutes_is_low():
    assert confidence_from_span(0.0) == t.CONFIDENCE_LOW
    assert confidence_from_span(14.9) == t.CONFIDENCE_LOW


def test_between_low_and_threshold_is_medium():
    assert confidence_from_span(15.0) == t.CONFIDENCE_MEDIUM
    assert confidence_from_span(60.0) == t.CONFIDENCE_MEDIUM
    assert confidence_from_span(119.9) == t.CONFIDENCE_MEDIUM


def test_above_threshold_is_high():
    assert confidence_from_span(120.0) == t.CONFIDENCE_HIGH
    assert confidence_from_span(360.0) == t.CONFIDENCE_HIGH


def test_custom_threshold():
    # With a lower threshold, the same span becomes HIGH.
    assert confidence_from_span(45.0, high_threshold_minutes=30.0) == t.CONFIDENCE_HIGH


def test_non_numeric_is_low():
    assert confidence_from_span("not a number") == t.CONFIDENCE_LOW