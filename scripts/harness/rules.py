from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

CONFIG = Path("config/control/harness")
REGISTRY = CONFIG / "rule-registry.v1.json"
LIFECYCLE_POLICY = CONFIG / "rule-lifecycle-policy.v1.json"
REVIEW_STATE = CONFIG / "rule-review-state.v1.json"


class RuleLifecycleError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuleHealth:
    rule_id: str
    rule_class: str
    status: str
    severity: str
    last_reviewed: str | None
    review_due: str | None
    days_since_review: int | None
    review_every_days: int
    missing_enforcement_targets: tuple[str, ...]
    missing_test_targets: tuple[str, ...]
    message: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["missing_enforcement_targets"] = list(self.missing_enforcement_targets)
        value["missing_test_targets"] = list(self.missing_test_targets)
        return value


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuleLifecycleError(f"RULE_CONFIG_MISSING:{path}") from exc
    except json.JSONDecodeError as exc:
        raise RuleLifecycleError(f"RULE_CONFIG_INVALID:{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise RuleLifecycleError(f"RULE_CONFIG_OBJECT_REQUIRED:{path}")
    return value


def load_registry(root: Path) -> dict[str, Any]:
    return _load_object(root / REGISTRY)


def load_lifecycle_policy(root: Path) -> dict[str, Any]:
    value = _load_object(root / LIFECYCLE_POLICY)
    if value.get("schema") != "hybrid_harness.rule_lifecycle_policy.v1":
        raise RuleLifecycleError("RULE_LIFECYCLE_POLICY_SCHEMA_INVALID")
    return value


def load_review_state(root: Path) -> dict[str, Any]:
    value = _load_object(root / REVIEW_STATE)
    if value.get("schema") != "hybrid_harness.rule_review_state.v1":
        raise RuleLifecycleError("RULE_REVIEW_STATE_SCHEMA_INVALID")
    return value


def _refs(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [x for x in value if isinstance(x, str) and x]
    return []


def _resolve_reference(root: Path, ref: str) -> Path | None:
    path_text = ref.split("::", 1)[0]
    p = Path(path_text)
    candidates: list[Path]
    if "/" in path_text or "\\" in path_text:
        candidates = [root / p]
    else:
        candidates = [
            root / p,
            root / CONFIG / p,
            root / "scripts/harness" / p,
            root / "tests" / p,
        ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _parse_day(raw: Any) -> date | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def _severity_for_status(rule_class: str, status: str, policy: dict[str, Any]) -> str:
    if status in {"BROKEN", "ORPHANED"}:
        return "ERROR"
    if status == "STALE":
        stale_errors = set(policy.get("stale_is_error_for_classes", []))
        return "ERROR" if rule_class in stale_errors else "WARN"
    if status in {"DRAFT", "DEPRECATED", "SUPERSEDED"}:
        return "INFO"
    return "OK"


def _health_for_rule(root: Path, rule: dict[str, Any], review: dict[str, Any] | None,
                     policy: dict[str, Any], today: date) -> RuleHealth:
    rid = str(rule.get("id") or "<missing>")
    rule_class = str(rule.get("class") or "<missing>")
    class_days = policy.get("review_every_days_by_class", {})
    cadence = rule.get("review_every_days", class_days.get(rule_class, policy.get("default_review_every_days", 90)))
    try:
        cadence = int(cadence)
    except (TypeError, ValueError):
        cadence = 90

    missing_enforced = tuple(ref for ref in _refs(rule.get("enforced_by")) if _resolve_reference(root, ref) is None)
    tests = _refs(rule.get("tests"))
    missing_tests = tuple(ref for ref in tests if _resolve_reference(root, ref) is None)

    lifecycle_required = rule.get("lifecycle_version") == 1
    broken_reasons: list[str] = []
    if not isinstance(review, dict):
        broken_reasons.append("review state missing")
    if cadence <= 0:
        broken_reasons.append("review cadence invalid")
    if lifecycle_required:
        if not rule.get("owner"):
            broken_reasons.append("owner missing")
        if rule.get("enforcement") in {"machine", "mixed"} and not tests:
            broken_reasons.append("machine rule test link missing")

    reviewed_day = _parse_day(review.get("last_reviewed") if isinstance(review, dict) else None)
    if isinstance(review, dict) and review.get("last_reviewed") and reviewed_day is None:
        broken_reasons.append("last_reviewed invalid")
    if isinstance(review, dict) and not str(review.get("reviewed_by") or "").strip():
        broken_reasons.append("reviewed_by missing")
    if reviewed_day is not None and reviewed_day > today:
        broken_reasons.append("last_reviewed is in the future")

    declared_status = str(rule.get("status") or "ACTIVE").upper()
    allowed = set(policy.get("allowed_rule_statuses", ["ACTIVE", "DRAFT", "DEPRECATED", "SUPERSEDED"]))
    if declared_status not in allowed:
        broken_reasons.append(f"status invalid:{declared_status}")

    due: date | None = reviewed_day + timedelta(days=cadence) if reviewed_day and cadence > 0 else None
    days_since = (today - reviewed_day).days if reviewed_day else None

    if broken_reasons:
        status = "BROKEN"
        message = "; ".join(broken_reasons)
    elif missing_enforced or missing_tests:
        status = "ORPHANED"
        chunks = []
        if missing_enforced:
            chunks.append("missing enforcement: " + ", ".join(missing_enforced))
        if missing_tests:
            chunks.append("missing tests: " + ", ".join(missing_tests))
        message = "; ".join(chunks)
    elif declared_status != "ACTIVE":
        status = declared_status
        message = f"declared {declared_status.lower()}"
    elif reviewed_day is None:
        status = "BROKEN"
        message = "last review missing"
    elif due and today > due:
        status = "STALE"
        message = f"review overdue by {(today - due).days} days"
    else:
        status = "ACTIVE"
        message = "healthy"

    return RuleHealth(
        rule_id=rid,
        rule_class=rule_class,
        status=status,
        severity=_severity_for_status(rule_class, status, policy),
        last_reviewed=reviewed_day.isoformat() if reviewed_day else None,
        review_due=due.isoformat() if due else None,
        days_since_review=days_since,
        review_every_days=cadence,
        missing_enforcement_targets=missing_enforced,
        missing_test_targets=missing_tests,
        message=message,
    )


def health_report(root: Path, *, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    registry = load_registry(root)
    policy = load_lifecycle_policy(root)
    state = load_review_state(root)
    rules = registry.get("rules", [])
    if not isinstance(rules, list):
        raise RuleLifecycleError("RULE_REGISTRY_RULES_ARRAY_REQUIRED")
    reviews = state.get("reviews", {})
    if not isinstance(reviews, dict):
        raise RuleLifecycleError("RULE_REVIEW_STATE_REVIEWS_OBJECT_REQUIRED")

    rows = [
        _health_for_rule(
            root,
            r if isinstance(r, dict) else {},
            reviews.get((r or {}).get("id")) if isinstance(r, dict) else None,
            policy,
            today,
        )
        for r in rules
    ]
    known = {r.rule_id for r in rows}
    orphan_review_ids = sorted(str(x) for x in reviews if x not in known)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    return {
        "schema": "hybrid_harness.rule_health_report.v1",
        "generated_for_date": today.isoformat(),
        "rule_count": len(rows),
        "counts": counts,
        "orphan_review_state_ids": orphan_review_ids,
        "rules": [r.to_dict() for r in rows],
    }


def lifecycle_issues(root: Path, *, today: date | None = None) -> list[dict[str, str]]:
    try:
        report = health_report(root, today=today)
    except RuleLifecycleError as exc:
        return [{"code": "RULE_HEALTH_CONFIG_INVALID", "severity": "ERROR", "message": str(exc)}]
    issues: list[dict[str, str]] = []
    code_by_status = {
        "BROKEN": "RULE_BROKEN",
        "ORPHANED": "RULE_ORPHANED",
        "STALE": "RULE_STALE",
    }
    for row in report["rules"]:
        status = row["status"]
        if status not in code_by_status:
            continue
        issues.append({
            "code": code_by_status[status],
            "severity": row["severity"],
            "message": f"{row['rule_id']}: {row['message']}",
        })
    for rid in report["orphan_review_state_ids"]:
        issues.append({"code": "RULE_REVIEW_STATE_ORPHANED", "severity": "WARN", "message": rid})
    return issues


def review_rule(root: Path, rule_id: str, *, reviewed_on: str | None = None,
                reviewed_by: str = "DIRECTOR", note: str = "") -> Path:
    registry = load_registry(root)
    ids = {r.get("id") for r in registry.get("rules", []) if isinstance(r, dict)}
    if rule_id not in ids:
        raise RuleLifecycleError(f"RULE_NOT_FOUND:{rule_id}")
    day = _parse_day(reviewed_on) if reviewed_on else date.today()
    if day is None:
        raise RuleLifecycleError("RULE_REVIEW_DATE_INVALID")
    if day > date.today():
        raise RuleLifecycleError("RULE_REVIEW_DATE_IN_FUTURE")
    if not str(reviewed_by or "").strip():
        raise RuleLifecycleError("RULE_REVIEW_ACTOR_REQUIRED")
    state = load_review_state(root)
    reviews = state.setdefault("reviews", {})
    reviews[rule_id] = {
        "last_reviewed": day.isoformat(),
        "reviewed_by": reviewed_by,
        "note": note,
    }
    path = root / REVIEW_STATE
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def add_rule(root: Path, rule: dict[str, Any], *, reviewed_by: str = "DIRECTOR",
             note: str = "initial rule activation") -> tuple[Path, Path]:
    registry = load_registry(root)
    policy = load_lifecycle_policy(root)
    rules = registry.get("rules", [])
    if not isinstance(rules, list):
        raise RuleLifecycleError("RULE_REGISTRY_RULES_ARRAY_REQUIRED")
    rid = rule.get("id")
    if not isinstance(rid, str) or not rid:
        raise RuleLifecycleError("RULE_ID_REQUIRED")
    if any(isinstance(r, dict) and r.get("id") == rid for r in rules):
        raise RuleLifecycleError(f"RULE_ID_DUPLICATE:{rid}")
    allowed_classes = set(policy.get("review_every_days_by_class", {}))
    if rule.get("class") not in allowed_classes:
        raise RuleLifecycleError(f"RULE_CLASS_INVALID:{rule.get('class')}")
    required = {
        "id", "class", "source", "applies_when", "enforcement",
        "enforced_by", "retirement", "prose_mode", "owner", "tests",
    }
    missing = sorted(required - set(rule))
    if missing:
        raise RuleLifecycleError("RULE_ADD_FIELDS_MISSING:" + ",".join(missing))
    for field in ("source", "applies_when", "enforcement", "retirement", "prose_mode", "owner"):
        if not str(rule.get(field) or "").strip():
            raise RuleLifecycleError(f"RULE_FIELD_EMPTY:{field}")
    if rule.get("enforcement") not in {"machine", "mixed", "prose"}:
        raise RuleLifecycleError(f"RULE_ENFORCEMENT_INVALID:{rule.get('enforcement')}")
    if rule.get("enforcement") in {"machine", "mixed"} and not _refs(rule.get("tests")):
        raise RuleLifecycleError("RULE_TEST_LINK_REQUIRED")
    if "review_every_days" in rule:
        try:
            cadence = int(rule["review_every_days"])
        except (TypeError, ValueError) as exc:
            raise RuleLifecycleError("RULE_REVIEW_CADENCE_INVALID") from exc
        if cadence <= 0:
            raise RuleLifecycleError("RULE_REVIEW_CADENCE_INVALID")
    missing_targets = [
        ref
        for ref in _refs(rule.get("enforced_by")) + _refs(rule.get("tests"))
        if _resolve_reference(root, ref) is None
    ]
    if missing_targets:
        raise RuleLifecycleError("RULE_TARGET_MISSING:" + ",".join(missing_targets))
    rule = dict(rule)
    rule.setdefault("status", "ACTIVE")
    rule["lifecycle_version"] = 1
    rule.setdefault("introduced_revision", "R13.2")
    rules.append(rule)
    registry_path = root / REGISTRY
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    state = load_review_state(root)
    state.setdefault("reviews", {})[rid] = {
        "last_reviewed": date.today().isoformat(),
        "reviewed_by": reviewed_by,
        "note": note,
    }
    state_path = root / REVIEW_STATE
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return registry_path, state_path
