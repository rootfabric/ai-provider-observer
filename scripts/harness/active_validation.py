from __future__ import annotations

import re
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from strictjson import StrictJSONError, load, loads
from gitproof import (
    GitProofError,
    branch as git_branch,
    changed_paths,
    commit_adding_path,
    commit_author_email,
    commit_changed_paths,
    commit_exists,
    commit_parent,
    commit_timestamp,
    commits_between,
    file_at_commit,
    head as git_head,
    is_ancestor,
    is_repo,
    path_immutable_since_add,
    path_matches,
    replace_refs,
    trajectory_violations,
    blob_oid_at_commit,
    path_matches_head,
    suspicious_reflog_actions,
    suspicious_reflog_actions_after_ref,
    worktree_changed_paths,
    bytes_at_commit,
)
from evidence import validate_event_chain, validate_receipt
from trust import verify_attestation, evidence_digest_at_commit
from semantic import RISK_ORDER as SEMANTIC_RISK_ORDER, semantic_contract_errors, coverage_errors
from requirements import validate_requirements_manifest, requirement_coverage_errors

SHA40 = re.compile(r"^[0-9a-f]{40}$")
RISK_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
DISPATCH_ALLOWED = ("config/control/missions/**", "config/control/acceptance/**", "config/control/requirements/**", "config/control/project-state.v1.json")


@dataclass(frozen=True)
class ActiveFinding:
    code: str
    message: str


def _f(code: str, msg: str) -> ActiveFinding:
    return ActiveFinding(code, msg)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _parse_time(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return None
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _temporal_claim_findings(issued_raw: Any, prerequisite_raw: Any, consumer_raw: Any, max_clock_skew_seconds: int) -> list[ActiveFinding]:
    findings: list[ActiveFinding] = []
    issued = _parse_time(issued_raw) if isinstance(issued_raw, str) else None
    prereq_time = _parse_time(prerequisite_raw) if isinstance(prerequisite_raw, str) else None
    consumer_time = _parse_time(consumer_raw) if isinstance(consumer_raw, str) else None
    if issued is None:
        findings.append(_f("ATTESTATION_ISSUED_AT_INVALID", repr(issued_raw)))
    if prereq_time is None:
        findings.append(_f("ATTESTATION_PREREQUISITE_TIME_INVALID", repr(prerequisite_raw)))
    if consumer_time is None:
        findings.append(_f("ATTESTATION_CONSUMER_TIME_INVALID", repr(consumer_raw)))
    skew = timedelta(seconds=max(0, int(max_clock_skew_seconds)))
    if issued is not None and prereq_time is not None and issued + skew < prereq_time:
        findings.append(_f("ATTESTATION_ISSUED_BEFORE_PREREQUISITE_TIME", f"prerequisite={prereq_time.isoformat()} issued={issued.isoformat()} skew_seconds={max_clock_skew_seconds}"))
    if issued is not None and consumer_time is not None and issued - skew > consumer_time:
        findings.append(_f("ATTESTATION_ISSUED_AFTER_CONSUMER_TIME", f"issued={issued.isoformat()} consumer={consumer_time.isoformat()} skew_seconds={max_clock_skew_seconds}"))
    if prereq_time is not None and consumer_time is not None and prereq_time - skew > consumer_time:
        findings.append(_f("ATTESTATION_CONSUMER_BEFORE_PREREQUISITE_TIME", f"prerequisite={prereq_time.isoformat()} consumer={consumer_time.isoformat()} skew_seconds={max_clock_skew_seconds}"))
    return findings


def _risk_floor_from_contract(wo: dict[str, Any]) -> str:
    required = {x for x in _list(wo.get("required_evidence")) if isinstance(x, str)}
    handoff = _dict(wo.get("handoff"))
    if {"exact_head_review", "durable_review_pass"} & required or handoff.get("next_actor") == "REVIEWER":
        return "MEDIUM"
    return "LOW"


def _load_json_at_commit(root: Path, commit: str, rel: str, code: str, findings: list[ActiveFinding]) -> dict[str, Any]:
    text = file_at_commit(root, commit, rel)
    if text is None:
        findings.append(_f(code, f"{rel} missing at {commit}"))
        return {}
    try:
        value = loads(text, label=f"{commit}:{rel}")
    except StrictJSONError as exc:
        findings.append(_f(code, str(exc)))
        return {}
    if not isinstance(value, dict):
        findings.append(_f(code, f"{rel} must be object"))
        return {}
    return value


def _durable(root: Path, rel: str, prefix: str, findings: list[ActiveFinding], immutable: bool = True) -> None:
    if not path_matches_head(root, rel):
        findings.append(_f(f"{prefix}_NOT_DURABLE_AT_HEAD", rel))
    if immutable and not path_immutable_since_add(root, rel):
        findings.append(_f(f"{prefix}_MUTATED_AFTER_ADD", rel))


def _verify_attestation_path(
    root: Path,
    rel: Any,
    *,
    base_sha: str,
    purpose: str,
    subject_head: str,
    mission_id: str,
    work_order_id: str,
    findings: list[ActiveFinding],
    expected_prerequisite_event: str | None = None,
    expected_evidence_paths: list[str] | None = None,
    consumer_commit: str | None = None,
    consumer_binding: dict[str, Any] | None = None,
    consumer_recorded_at_utc: Any = None,
    max_clock_skew_seconds: int = 120,
) -> None:
    if not isinstance(rel, str) or not rel.startswith("evidence/attestations/") or not rel.endswith(".json"):
        findings.append(_f("EXTERNAL_ATTESTATION_REF_INVALID", repr(rel)))
        return
    path = root / rel
    if not path.is_file():
        findings.append(_f("EXTERNAL_ATTESTATION_MISSING", rel))
        return
    _durable(root, rel, "EXTERNAL_ATTESTATION", findings)

    # R8 consumer binding: validate the exact attestation bytes visible to the
    # consumer event, not merely whatever bytes happen to exist at final HEAD.
    att_bytes = bytes_at_commit(root, consumer_commit, rel) if consumer_commit else path.read_bytes()
    if att_bytes is None:
        findings.append(_f("ATTESTATION_NOT_VISIBLE_TO_CONSUMER", f"{rel} consumer={consumer_commit}"))
        return
    try:
        att = loads(att_bytes.decode("utf-8"), label=f"{consumer_commit or 'HEAD'}:{rel}")
    except (UnicodeDecodeError, StrictJSONError) as exc:
        findings.append(_f("EXTERNAL_ATTESTATION_JSON_INVALID", str(exc)))
        return
    if not isinstance(att, dict):
        findings.append(_f("EXTERNAL_ATTESTATION_INVALID", rel))
        return

    if consumer_commit:
        actual_sha = hashlib.sha256(att_bytes).hexdigest()
        actual_blob = blob_oid_at_commit(root, consumer_commit, rel)
        binding = consumer_binding if isinstance(consumer_binding, dict) else {}
        if binding.get("attestation_sha256") != actual_sha:
            findings.append(_f("ATTESTATION_CONSUMER_SHA256_MISMATCH", f"expected={actual_sha} got={binding.get('attestation_sha256')}"))
        if binding.get("attestation_git_blob") != actual_blob:
            findings.append(_f("ATTESTATION_CONSUMER_BLOB_MISMATCH", f"expected={actual_blob} got={binding.get('attestation_git_blob')}"))

    for err in verify_attestation(
        root, att, base_sha=base_sha, purpose=purpose, subject_head=subject_head,
        mission_id=mission_id, work_order_id=work_order_id,
    ):
        findings.append(_f(err.code, err.message))

    # R8 causal trust: signature + prerequisite/evidence digest + exact consumer
    # content identity. Git ancestry is evaluated with replace objects disabled.
    att_add = commit_adding_path(root, rel)
    att_parent = commit_parent(root, att_add) if att_add else None
    if not att_add or not att_parent:
        findings.append(_f("ATTESTATION_ADD_COMMIT_MISSING", rel))
        return
    if consumer_commit and not is_ancestor(root, att_add, consumer_commit):
        findings.append(_f("ATTESTATION_NOT_BEFORE_CONSUMER_EVENT", f"attestation={att_add} consumer={consumer_commit}"))
    if expected_prerequisite_event is not None:
        if att.get("prerequisite_event") != expected_prerequisite_event:
            findings.append(_f("ATTESTATION_PREREQUISITE_EVENT_MISMATCH", f"expected={expected_prerequisite_event} got={att.get('prerequisite_event')}"))
        prereq_add = commit_adding_path(root, expected_prerequisite_event)
        if not prereq_add or not is_ancestor(root, prereq_add, att_parent):
            findings.append(_f("ATTESTATION_PRE_SIGNED_BEFORE_PREREQUISITE", f"prerequisite={prereq_add} attestation_parent={att_parent}"))
        prereq_bytes = bytes_at_commit(root, att_parent, expected_prerequisite_event)
        prereq_hash = hashlib.sha256(prereq_bytes).hexdigest() if prereq_bytes is not None else None
        if att.get("prerequisite_event_sha256") != prereq_hash:
            findings.append(_f("ATTESTATION_PREREQUISITE_HASH_MISMATCH", f"expected={prereq_hash} got={att.get('prerequisite_event_sha256')}"))

        # R9 temporal consistency. Git ancestry remains the durable causal proof,
        # but signed/event wall-clock claims may not contradict that causal order.
        prereq_raw = None
        if prereq_bytes is not None:
            try:
                prereq_obj = loads(prereq_bytes.decode("utf-8"), label=f"{att_parent}:{expected_prerequisite_event}")
                prereq_raw = prereq_obj.get("recorded_at_utc") if isinstance(prereq_obj, dict) else None
            except (UnicodeDecodeError, StrictJSONError):
                prereq_raw = None
        findings.extend(_temporal_claim_findings(
            att.get("issued_at_utc"), prereq_raw, consumer_recorded_at_utc, max_clock_skew_seconds
        ))
    if expected_evidence_paths is not None:
        expected = sorted(set(expected_evidence_paths))
        declared = sorted(set(att.get("evidence_paths", []))) if isinstance(att.get("evidence_paths"), list) else []
        if declared != expected:
            findings.append(_f("ATTESTATION_EVIDENCE_SET_MISMATCH", f"expected={expected} got={declared}"))
        digest = evidence_digest_at_commit(root, att_parent, expected)
        if digest is None:
            findings.append(_f("ATTESTATION_EVIDENCE_SET_NOT_DURABLE_BEFORE_SIGNING", repr(expected)))
        elif att.get("evidence_digest") != digest:
            findings.append(_f("ATTESTATION_EVIDENCE_DIGEST_MISMATCH", f"expected={digest} got={att.get('evidence_digest')}"))


def validate_active(root: Path, *, require_completion: bool = False) -> list[ActiveFinding]:
    findings: list[ActiveFinding] = []
    state_path = root / "config/control/project-state.v1.json"
    try:
        state = load(state_path)
    except StrictJSONError as exc:
        return [_f("PROJECT_STATE_JSON_INVALID", str(exc))]
    if not isinstance(state, dict):
        return [_f("PROJECT_STATE_INVALID", "project state must be an object")]

    active = state.get("active_mission")
    if not active:
        if state.get("status") == "MISSION_COMPLETE":
            findings.append(_f("COMPLETION_WITHOUT_ACTIVE_MISSION", "MISSION_COMPLETE requires a durable active_mission record"))
        if require_completion:
            findings.append(_f("NO_ACTIVE_MISSION_FOR_COMPLETION", "validate-ready requires an active mission"))
        return findings
    if not isinstance(active, dict):
        return [_f("ACTIVE_MISSION_NOT_STRUCTURED", "active_mission must be an object in R6")]

    mission_id = active.get("mission_id")
    wo_id = state.get("active_work_order")
    if not isinstance(mission_id, str) or not mission_id:
        findings.append(_f("MISSION_ID_MISSING", "active_mission.mission_id required"))
    if not isinstance(wo_id, str) or not wo_id:
        findings.append(_f("ACTIVE_WORK_ORDER_MISSING", "active_work_order required"))
        return findings

    wo_rel = f"config/control/missions/{wo_id}.json"
    wo_path = root / wo_rel
    if not wo_path.is_file():
        findings.append(_f("WORK_ORDER_FILE_MISSING", wo_rel))
        return findings
    try:
        wo = load(wo_path)
    except StrictJSONError as exc:
        findings.append(_f("WORK_ORDER_JSON_INVALID", str(exc)))
        return findings
    if not isinstance(wo, dict):
        return findings + [_f("WORK_ORDER_INVALID", "work order must be an object")]

    required_wo = [
        "work_order_id", "risk", "base_sha", "branch", "implementer_actor_id", "implementer_git_email",
        "allowed_paths", "implementation_paths", "forbidden_paths", "required_evidence", "stop_conditions",
        "started_at_utc", "mission", "handoff", "integration", "acceptance_contract", "requirements_manifest", "attempt_id", "specification"
    ]
    for key in required_wo:
        if key not in wo:
            findings.append(_f("WORK_ORDER_FIELD_MISSING", key))
    if wo.get("work_order_id") != wo_id:
        findings.append(_f("WORK_ORDER_ID_MISMATCH", f"state={wo_id} file={wo.get('work_order_id')}"))
    wo_mission = _dict(wo.get("mission"))
    if mission_id and wo_mission.get("mission_id") != mission_id:
        findings.append(_f("MISSION_ID_MISMATCH", f"state={mission_id} work_order={wo_mission.get('mission_id')}"))

    risk = wo.get("risk")
    if risk not in RISK_ORDER:
        findings.append(_f("RISK_INVALID", str(risk)))
        risk = "CRITICAL"
    floor = _risk_floor_from_contract(wo)
    if RISK_ORDER.get(risk, 3) < RISK_ORDER[floor]:
        findings.append(_f("RISK_BELOW_CONTRACT_FLOOR", f"risk={risk} floor={floor}"))

    if not is_repo(root):
        findings.append(_f("ACTIVE_MISSION_REQUIRES_GIT", "cannot prove temporal state outside Git"))
        return findings
    try:
        current_head = git_head(root)
        current_branch = git_branch(root)
    except GitProofError as exc:
        return findings + [_f("GIT_PROOF_FAILED", str(exc))]

    local_replace_refs = replace_refs(root)
    if local_replace_refs:
        findings.append(_f("GIT_REPLACE_REFS_PRESENT", ",".join(local_replace_refs[:12])))

    dispatch_commit = commit_adding_path(root, wo_rel)
    if not dispatch_commit:
        findings.append(_f("WORK_ORDER_DISPATCH_COMMIT_MISSING", wo_rel))
        return findings
    dispatch_parent = commit_parent(root, dispatch_commit)
    base_sha = wo.get("base_sha")
    if not isinstance(base_sha, str) or not SHA40.match(base_sha) or not commit_exists(root, base_sha):
        findings.append(_f("WORK_ORDER_BASE_SHA_INVALID", str(base_sha)))
        return findings
    if dispatch_parent != base_sha:
        findings.append(_f("WORK_ORDER_BASE_SHA_NOT_DISPATCH_PARENT", f"base={base_sha} dispatch_parent={dispatch_parent}"))

    # R9 wall-clock claims are supplemental consistency assertions. The policy
    # is control-owned; Git ancestry remains the authoritative causal order.
    attestation_clock_skew_seconds = 120
    review_policy_path = root / "config/control/harness/review-policy.v1.json"
    try:
        rp = load(review_policy_path)
        assurance_policy = _dict(rp.get("assurance")) if isinstance(rp, dict) else {}
        if assurance_policy.get("causal_wall_clock_consistency_required") is not True:
            findings.append(_f("R9_TEMPORAL_POLICY_INVALID", "causal_wall_clock_consistency_required must be true"))
        configured_skew = assurance_policy.get("max_clock_skew_seconds")
        if not isinstance(configured_skew, int) or isinstance(configured_skew, bool) or configured_skew < 0 or configured_skew > 300:
            findings.append(_f("R9_TEMPORAL_POLICY_INVALID", f"max_clock_skew_seconds={configured_skew!r}"))
        else:
            attestation_clock_skew_seconds = configured_skew
    except (StrictJSONError, OSError) as exc:
        findings.append(_f("R9_TEMPORAL_POLICY_INVALID", str(exc)))

    specification_rel = wo.get("specification")
    specification_text = ""
    if not isinstance(specification_rel, str) or not specification_rel.startswith("config/control/specifications/"):
        findings.append(_f("MISSION_SPECIFICATION_REF_INVALID", repr(specification_rel)))
    else:
        specification_text = file_at_commit(root, base_sha, specification_rel) or ""
        if not specification_text:
            findings.append(_f("MISSION_SPECIFICATION_NOT_IN_BASE", f"{specification_rel} missing at {base_sha}"))
        add_commit = commit_adding_path(root, specification_rel)
        if not add_commit or not is_ancestor(root, add_commit, base_sha):
            findings.append(_f("MISSION_SPECIFICATION_NOT_PREDECLARED", f"added={add_commit} base={base_sha}"))
        if (root / specification_rel).is_file() and not path_immutable_since_add(root, specification_rel):
            findings.append(_f("MISSION_SPECIFICATION_MUTATED", specification_rel))

    original_text = file_at_commit(root, dispatch_commit, wo_rel)
    if original_text is None:
        findings.append(_f("WORK_ORDER_DISPATCH_BLOB_MISSING", dispatch_commit))
    else:
        try:
            original_wo = loads(original_text, label=f"{dispatch_commit}:{wo_rel}")
            if original_wo != wo:
                findings.append(_f("WORK_ORDER_MUTATED_AFTER_DISPATCH", "risk/scope/identity/handoff/integration must remain immutable"))
        except StrictJSONError as exc:
            findings.append(_f("WORK_ORDER_DISPATCH_JSON_INVALID", str(exc)))

    acceptance_rel = wo.get("acceptance_contract")
    acceptance: dict[str, Any] = {}
    if not isinstance(acceptance_rel, str) or not acceptance_rel.startswith("config/control/acceptance/") or not acceptance_rel.endswith(".json"):
        findings.append(_f("ACCEPTANCE_CONTRACT_REF_INVALID", repr(acceptance_rel)))
    else:
        acceptance_path = root / acceptance_rel
        if not acceptance_path.is_file():
            findings.append(_f("ACCEPTANCE_CONTRACT_MISSING", acceptance_rel))
        else:
            try:
                loaded_acceptance = load(acceptance_path)
                if isinstance(loaded_acceptance, dict):
                    acceptance = loaded_acceptance
                else:
                    findings.append(_f("ACCEPTANCE_CONTRACT_INVALID", "object required"))
            except StrictJSONError as exc:
                findings.append(_f("ACCEPTANCE_CONTRACT_JSON_INVALID", str(exc)))
            add_commit = commit_adding_path(root, acceptance_rel)
            if add_commit != dispatch_commit:
                findings.append(_f("ACCEPTANCE_CONTRACT_NOT_PREDECLARED", f"added={add_commit} dispatch={dispatch_commit}"))
            if not path_immutable_since_add(root, acceptance_rel):
                findings.append(_f("ACCEPTANCE_CONTRACT_MUTATED_AFTER_DISPATCH", acceptance_rel))
            if acceptance.get("mission_id") != mission_id or acceptance.get("work_order_id") != wo_id:
                findings.append(_f("ACCEPTANCE_CONTRACT_SUBJECT_MISMATCH", f"mission={acceptance.get('mission_id')} work_order={acceptance.get('work_order_id')}"))

    requirements_rel = wo.get("requirements_manifest")
    requirements_manifest: dict[str, Any] = {}
    if not isinstance(requirements_rel, str) or not requirements_rel.startswith("config/control/requirements/") or not requirements_rel.endswith(".json"):
        findings.append(_f("REQUIREMENTS_MANIFEST_REF_INVALID", repr(requirements_rel)))
    else:
        requirements_path = root / requirements_rel
        if not requirements_path.is_file():
            findings.append(_f("REQUIREMENTS_MANIFEST_MISSING", requirements_rel))
        else:
            try:
                loaded_requirements = load(requirements_path)
                if isinstance(loaded_requirements, dict):
                    requirements_manifest = loaded_requirements
                else:
                    findings.append(_f("REQUIREMENTS_MANIFEST_INVALID", "object required"))
            except StrictJSONError as exc:
                findings.append(_f("REQUIREMENTS_MANIFEST_JSON_INVALID", str(exc)))
            add_commit = commit_adding_path(root, requirements_rel)
            if add_commit != dispatch_commit:
                findings.append(_f("REQUIREMENTS_MANIFEST_NOT_PREDECLARED", f"added={add_commit} dispatch={dispatch_commit}"))
            if not path_immutable_since_add(root, requirements_rel):
                findings.append(_f("REQUIREMENTS_MANIFEST_MUTATED_AFTER_DISPATCH", requirements_rel))

    bad_dispatch_paths = [p for p in commit_changed_paths(root, dispatch_commit) if not path_matches(p, DISPATCH_ALLOWED)]
    if bad_dispatch_paths:
        findings.append(_f("WORK_ORDER_NOT_PREDECLARED", f"dispatch changed non-control paths: {', '.join(bad_dispatch_paths[:12])}"))

    dispatch_time_text = commit_timestamp(root, dispatch_commit)
    dispatch_time = _parse_time(dispatch_time_text)
    declared_start = _parse_time(wo.get("started_at_utc")) if isinstance(wo.get("started_at_utc"), str) else None
    if declared_start is None:
        findings.append(_f("WORK_ORDER_START_TIME_INVALID", str(wo.get("started_at_utc"))))
    elif dispatch_time and abs((declared_start - dispatch_time).total_seconds()) > 600:
        findings.append(_f("WORK_ORDER_START_TIME_NOT_BOUND_TO_DISPATCH", f"declared={declared_start.isoformat()} dispatch={dispatch_time.isoformat()}"))

    # Mission policy is resolved from the immutable base, never from self-authored branch changes.
    base_policy = _load_json_at_commit(root, base_sha, "config/control/harness/harness-policy.v1.json", "HARNESS_POLICY_NOT_IN_BASE", findings)
    base_trust = _load_json_at_commit(root, base_sha, "config/control/harness/trust-providers.v1.json", "TRUST_ROOT_NOT_IN_BASE", findings)
    base_semantic = _load_json_at_commit(root, base_sha, "config/control/harness/semantic-risk-policy.v1.json", "SEMANTIC_RISK_POLICY_NOT_IN_BASE", findings)
    base_acceptance_schema = _load_json_at_commit(root, base_sha, "config/control/harness/acceptance-contract.schema.v1.json", "ACCEPTANCE_SCHEMA_NOT_IN_BASE", findings)
    if not base_trust:
        findings.append(_f("TRUST_ROOT_UNAVAILABLE", "external assurance cannot be proven"))

    if acceptance and base_semantic and base_acceptance_schema:
        semantic_errors, semantic_tags, semantic_floor = semantic_contract_errors(base_semantic, base_acceptance_schema, wo, acceptance, specification_text)
        for err in semantic_errors:
            code, _, detail = err.partition(":")
            findings.append(_f(code, detail or err))
        contract_floor = _risk_floor_from_contract(wo)
        floor = semantic_floor if RISK_ORDER.get(semantic_floor, 0) >= RISK_ORDER.get(contract_floor, 0) else contract_floor
        if RISK_ORDER.get(risk, 3) < RISK_ORDER.get(floor, 3):
            findings.append(_f("RISK_BELOW_MACHINE_SEMANTIC_FLOOR", f"risk={risk} floor={floor} tags={sorted(semantic_tags)}"))

    if requirements_manifest and acceptance and base_semantic and isinstance(specification_rel, str):
        spec_hash = hashlib.sha256(specification_text.encode("utf-8")).hexdigest()
        req_errors, _clauses = validate_requirements_manifest(
            requirements_manifest, specification_text=specification_text, specification_rel=specification_rel,
            specification_sha256=spec_hash, mission_id=str(mission_id), work_order_id=wo_id,
            acceptance=acceptance, semantic_policy=base_semantic,
        )
        for err in req_errors:
            code, _, detail = err.partition(":")
            findings.append(_f(code, detail or err))

    canonical = state.get("canonical_branch", base_policy.get("canonical_branch", "main"))
    integration = _dict(wo.get("integration"))
    integration_required = integration.get("required", True) is True
    target_branch = integration.get("target_branch", canonical)
    impl_branch = wo.get("branch")
    policy_id = integration.get("policy_id")
    if integration.get("merge_whitelisted") is True:
        findings.append(_f("SELF_AUTHORED_MERGE_WHITELIST_FORBIDDEN", "use a base-owned policy_id"))
    if integration_required and impl_branch == target_branch:
        findings.append(_f("IMPLEMENTATION_ON_CANONICAL_BRANCH_FORBIDDEN", f"{impl_branch} == {target_branch}"))

    # R7 local attempt transparency: durable ancestry remains authoritative, but if
    # reflog is present we refuse a completion that observably reset/amended/rebased
    # either managed implementation or canonical target history after dispatch.
    dispatch_iso = commit_timestamp(root, dispatch_commit)
    for managed_ref in [impl_branch, target_branch]:
        if isinstance(managed_ref, str) and managed_ref:
            bad_actions = suspicious_reflog_actions(root, managed_ref, dispatch_iso)
            if bad_actions:
                findings.append(_f("ACTIVE_ATTEMPT_HISTORY_REWRITE_DETECTED", f"{managed_ref}: {'; '.join(bad_actions[:5])}"))

    attempt_id = wo.get("attempt_id")
    if isinstance(attempt_id, str):
        collisions = []
        for other in (root / "config/control/missions").glob("*.json"):
            try:
                value = load(other)
            except Exception:
                continue
            if isinstance(value, dict) and value.get("attempt_id") == attempt_id and value.get("work_order_id") != wo_id:
                collisions.append(other.name)
        if collisions:
            findings.append(_f("ATTEMPT_ID_REUSED", f"{attempt_id}: {','.join(sorted(collisions))}"))

    whitelists = _dict(_dict(base_policy.get("integration")).get("whitelists"))
    whitelist = _dict(whitelists.get(policy_id)) if isinstance(policy_id, str) else {}
    if policy_id and not whitelist:
        findings.append(_f("INTEGRATION_POLICY_UNKNOWN_AT_BASE", str(policy_id)))
    if whitelist:
        max_risk = whitelist.get("max_risk", "LOW")
        if max_risk not in RISK_ORDER or RISK_ORDER.get(risk, 3) > RISK_ORDER.get(max_risk, -1):
            findings.append(_f("INTEGRATION_POLICY_RISK_EXCEEDED", f"policy={policy_id} risk={risk} max={max_risk}"))

    complete_claim = state.get("status") == "MISSION_COMPLETE" or active.get("complete") is True
    if not complete_claim and not require_completion:
        return findings

    # R6 final acceptance is a Git-HEAD property, not a working-directory property.
    dirty = worktree_changed_paths(root)
    if dirty:
        findings.append(_f("COMPLETION_WORKTREE_NOT_CLEAN", ", ".join(dirty[:12])))
    if state.get("mutation_lease") is not None:
        findings.append(_f("COMPLETION_WITH_ACTIVE_MUTATION_LEASE", "mutation_lease must be null"))
    _durable(root, "config/control/project-state.v1.json", "PROJECT_STATE", findings, immutable=False)
    _durable(root, wo_rel, "WORK_ORDER", findings)
    if isinstance(acceptance_rel, str) and acceptance_rel.startswith("config/control/acceptance/"):
        _durable(root, acceptance_rel, "ACCEPTANCE_CONTRACT", findings)
    if isinstance(requirements_rel, str) and requirements_rel.startswith("config/control/requirements/"):
        _durable(root, requirements_rel, "REQUIREMENTS_MANIFEST", findings)
    if isinstance(specification_rel, str) and specification_rel.startswith("config/control/specifications/"):
        _durable(root, specification_rel, "MISSION_SPECIFICATION", findings)

    lock_rel = "evidence/candidate-lock.v1.json"
    lock_path = root / lock_rel
    lock: dict[str, Any] = {}
    try:
        loaded_lock = load(lock_path)
        if isinstance(loaded_lock, dict):
            lock = loaded_lock
        else:
            findings.append(_f("CANDIDATE_LOCK_INVALID", "object required"))
    except (StrictJSONError, OSError) as exc:
        findings.append(_f("CANDIDATE_LOCK_MISSING_OR_INVALID", str(exc)))
    if lock:
        _durable(root, lock_rel, "CANDIDATE_LOCK", findings)
    candidate = lock.get("candidate_head") or active.get("candidate_head")
    if not isinstance(candidate, str) or not SHA40.match(candidate) or not commit_exists(root, candidate):
        findings.append(_f("CANDIDATE_HEAD_INVALID", str(candidate)))
        return findings
    if active.get("candidate_head") != candidate:
        findings.append(_f("STATE_CANDIDATE_HEAD_MISMATCH", f"state={active.get('candidate_head')} lock={candidate}"))
    if lock.get("branch") != impl_branch:
        findings.append(_f("CANDIDATE_BRANCH_MISMATCH", f"lock={lock.get('branch')} work_order={impl_branch}"))
    if candidate == dispatch_commit or not is_ancestor(root, dispatch_commit, candidate):
        findings.append(_f("CANDIDATE_NOT_AFTER_DISPATCH", f"dispatch={dispatch_commit} candidate={candidate}"))
    if not is_ancestor(root, candidate, "HEAD"):
        findings.append(_f("CANDIDATE_NOT_ANCESTOR", f"candidate={candidate} head={current_head}"))
        return findings

    # Scope and product authorship are proven over every implementation commit, not just candidate author.
    allowed_paths = [x for x in _list(wo.get("allowed_paths")) if isinstance(x, str)]
    implementation_paths = [x for x in _list(wo.get("implementation_paths")) if isinstance(x, str)]
    impl_email = wo.get("implementer_git_email")
    if not implementation_paths:
        findings.append(_f("IMPLEMENTATION_PATHS_MISSING", "explicit implementation_paths required"))
    product_commits = 0
    for commit in commits_between(root, dispatch_commit, candidate):
        paths = commit_changed_paths(root, commit)
        bad_scope = [p for p in paths if not path_matches(p, allowed_paths)]
        if bad_scope:
            findings.append(_f("IMPLEMENTATION_SCOPE_VIOLATION", f"{commit}: {', '.join(bad_scope[:8])}"))
        if any(path_matches(p, implementation_paths) for p in paths):
            product_commits += 1
            author = commit_author_email(root, commit)
            if not isinstance(impl_email, str) or author.lower() != impl_email.lower():
                findings.append(_f("IMPLEMENTER_GIT_IDENTITY_MISMATCH", f"{commit}: author={author} declared={impl_email}"))
    if implementation_paths and product_commits == 0:
        findings.append(_f("NO_IMPLEMENTATION_COMMIT", "candidate contains no declared implementation mutation after dispatch"))

    closure_patterns = _list(lock.get("closure_allowed_paths"))
    base_closure = _list(_dict(base_policy.get("closure_tail")).get("allowed_paths"))
    if sorted(closure_patterns) != sorted(base_closure) or not closure_patterns:
        findings.append(_f("CLOSURE_PATH_POLICY_NOT_BASE_OWNED", f"lock={closure_patterns} base={base_closure}"))
    post_candidate = changed_paths(root, candidate, "HEAD")
    if closure_patterns:
        bad_paths = [p for p in post_candidate if not path_matches(p, closure_patterns)]
        if bad_paths:
            findings.append(_f("PRODUCT_CHANGED_AFTER_CANDIDATE", ", ".join(bad_paths[:12])))

    em_rel = "evidence/evidence-map.json"
    em: dict[str, Any] = {}
    try:
        loaded_em = load(root / em_rel)
        if isinstance(loaded_em, dict):
            em = loaded_em
        else:
            findings.append(_f("EVIDENCE_MAP_INVALID", "object required"))
    except (StrictJSONError, OSError) as exc:
        findings.append(_f("EVIDENCE_MAP_MISSING_OR_INVALID", str(exc)))
    if em:
        _durable(root, em_rel, "EVIDENCE_MAP", findings)
    for key in ("candidate_head", "tested_head", "reviewed_head"):
        if em.get(key) != candidate:
            findings.append(_f("EXACT_HEAD_MISMATCH", f"{key}={em.get(key)} candidate={candidate}"))
    if _list(em.get("unverified_items")):
        findings.append(_f("UNVERIFIED_ITEMS_REMAIN", repr(em.get("unverified_items"))))
    requirement_ids = {r.get("requirement_id") for r in requirements_manifest.get("requirements", []) if isinstance(r, dict) and isinstance(r.get("requirement_id"), str)} if isinstance(requirements_manifest.get("requirements"), list) else set()
    declared_req_coverage = set(em.get("requirement_coverage", [])) if isinstance(em.get("requirement_coverage"), list) else set()
    for missing in sorted(requirement_ids - declared_req_coverage):
        findings.append(_f("EVIDENCE_MAP_REQUIREMENT_COVERAGE_MISSING", missing))
    for unknown in sorted(declared_req_coverage - requirement_ids):
        findings.append(_f("EVIDENCE_MAP_REQUIREMENT_COVERAGE_UNKNOWN", unknown))
    if "lineage" in em:
        findings.append(_f("DERIVED_LINEAGE_MUST_NOT_BE_SELF_AUTHORED", "R6 derives lineage from Git; evidence-map.lineage is forbidden"))

    candidate_receipts = _list(em.get("candidate_receipts", em.get("receipts")))
    if not candidate_receipts:
        findings.append(_f("EVIDENCE_RECEIPTS_MISSING", "candidate receipts required"))
    for rel in candidate_receipts:
        if not isinstance(rel, str):
            findings.append(_f("EVIDENCE_RECEIPT_REF_INVALID", repr(rel)))
            continue
        for err in validate_receipt(root, root / rel, candidate):
            findings.append(_f(err.split(":", 1)[0], err))

    verifier_receipts = _list(em.get("verifier_receipts"))
    verifier_receipt_objects: dict[str, dict[str, Any]] = {}
    for rel in verifier_receipts:
        if not isinstance(rel, str):
            findings.append(_f("VERIFIER_RECEIPT_REF_INVALID", repr(rel)))
            continue
        for err in validate_receipt(root, root / rel, candidate):
            findings.append(_f(err.split(":", 1)[0], err))
        try:
            vr = load(root / rel)
        except (StrictJSONError, OSError):
            vr = {}
        if isinstance(vr, dict):
            verifier_receipt_objects[rel] = vr
        inputs = _list(_dict(vr).get("input_files"))
        verifier_inputs = [i.get("path") for i in inputs if isinstance(i, dict) and isinstance(i.get("path"), str) and i.get("path").startswith("evidence/verifier/")]
        if not verifier_inputs:
            findings.append(_f("VERIFIER_RECEIPT_INPUT_MISSING", rel))
        verifier_result = _dict(vr).get("verifier_result")
        if not isinstance(verifier_result, dict) or not isinstance(verifier_result.get("passed_test_ids"), list):
            findings.append(_f("VERIFIER_RECEIPT_EXECUTION_PROOF_MISSING", rel))
        command = _dict(vr).get("command")
        if not isinstance(command, list) or len(command) < 2 or command[1] != "scripts/harness/verifier_runner.py":
            findings.append(_f("VERIFIER_RECEIPT_RUNNER_COMMAND_INVALID", f"{rel}:{command}"))

    verifier_rel = "evidence/verifier/verification-manifest.json"
    verifier_manifest: dict[str, Any] = {}
    if not (root / verifier_rel).is_file():
        findings.append(_f("VERIFIER_MANIFEST_MISSING", verifier_rel))
    else:
        try:
            loaded_manifest = load(root / verifier_rel)
            if isinstance(loaded_manifest, dict):
                verifier_manifest = loaded_manifest
            else:
                findings.append(_f("VERIFIER_MANIFEST_INVALID", "object required"))
        except StrictJSONError as exc:
            findings.append(_f("VERIFIER_MANIFEST_JSON_INVALID", str(exc)))
        _durable(root, verifier_rel, "VERIFIER_MANIFEST", findings)
        if verifier_manifest.get("mission_id") != mission_id or verifier_manifest.get("work_order_id") != wo_id or verifier_manifest.get("candidate_head") != candidate:
            findings.append(_f("VERIFIER_MANIFEST_SUBJECT_MISMATCH", repr({k: verifier_manifest.get(k) for k in ('mission_id','work_order_id','candidate_head')})))
        manifest_commit = commit_adding_path(root, verifier_rel)
        manifest_email = verifier_manifest.get("git_email")
        if manifest_commit and not is_ancestor(root, candidate, manifest_commit):
            findings.append(_f("VERIFIER_MANIFEST_NOT_AFTER_CANDIDATE", f"candidate={candidate} manifest_commit={manifest_commit}"))
        if manifest_commit and isinstance(manifest_email, str) and commit_author_email(root, manifest_commit).lower() != manifest_email.lower():
            findings.append(_f("VERIFIER_MANIFEST_GIT_IDENTITY_MISMATCH", f"commit={commit_author_email(root, manifest_commit)} manifest={manifest_email}"))
        if isinstance(manifest_email, str) and isinstance(impl_email, str) and manifest_email.lower() == impl_email.lower():
            findings.append(_f("VERIFIER_MANIFEST_NOT_DISTINCT", manifest_email))
        medium_plus = RISK_ORDER.get(risk, 3) >= RISK_ORDER["MEDIUM"]
        for err in coverage_errors(acceptance, verifier_manifest, em, medium_plus=medium_plus, receipt_objects=verifier_receipt_objects):
            code, _, detail = err.partition(":")
            findings.append(_f(code, detail or err))
        for err in requirement_coverage_errors(requirements_manifest, acceptance, verifier_manifest):
            code, _, detail = err.partition(":")
            findings.append(_f(code, detail or err))

    chain_errors, event_records = validate_event_chain(root, candidate)
    findings.extend(_f(err.split(":", 1)[0], err) for err in chain_errors)
    by_phase: dict[str, tuple[Path, dict[str, Any], str | None]] = {}
    for record in event_records:
        phase = record[1].get("phase")
        if isinstance(phase, str):
            if phase in by_phase:
                findings.append(_f("DUPLICATE_EVIDENCE_PHASE", phase))
            by_phase[phase] = record

    review_required = RISK_ORDER.get(risk, 3) >= RISK_ORDER["MEDIUM"] or "exact_head_review" in set(_list(wo.get("required_evidence")))
    required_phases = ["VERIFICATION"]
    if review_required:
        required_phases = ["REVIEW_REQUEST", "REVIEW", "VERIFICATION"]
    if integration_required:
        required_phases += ["DIRECTOR_PRE_INTEGRATION", "INTEGRATION_AUTHORIZATION", "INTEGRATION", "POST_INTEGRATION_VALIDATION", "DIRECTOR_FINAL"]
    else:
        required_phases += ["DIRECTOR_FINAL"]
    for phase in required_phases:
        if phase not in by_phase:
            findings.append(_f("REQUIRED_EVIDENCE_EVENT_MISSING", phase))

    review = _dict(by_phase.get("REVIEW", (None, {}, None))[1])
    verification = _dict(by_phase.get("VERIFICATION", (None, {}, None))[1])
    pre_director = _dict(by_phase.get("DIRECTOR_PRE_INTEGRATION", (None, {}, None))[1])
    integration_auth_event = _dict(by_phase.get("INTEGRATION_AUTHORIZATION", (None, {}, None))[1])
    integration_event = _dict(by_phase.get("INTEGRATION", (None, {}, None))[1])
    post_validation = _dict(by_phase.get("POST_INTEGRATION_VALIDATION", (None, {}, None))[1])
    final_director = _dict(by_phase.get("DIRECTOR_FINAL", (None, {}, None))[1])
    if review_required and review.get("verdict") != "PASS":
        findings.append(_f("REVIEW_NOT_PASS", str(review.get("verdict"))))
    if verification.get("verdict") != "PASS":
        findings.append(_f("VERIFICATION_NOT_PASS", str(verification.get("verdict"))))
    if verification.get("verification_manifest") != "evidence/verifier/verification-manifest.json":
        findings.append(_f("VERIFICATION_EVENT_MANIFEST_LINK_MISSING", repr(verification.get("verification_manifest"))))
    contract_predicates = {p.get("predicate_id") for p in acceptance.get("predicates", []) if isinstance(p, dict) and isinstance(p.get("predicate_id"), str)} if isinstance(acceptance.get("predicates"), list) else set()
    event_predicates = set(verification.get("predicates", [])) if isinstance(verification.get("predicates"), list) else set()
    for missing in sorted(contract_predicates - event_predicates):
        findings.append(_f("VERIFICATION_EVENT_PREDICATE_MISSING", missing))
    verification_record = by_phase.get("VERIFICATION")
    if verification_record and verification_record[2]:
        verification_commit = verification_record[2]
        manifest_add = commit_adding_path(root, "evidence/verifier/verification-manifest.json")
        if manifest_add and not is_ancestor(root, manifest_add, verification_commit):
            findings.append(_f("VERIFICATION_EVENT_PRECEDES_MANIFEST", f"manifest={manifest_add} verification={verification_commit}"))
        manifest_email = verifier_manifest.get("git_email") if isinstance(verifier_manifest, dict) else None
        if isinstance(manifest_email, str) and commit_author_email(root, verification_commit).lower() != manifest_email.lower():
            findings.append(_f("VERIFICATION_EVENT_GIT_IDENTITY_MISMATCH", f"event={commit_author_email(root, verification_commit)} manifest={manifest_email}"))
    if integration_required and pre_director.get("verdict") != "READY_FOR_INTEGRATION":
        findings.append(_f("DIRECTOR_PRE_INTEGRATION_NOT_READY", str(pre_director.get("verdict"))))
    if integration_required and integration_auth_event.get("verdict") != "APPROVE":
        findings.append(_f("INTEGRATION_AUTHORIZATION_EVENT_NOT_APPROVE", str(integration_auth_event.get("verdict"))))
    if integration_required and integration_event.get("verdict") != "INTEGRATED":
        findings.append(_f("INTEGRATION_EVENT_NOT_PASS", str(integration_event.get("verdict"))))
    if integration_required and post_validation.get("verdict") != "PASS":
        findings.append(_f("POST_INTEGRATION_VALIDATION_NOT_PASS", str(post_validation.get("verdict"))))
    if final_director.get("verdict") != "COMPLETE":
        findings.append(_f("DIRECTOR_FINAL_NOT_COMPLETE", str(final_director.get("verdict"))))

    implementer_id = wo.get("implementer_actor_id")
    reviewer_id = review.get("actor_id")
    verifier_id = verification.get("actor_id")
    if review_required and reviewer_id in (None, implementer_id):
        findings.append(_f("REVIEWER_NOT_DISTINCT", f"implementer={implementer_id} reviewer={reviewer_id}"))
    if verifier_id in (None, implementer_id, reviewer_id if review_required else None):
        findings.append(_f("VERIFIER_NOT_DISTINCT", f"implementer={implementer_id} reviewer={reviewer_id} verifier={verifier_id}"))

    # Git identity is provenance only; cryptographic external attestation is the trust boundary.
    if review_required and "REVIEW" in by_phase:
        review_path, _, review_commit = by_phase["REVIEW"]
        if review_commit and isinstance(impl_email, str) and commit_author_email(root, review_commit).lower() == impl_email.lower():
            findings.append(_f("ROLE_GIT_IDENTITY_NOT_DISTINCT", f"REVIEW author equals implementer {impl_email}"))
        assurance = _dict(review.get("assurance"))
        if assurance.get("class") != "EXTERNAL_VERIFIED":
            findings.append(_f("INDEPENDENT_REVIEW_ASSURANCE_INSUFFICIENT", repr(assurance)))
        request_record = by_phase.get("REVIEW_REQUEST")
        request_rel = request_record[0].relative_to(root).as_posix() if request_record else None
        review_commit = by_phase.get("REVIEW", (None, {}, None))[2]
        review_evidence_paths = [p for p in [specification_rel, requirements_rel, acceptance_rel, lock_rel] if isinstance(p, str)] + [p for p in candidate_receipts if isinstance(p, str)]
        _verify_attestation_path(
            root, assurance.get("attestation"), base_sha=base_sha, purpose="REVIEW_PASS",
            subject_head=candidate, mission_id=str(mission_id), work_order_id=wo_id, findings=findings,
            expected_prerequisite_event=request_rel, expected_evidence_paths=review_evidence_paths, consumer_commit=review_commit, consumer_binding=assurance,
            consumer_recorded_at_utc=review.get("recorded_at_utc"), max_clock_skew_seconds=attestation_clock_skew_seconds,
        )

    if integration_required:
        if current_branch != target_branch:
            findings.append(_f("INTEGRATION_NOT_ON_TARGET_BRANCH", f"current={current_branch} target={target_branch}"))
        source_head = integration_event.get("source_head")
        target_pre = integration_event.get("target_premerge_head")
        result_head = integration_event.get("resulting_head")
        method = integration_event.get("method")
        for label, value in (("source", source_head), ("target_premerge", target_pre), ("result", result_head)):
            if not isinstance(value, str) or not SHA40.match(value) or not commit_exists(root, value):
                findings.append(_f("INTEGRATION_LINEAGE_HEAD_INVALID", f"{label}={value}"))
        if all(isinstance(x, str) and SHA40.match(x) and commit_exists(root, x) for x in (source_head, target_pre, result_head)):
            if not is_ancestor(root, candidate, source_head):
                findings.append(_f("INTEGRATION_SOURCE_DOES_NOT_CONTAIN_CANDIDATE", f"candidate={candidate} source={source_head}"))
            if not is_ancestor(root, source_head, result_head):
                findings.append(_f("INTEGRATED_SOURCE_NOT_ANCESTOR_OF_RESULT", f"source={source_head} result={result_head}"))
            if not is_ancestor(root, target_pre, result_head):
                findings.append(_f("TARGET_PREMERGE_NOT_ANCESTOR_OF_RESULT", f"target_pre={target_pre} result={result_head}"))
            if not is_ancestor(root, result_head, "HEAD"):
                findings.append(_f("INTEGRATION_RESULT_NOT_ANCESTOR_OF_FINAL", f"result={result_head} final={current_head}"))
            if method == "FF" and source_head != result_head:
                findings.append(_f("INTEGRATION_METHOD_LINEAGE_MISMATCH", f"FF requires source==result"))
            if method not in {"FF", "MERGE_COMMIT"}:
                findings.append(_f("INTEGRATION_METHOD_INVALID", str(method)))
            source_tail_bad = trajectory_violations(root, candidate, source_head, closure_patterns)
            if source_tail_bad:
                rendered = ", ".join(f"{c[:12]}:{p}" for c,p in source_tail_bad[:12])
                findings.append(_f("INTEGRATION_SOURCE_CONTAINS_POST_REVIEW_PRODUCT_CHANGE", rendered))
            final_tail_bad = trajectory_violations(root, result_head, "HEAD", closure_patterns)
            if final_tail_bad:
                rendered = ", ".join(f"{c[:12]}:{p}" for c,p in final_tail_bad[:12])
                findings.append(_f("POST_INTEGRATION_PRODUCT_CHANGE", rendered))
            integ_record = by_phase.get("INTEGRATION")
            if integ_record and integ_record[2] and not is_ancestor(root, result_head, integ_record[2]):
                findings.append(_f("INTEGRATION_EVENT_NOT_AFTER_RESULT", f"result={result_head} event_commit={integ_record[2]}"))
            # Local reflog is supplemental. Durable ancestry above is the acceptance guard.
            bad_target_reflog = suspicious_reflog_actions_after_ref(root, str(target_branch), result_head)
            if bad_target_reflog:
                findings.append(_f("CANONICAL_HISTORY_REWRITE_DETECTED", "; ".join(bad_target_reflog[:5])))

        # Authorization must be a distinct pre-integration event whose commit is
        # already contained by the exact source_head that gets integrated.
        auth = _dict(integration_auth_event.get("authorization"))
        auth_record = by_phase.get("INTEGRATION_AUTHORIZATION")
        if isinstance(source_head, str) and auth_record and auth_record[2] and not is_ancestor(root, auth_record[2], source_head):
            findings.append(_f("INTEGRATION_AUTHORIZATION_NOT_BEFORE_SOURCE", f"auth_commit={auth_record[2]} source={source_head}"))
        if whitelist:
            if auth.get("mode") != "BASE_POLICY" or auth.get("policy_id") != policy_id:
                findings.append(_f("INTEGRATION_AUTHORIZATION_POLICY_MISMATCH", repr(auth)))
        else:
            if auth.get("mode") != "EXTERNAL_ATTESTATION":
                findings.append(_f("INTEGRATION_AUTHORIZATION_MISSING", repr(auth)))
            pre_record = by_phase.get("DIRECTOR_PRE_INTEGRATION")
            pre_rel = pre_record[0].relative_to(root).as_posix() if pre_record else None
            auth_commit = by_phase.get("INTEGRATION_AUTHORIZATION", (None, {}, None))[2]
            review_event_rel = by_phase.get("REVIEW", (None, {}, None))[0]
            verification_event_rel = by_phase.get("VERIFICATION", (None, {}, None))[0]
            integration_evidence_paths = [p for p in [specification_rel, requirements_rel, acceptance_rel, lock_rel, verifier_rel] if isinstance(p, str)]
            integration_evidence_paths += [p.relative_to(root).as_posix() for p in (review_event_rel, verification_event_rel, pre_record[0] if pre_record else None) if isinstance(p, Path)]
            integration_evidence_paths += [p for p in candidate_receipts + verifier_receipts if isinstance(p, str)]
            review_att_ref = _dict(review.get("assurance")).get("attestation")
            if isinstance(review_att_ref, str): integration_evidence_paths.append(review_att_ref)
            _verify_attestation_path(
                root, auth.get("attestation"), base_sha=base_sha, purpose="INTEGRATION_APPROVE",
                subject_head=candidate, mission_id=str(mission_id), work_order_id=wo_id, findings=findings,
                expected_prerequisite_event=pre_rel, expected_evidence_paths=integration_evidence_paths, consumer_commit=auth_commit, consumer_binding=auth,
                consumer_recorded_at_utc=integration_auth_event.get("recorded_at_utc"), max_clock_skew_seconds=attestation_clock_skew_seconds,
            )
        if integration_event.get("authorization_event_seq") != integration_auth_event.get("seq"):
            findings.append(_f("INTEGRATION_EVENT_AUTHORIZATION_LINK_MISMATCH", f"integration={integration_event.get('authorization_event_seq')} auth={integration_auth_event.get('seq')}"))

        post_receipts = _list(em.get("post_integration_receipts"))
        if not post_receipts:
            findings.append(_f("POST_INTEGRATION_RECEIPT_MISSING", "integration result must be machine-validated"))
        if isinstance(result_head, str):
            for rel in post_receipts:
                if not isinstance(rel, str):
                    findings.append(_f("POST_INTEGRATION_RECEIPT_REF_INVALID", repr(rel)))
                    continue
                for err in validate_receipt(root, root / rel, result_head):
                    findings.append(_f(err.split(":", 1)[0], err))
        receipt_ref = post_validation.get("receipt")
        if post_receipts and receipt_ref not in post_receipts:
            findings.append(_f("POST_INTEGRATION_EVENT_RECEIPT_MISMATCH", f"event={receipt_ref} map={post_receipts}"))

    return findings


def completion_proven(root: Path) -> bool:
    return not validate_active(root, require_completion=True)
