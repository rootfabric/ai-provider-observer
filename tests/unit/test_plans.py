"""Plans config loader tests (M3 / §25)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.analytics.plans import (
    PlanInfo,
    PlansConfig,
    ProviderPlans,
    load_plans,
    next_sufficient_plan,
)
from app.analytics import types as t


class _Cfg:
    """Local stand-in for Settings; we do not import app.config to keep
    the test pure."""

    pass


def test_load_missing_file_returns_none_source(tmp_path: Path) -> None:
    pc = load_plans(str(tmp_path / "no-such.yaml"))
    assert pc.source == "none"
    assert pc.providers == {}


def test_load_empty_file_returns_none_source(tmp_path: Path) -> None:
    file = tmp_path / "plans.yaml"
    file.write_text("", encoding="utf-8")
    pc = load_plans(str(file))
    assert pc.source == "none"


def test_load_invalid_yaml_returns_none_source(tmp_path: Path) -> None:
    file = tmp_path / "broken.yaml"
    file.write_text("providers: { [invalid", encoding="utf-8")
    pc = load_plans(str(file))
    assert pc.source == "none"
    assert pc.providers == {}


def test_load_example_yaml_with_nulls(tmp_path: Path) -> None:
    payload = (
        "providers:\n"
        "  zai:\n"
        "    current_plan: lite\n"
        "    plans:\n"
        "      lite: { weekly_capacity: null }\n"
        "      pro:  { weekly_capacity: 50000 }\n"
        "      max:  { weekly_capacity: null }\n"
    )
    file = tmp_path / "plans.yaml"
    file.write_text(payload, encoding="utf-8")
    pc = load_plans(str(file))

    assert pc.source == "configured"
    assert set(pc.providers.keys()) == {"zai"}
    zai = pc.providers["zai"]
    assert zai.current_plan == "lite"
    by_name = {p.name: p.weekly_capacity for p in zai.plans}
    assert by_name["lite"] is None
    assert by_name["pro"] == 50000.0
    assert by_name["max"] is None


def test_load_yaml_with_string_capacity(tmp_path: Path) -> None:
    payload = (
        "providers:\n"
        "  zai:\n"
        "    current_plan: pro\n"
        "    plans:\n"
        "      pro: { weekly_capacity: '12345' }\n"
    )
    file = tmp_path / "plans.yaml"
    file.write_text(payload, encoding="utf-8")
    pc = load_plans(str(file))
    assert pc.providers["zai"].plans[0].weekly_capacity == 12345.0


def test_load_yaml_with_unparseable_capacity(tmp_path: Path) -> None:
    payload = (
        "providers:\n"
        "  zai:\n"
        "    current_plan: pro\n"
        "    plans:\n"
        "      pro: { weekly_capacity: 'not-a-number' }\n"
    )
    file = tmp_path / "plans.yaml"
    file.write_text(payload, encoding="utf-8")
    pc = load_plans(str(file))
    assert pc.source == "configured"
    assert pc.providers["zai"].plans[0].weekly_capacity is None


def test_load_yaml_with_empty_providers(tmp_path: Path) -> None:
    file = tmp_path / "plans.yaml"
    file.write_text("providers: {}\n", encoding="utf-8")
    pc = load_plans(str(file))
    assert pc.source == "none"
    assert pc.providers == {}


def test_next_sufficient_plan_picks_min_above_threshold() -> None:
    pc = PlansConfig(
        source="configured",
        providers={
            "zai": ProviderPlans(
                provider="zai",
                current_plan="lite",
                plans=[
                    PlanInfo(name="lite", weekly_capacity=10000.0),
                    PlanInfo(name="pro", weekly_capacity=50000.0),
                    PlanInfo(name="max", weekly_capacity=200000.0),
                ],
            )
        },
    )
    # required 1.73x -> target = 17300. Only "pro" and "max" qualify.
    chosen = next_sufficient_plan(pc, "zai", 1.73)
    assert chosen == "pro"


def test_next_sufficient_plan_no_provider_returns_none() -> None:
    pc = PlansConfig(source="configured", providers={})
    assert next_sufficient_plan(pc, "zai", 1.5) is None


def test_next_sufficient_plan_source_none_returns_none() -> None:
    pc = PlansConfig(source="none", providers={})
    assert next_sufficient_plan(pc, "zai", 1.5) is None


def test_next_sufficient_plan_unknown_capacity_returns_none() -> None:
    pc = PlansConfig(
        source="configured",
        providers={
            "zai": ProviderPlans(
                provider="zai",
                current_plan="lite",
                plans=[
                    PlanInfo(name="lite", weekly_capacity=None),
                    PlanInfo(name="pro", weekly_capacity=50000.0),
                ],
            )
        },
    )
    assert next_sufficient_plan(pc, "zai", 1.5) is None


def test_next_sufficient_plan_no_qualified_returns_none() -> None:
    pc = PlansConfig(
        source="configured",
        providers={
            "zai": ProviderPlans(
                provider="zai",
                current_plan="lite",
                plans=[
                    PlanInfo(name="lite", weekly_capacity=10000.0),
                    PlanInfo(name="pro", weekly_capacity=12000.0),
                ],
            )
        },
    )
    assert next_sufficient_plan(pc, "zai", 2.0) is None


def test_next_sufficient_plan_missing_current_plan() -> None:
    pc = PlansConfig(
        source="configured",
        providers={
            "zai": ProviderPlans(
                provider="zai",
                current_plan="",
                plans=[PlanInfo(name="pro", weekly_capacity=50000.0)],
            )
        },
    )
    assert next_sufficient_plan(pc, "zai", 1.5) is None


def test_plans_config_dataclass_round_trip() -> None:
    # Round-trip through asdict equivalent to keep the dataclass ergonomic.
    pc = PlansConfig(
        source="configured",
        providers={
            "zai": ProviderPlans(
                provider="zai",
                current_plan="lite",
                plans=[PlanInfo(name="lite", weekly_capacity=None)],
            )
        },
    )
    assert pc.providers["zai"].provider == "zai"
    assert pc.providers["zai"].current_plan == "lite"
    assert pc.providers["zai"].plans[0].name == "lite"
    assert pc.providers["zai"].plans[0].weekly_capacity is None
