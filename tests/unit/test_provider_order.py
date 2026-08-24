"""Providers surface in the canonical display order (zai, minimax, deepseek, codex, openrouter)."""
from __future__ import annotations

from app.engine import PROVIDER_DISPLAY_ORDER, AnalyticsEngine, _display_rank
from app.store import Store

from tests.fakes import make_snapshot


def _engine(tmp_path):
    store = Store(str(tmp_path / "order.db"))
    for provider in ["openrouter", "codex", "deepseek", "minimax", "zai"]:
        store.save(make_snapshot(provider, provider.upper(), status="ok"))
    settings = type("S", (), {"history_lookback_hours": 200})()
    return AnalyticsEngine(store, settings)


def test_display_rank_canonical_then_unknown():
    assert _display_rank("zai") < _display_rank("minimax") < _display_rank("deepseek") < _display_rank("codex") < _display_rank("openrouter")
    assert _display_rank("newguy") > _display_rank("openrouter")
    assert _display_rank("aaa")[0] == _display_rank("zzz")[0]  # unknowns share rank; name breaks ties


def test_collect_providers_ordered(tmp_path):
    engine = _engine(tmp_path)
    ordered = [p["provider"] for p in engine._collect_providers("1970-01-01T00:00:00+00:00")]
    assert ordered == list(PROVIDER_DISPLAY_ORDER)


def test_display_order_tuple_matches_request():
    assert PROVIDER_DISPLAY_ORDER == ("zai", "minimax", "deepseek", "codex", "openrouter")
