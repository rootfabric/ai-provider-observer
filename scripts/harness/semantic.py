from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from strictjson import load

RISK_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


def _strings(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            out.extend(_strings(v))
    elif isinstance(value, list):
        for v in value:
            out.extend(_strings(v))
    return out


def semantic_text(work_order: dict[str, Any], contract: dict[str, Any], specification_text: str = "") -> str:
    # Deliberately excludes risk/risk_reasons so an actor cannot suppress or bias
    # inference by describing the work as low risk.
    source = {
        "specification": specification_text,
        "mission": work_order.get("mission"),
        "stop_conditions": work_order.get("stop_conditions"),
        "required_evidence": work_order.get("required_evidence"),
        "contract": contract,
    }
    return "\n".join(_strings(source)).casefold()


def _trigger_matches(text: str, trigger: str) -> bool:
    t = trigger.casefold().strip()
    if not t:
        return False
    # ASCII single-word triggers are treated as word-prefixes so `persist` matches
    # `persistent`, while `race` does not accidentally match `traceability`.
    if t.isascii() and re.fullmatch(r"[a-z0-9_]+", t):
        return re.search(r"(?<![a-z0-9_])" + re.escape(t) + r"[a-z0-9_]*(?![a-z0-9_])", text) is not None
    # Phrases and non-ASCII stems intentionally retain deterministic substring
    # behavior (many Russian triggers are configured as morphological stems).
    return t in text


def infer_semantic_tags(policy: dict[str, Any], work_order: dict[str, Any], contract: dict[str, Any], specification_text: str = "") -> set[str]:
    text = semantic_text(work_order, contract, specification_text)
    inferred: set[str] = set()
    for tag, cfg in (policy.get("tags") or {}).items():
        if not isinstance(cfg, dict):
            continue
        for trigger in cfg.get("triggers", []):
            if isinstance(trigger, str) and _trigger_matches(text, trigger):
                inferred.add(tag)
                break
    return inferred


def risk_floor(policy: dict[str, Any], tags: set[str]) -> str:
    floor = "LOW"
    for tag in tags:
        cfg = (policy.get("tags") or {}).get(tag, {})
        candidate = cfg.get("min_risk", "LOW") if isinstance(cfg, dict) else "LOW"
        if candidate in RISK_ORDER and RISK_ORDER[candidate] > RISK_ORDER[floor]:
            floor = candidate
    return floor


def required_partitions(policy: dict[str, Any], tag: str) -> set[str]:
    cfg = (policy.get("tags") or {}).get(tag, {})
    if not isinstance(cfg, dict):
        return set()
    return {x for x in cfg.get("required_partitions", []) if isinstance(x, str) and x}


def validate_contract_shape(contract: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in schema.get("required", []):
        if key not in contract:
            errors.append(f"ACCEPTANCE_CONTRACT_FIELD_MISSING:{key}")
    predicates = contract.get("predicates")
    if not isinstance(predicates, list) or not predicates:
        errors.append("ACCEPTANCE_PREDICATES_MISSING")
        return errors
    required = set(schema.get("predicate_required", []))
    allowed_classes = set(schema.get("classes", []))
    allowed_evidence = set(schema.get("required_evidence_modes", []))
    seen: set[str] = set()
    for idx, predicate in enumerate(predicates):
        if not isinstance(predicate, dict):
            errors.append(f"ACCEPTANCE_PREDICATE_INVALID:{idx}")
            continue
        missing = required - set(predicate)
        if missing:
            errors.append(f"ACCEPTANCE_PREDICATE_FIELD_MISSING:{idx}:{','.join(sorted(missing))}")
        pid = predicate.get("predicate_id")
        if not isinstance(pid, str) or not pid:
            errors.append(f"ACCEPTANCE_PREDICATE_ID_INVALID:{idx}")
        elif pid in seen:
            errors.append(f"ACCEPTANCE_PREDICATE_ID_DUPLICATE:{pid}")
        else:
            seen.add(pid)
        if predicate.get("class") not in allowed_classes:
            errors.append(f"ACCEPTANCE_PREDICATE_CLASS_INVALID:{pid}:{predicate.get('class')}")
        parts = predicate.get("partitions")
        if not isinstance(parts, list) or not all(isinstance(x, str) and x for x in parts):
            errors.append(f"ACCEPTANCE_PARTITIONS_INVALID:{pid}")
        modes = predicate.get("required_evidence")
        if not isinstance(modes, list) or not modes:
            errors.append(f"ACCEPTANCE_EVIDENCE_MODE_MISSING:{pid}")
        elif any(x not in allowed_evidence for x in modes):
            errors.append(f"ACCEPTANCE_EVIDENCE_MODE_INVALID:{pid}")
    return errors


def semantic_contract_errors(
    policy: dict[str, Any], schema: dict[str, Any], work_order: dict[str, Any], contract: dict[str, Any], specification_text: str = ""
) -> tuple[list[str], set[str], str]:
    errors = validate_contract_shape(contract, schema)
    inferred = infer_semantic_tags(policy, work_order, contract, specification_text)
    declared = {x for x in contract.get("semantic_tags", []) if isinstance(x, str)} if isinstance(contract.get("semantic_tags"), list) else set()
    missing_tags = inferred - declared
    for tag in sorted(missing_tags):
        errors.append(f"SEMANTIC_PROFILE_INCOMPLETE:{tag}")

    predicates = [p for p in contract.get("predicates", []) if isinstance(p, dict)] if isinstance(contract.get("predicates"), list) else []
    universal = set(policy.get("universal_predicate_classes", []))
    universal_min = int(policy.get("universal_min_partitions", 2))
    for p in predicates:
        pid = p.get("predicate_id", "<unknown>")
        parts = {x for x in p.get("partitions", []) if isinstance(x, str)} if isinstance(p.get("partitions"), list) else set()
        if p.get("class") in universal and len(parts) < universal_min:
            errors.append(f"UNIVERSAL_PREDICATE_UNDERPARTITIONED:{pid}:{len(parts)}<{universal_min}")

    all_tags = inferred | declared
    for tag in sorted(all_tags):
        covered_predicates = [p for p in predicates if tag in set(p.get("semantic_tags", []))]
        if not covered_predicates:
            errors.append(f"SEMANTIC_TAG_UNCOVERED:{tag}")
            continue
        covered_parts: set[str] = set()
        for p in covered_predicates:
            covered_parts.update(x for x in p.get("partitions", []) if isinstance(x, str))
        for required in sorted(required_partitions(policy, tag) - covered_parts):
            errors.append(f"ACCEPTANCE_PARTITION_MISSING:{tag}:{required}")

    floor = risk_floor(policy, all_tags)
    if RISK_ORDER[floor] >= RISK_ORDER["MEDIUM"]:
        for p in predicates:
            pid = p.get("predicate_id", "<unknown>")
            if p.get("class") != "EXAMPLE" and p.get("verifier_owned") is not True:
                errors.append(f"MEDIUM_PLUS_PREDICATE_REQUIRES_VERIFIER_OWNERSHIP:{pid}")
    return errors, all_tags, floor


def coverage_errors(
    contract: dict[str, Any], manifest: dict[str, Any], evidence_map: dict[str, Any], *, medium_plus: bool
) -> list[str]:
    errors: list[str] = []
    predicates = {p.get("predicate_id"): p for p in contract.get("predicates", []) if isinstance(p, dict) and isinstance(p.get("predicate_id"), str)}
    cases = {c.get("case_id"): c for c in manifest.get("cases", []) if isinstance(c, dict) and isinstance(c.get("case_id"), str)} if isinstance(manifest.get("cases"), list) else {}
    coverage_entries = manifest.get("predicate_coverage")
    if not isinstance(coverage_entries, list):
        return ["VERIFIER_PREDICATE_COVERAGE_MISSING"]
    coverage_by_pid: dict[str, dict[str, Any]] = {}
    for entry in coverage_entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("predicate_id"), str):
            errors.append("VERIFIER_COVERAGE_ENTRY_INVALID")
            continue
        coverage_by_pid[entry["predicate_id"]] = entry

    candidate_receipts = set(evidence_map.get("candidate_receipts", evidence_map.get("receipts", [])) or [])
    verifier_receipts = set(evidence_map.get("verifier_receipts", []) or [])
    allowed_receipts = candidate_receipts | verifier_receipts
    adversarial_count = 0

    for pid, predicate in predicates.items():
        entry = coverage_by_pid.get(pid)
        if not entry:
            errors.append(f"PREDICATE_EVIDENCE_MISSING:{pid}")
            continue
        case_ids = entry.get("case_ids")
        receipts = entry.get("receipt_refs")
        covered_parts = set(entry.get("covered_partitions", [])) if isinstance(entry.get("covered_partitions"), list) else set()
        if not isinstance(case_ids, list) or not case_ids:
            errors.append(f"PREDICATE_CASES_MISSING:{pid}")
            continue
        if not isinstance(receipts, list) or not receipts:
            errors.append(f"PREDICATE_RECEIPTS_MISSING:{pid}")
        for ref in receipts or []:
            if ref not in allowed_receipts:
                errors.append(f"PREDICATE_RECEIPT_NOT_IN_EVIDENCE_MAP:{pid}:{ref}")
        required_parts = set(predicate.get("partitions", [])) if isinstance(predicate.get("partitions"), list) else set()
        missing_parts = required_parts - covered_parts
        for part in sorted(missing_parts):
            errors.append(f"PREDICATE_PARTITION_UNPROVEN:{pid}:{part}")
        for cid in case_ids:
            case = cases.get(cid)
            if not case:
                errors.append(f"VERIFIER_CASE_MISSING:{pid}:{cid}")
                continue
            kind = case.get("kind")
            if kind in {"ADVERSARIAL", "FAULT_INJECTION", "PROPERTY"}:
                adversarial_count += 1
            case_parts = set(case.get("partitions", [])) if isinstance(case.get("partitions"), list) else set()
            if not case_parts & required_parts and required_parts:
                errors.append(f"VERIFIER_CASE_NOT_MAPPED_TO_PARTITION:{pid}:{cid}")
        if predicate.get("verifier_owned") is True:
            verifier_case_ids = [cid for cid in case_ids if cases.get(cid, {}).get("owner") == "VERIFIER"]
            if not verifier_case_ids:
                errors.append(f"VERIFIER_OWNED_EVIDENCE_MISSING:{pid}")
            if not any(ref in verifier_receipts for ref in (receipts or [])):
                errors.append(f"VERIFIER_OWNED_RECEIPT_MISSING:{pid}")

    if medium_plus and adversarial_count < 1:
        errors.append("MEDIUM_PLUS_ADVERSARIAL_VERIFICATION_MISSING")
    return errors
