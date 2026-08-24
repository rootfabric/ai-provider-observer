"""Tariff plans config loader for M3 (§12-§13, §25).

Reads `config/plans.yaml` and exposes provider/plan capacity lookups.
Numbers are NEVER invented: every missing capacity becomes None and
the loader reports `source='none'` when the file is absent or broken.

Why this is here: the recommendation engine (`recommendation.py`) needs
the smallest sufficient plan when projected weekly usage exceeds the
current plan. Without configured capacities we cannot name a plan, so
the engine recommends INCREASE_BUDGET instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(slots=True)
class PlanInfo:
    """One plan's known weekly capacity.

    weekly_capacity is `None` when the operator left it blank — the
    loader must propagate that exactly to avoid invented numbers.
    """

    name: str
    weekly_capacity: float | None = None


@dataclass(slots=True)
class ProviderPlans:
    """Plans of a single provider plus the currently active plan name."""

    provider: str
    current_plan: str | None = None
    plans: list[PlanInfo] = field(default_factory=list)


@dataclass(slots=True)
class PlansConfig:
    """Container with all known provider plans and a load-source flag."""

    source: str                       # 'configured'|'none'
    providers: dict[str, ProviderPlans] = field(default_factory=dict)


def _coerce_capacity(value: Any) -> float | None:
    """Coerce YAML scalar into float|None; preserve None for missing/null."""
    if value is None:
        return None
    if isinstance(value, bool):
        # YAML treats `yes`/`no` as bools; never silently turn that into 1/0.
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if text == "" or text.lower() in {"null", "~"}:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def load_plans(path: str) -> PlansConfig:
    """Load tariffs from `path`.

    Missing file, parse error or empty payload -> `PlansConfig(source='none')`.
    Otherwise `source='configured'`. Numeric `weekly_capacity` values
    are coerced to `float`; everything else (missing, null, empty,
    unparseable) stays `None`.
    """
    file_path = Path(path)
    if not file_path.exists():
        return PlansConfig(source="none", providers={})

    try:
        raw = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return PlansConfig(source="none", providers={})

    if not isinstance(raw, dict):
        return PlansConfig(source="none", providers={})

    raw_providers = raw.get("providers")
    if not isinstance(raw_providers, dict) or not raw_providers:
        return PlansConfig(source="none", providers={})

    providers: dict[str, ProviderPlans] = {}
    for provider_name, provider_payload in raw_providers.items():
        if not isinstance(provider_payload, dict):
            continue

        current_plan_raw = provider_payload.get("current_plan")
        current_plan: str | None
        if isinstance(current_plan_raw, str) and current_plan_raw.strip():
            current_plan = current_plan_raw.strip()
        else:
            current_plan = None

        plans_raw = provider_payload.get("plans")
        plans: list[PlanInfo] = []
        if isinstance(plans_raw, dict):
            for plan_name, plan_payload in plans_raw.items():
                capacity: float | None = None
                if isinstance(plan_payload, dict):
                    capacity = _coerce_capacity(plan_payload.get("weekly_capacity"))
                plans.append(PlanInfo(name=str(plan_name), weekly_capacity=capacity))

        providers[str(provider_name)] = ProviderPlans(
            provider=str(provider_name),
            current_plan=current_plan,
            plans=plans,
        )

    if not providers:
        return PlansConfig(source="none", providers={})
    return PlansConfig(source="configured", providers=providers)


def next_sufficient_plan(
    pc: PlansConfig,
    provider: str,
    required_capacity_ratio: float,
) -> str | None:
    """Pick the smallest named plan whose weekly_capacity meets the target.

    Required ratio is `new_weekly_capacity / current_weekly_capacity`.
    When the source isn't `configured`, or the current plan has no
    capacity, no plan is named (caller falls back to INCREASE_BUDGET).
    """
    if pc.source != "configured":
        return None

    provider_plans = pc.providers.get(provider)
    if provider_plans is None:
        return None

    current_plan_name = provider_plans.current_plan
    if not current_plan_name:
        return None

    current_capacity: float | None = None
    for plan in provider_plans.plans:
        if plan.name == current_plan_name:
            current_capacity = plan.weekly_capacity
            break

    if current_capacity is None or current_capacity <= 0:
        return None

    target = required_capacity_ratio * current_capacity

    candidates: list[tuple[float, str]] = []
    for plan in provider_plans.plans:
        if plan.weekly_capacity is None:
            continue
        if plan.weekly_capacity >= target:
            candidates.append((plan.weekly_capacity, plan.name))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][1]
