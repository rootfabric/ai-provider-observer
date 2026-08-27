from __future__ import annotations

import json
import os
import sys
import hashlib
import ast
import copy
import subprocess
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

from audit import all_findings, summary
from selftest import run as run_selftest
from continuation import build_continuation
from active_validation import validate_active
from evidence import write_candidate_lock, write_event, write_receipt
from strictjson import load as strict_load
from semantic import semantic_contract_errors, infer_semantic_tags, risk_floor, required_partitions, RISK_ORDER
from requirements import extract_normative_clauses, validate_requirements_manifest
from portable import portable_clone_validate


def _verbose_output() -> bool:
    return os.environ.get("HARNESS_VERBOSE", "").strip().lower() in {"1", "true", "yes", "full"}


def _compact_finding_rows(findings, limit: int = 8):
    """Return bounded, de-duplicated finding rows for agent-facing output.

    Full findings remain machine-computed and are available with HARNESS_VERBOSE=1.
    """
    counts: dict[str, int] = {}
    first = {}
    for f in findings:
        counts[f.code] = counts.get(f.code, 0) + 1
        first.setdefault(f.code, f)
    rows = []
    for code in list(first)[:limit]:
        f = first[code]
        rows.append((f, counts[code]))
    return rows, max(0, len(first) - len(rows))


def _finding_severity(f) -> str:
    """ActiveFinding carries no severity; tolerate both finding shapes."""
    return getattr(f, "severity", "") or "ACTIVE"


def print_findings(findings, *, limit: int = 8):
    if _verbose_output():
        for f in findings:
            print(f"{_finding_severity(f)} {f.code}: {f.message}")
        return
    rows, omitted = _compact_finding_rows(findings, limit)
    for f, count in rows:
        suffix = f" x{count}" if count > 1 else ""
        print(f"{_finding_severity(f)} {f.code}{suffix}: {f.message}")
    if omitted:
        print(f"FINDING_CODES_OMITTED={omitted} (set HARNESS_VERBOSE=1 for full detail)")


def _next_from_findings(findings):
    codes = {f.code for f in findings}
    if any(c.startswith(("REQUIREMENT_", "REQUIREMENTS_", "NORMATIVE_CLAUSE_", "ACCEPTANCE_", "SEMANTIC_", "RISK_BELOW_MACHINE_", "UNIVERSAL_PREDICATE_")) for c in codes):
        return {"handoff_class":"SYSTEM_BLOCKED","next_actor":"DIRECTOR","next_action":"REPAIR_REQUIREMENT_TRACEABILITY_AND_ACCEPTANCE_BEFORE_IMPLEMENTATION"}
    if any(c.startswith(("WORK_ORDER_", "HARNESS_POLICY_", "TRUST_ROOT_", "IMPLEMENTATION_SCOPE_")) for c in codes):
        return {"handoff_class":"SYSTEM_BLOCKED","next_actor":"DIRECTOR","next_action":"REPAIR_CONTROL_PROOF"}
    if any(c.startswith(("CANDIDATE_", "NO_IMPLEMENTATION_", "IMPLEMENTER_GIT_")) for c in codes):
        return {"handoff_class":"ROLE_BOUNDARY","next_actor":"IMPLEMENTER","next_action":"PRODUCE_AND_FREEZE_CANDIDATE"}
    if any(c.startswith(("EVIDENCE_RECEIPT", "EVIDENCE_OUTPUT", "EVIDENCE_RECEIPTS")) for c in codes):
        return {"handoff_class":"ROLE_BOUNDARY","next_actor":"VERIFIER","next_action":"RUN_MACHINE_RECEIPTED_CANDIDATE_VALIDATION"}
    if any(c.startswith(("REVIEW_", "INDEPENDENT_REVIEW", "EXTERNAL_ATTESTATION", "ATTESTATION_", "CAUSAL_ATTESTATION")) for c in codes):
        return {"handoff_class":"ROLE_BOUNDARY","next_actor":"EXTERNAL_REVIEWER","next_action":"PERSIST_CAUSALLY_BOUND_BASE_TRUSTED_SIGNED_REVIEW"}
    if any(c.startswith(("VERIFICATION_", "VERIFIER_", "PREDICATE_", "MEDIUM_PLUS_ADVERSARIAL")) or c == "VERIFIER_NOT_DISTINCT" for c in codes):
        return {"handoff_class":"ROLE_BOUNDARY","next_actor":"VERIFIER","next_action":"BUILD_ADVERSARIAL_PREDICATE_COVERAGE"}
    if "DIRECTOR_PRE_INTEGRATION_NOT_READY" in codes:
        return {"handoff_class":"ROLE_BOUNDARY","next_actor":"DIRECTOR","next_action":"DECIDE_INTEGRATION_READINESS"}
    if any(c.startswith("INTEGRATION_AUTHORIZATION") for c in codes):
        return {"handoff_class":"ROLE_BOUNDARY","next_actor":"EXTERNAL_INTEGRATOR","next_action":"PERSIST_BASE_TRUSTED_INTEGRATION_APPROVAL"}
    if any(c.startswith(("INTEGRATION_", "TARGET_PREMERGE_", "INTEGRATED_SOURCE_")) for c in codes):
        return {"handoff_class":"ROLE_BOUNDARY","next_actor":"INTEGRATOR","next_action":"INTEGRATE_PINNED_SOURCE_AND_RECORD_LINEAGE"}
    if any(c.startswith(("POST_INTEGRATION_", "POST_INTEGRATION_RECEIPT")) for c in codes):
        return {"handoff_class":"ROLE_BOUNDARY","next_actor":"VERIFIER","next_action":"VALIDATE_EXACT_INTEGRATION_RESULT"}
    if "DIRECTOR_FINAL_NOT_COMPLETE" in codes:
        return {"handoff_class":"ROLE_BOUNDARY","next_actor":"DIRECTOR","next_action":"CLOSE_MISSION_AFTER_PROOF"}
    if "COMPLETION_WITH_ACTIVE_MUTATION_LEASE" in codes:
        return {"handoff_class":"ROLE_BOUNDARY","next_actor":"DIRECTOR","next_action":"RELEASE_MUTATION_LEASE"}
    if not findings:
        return {"handoff_class":"ROLE_BOUNDARY","next_actor":"DIRECTOR","next_action":"COMMIT_DERIVED_MISSION_COMPLETE_STATE"}
    return {"handoff_class":"SYSTEM_BLOCKED","next_actor":"DIRECTOR","next_action":"RECONCILE_ACCEPTANCE_FINDINGS"}


def _effective_status(state: dict, readiness, active_errors=None) -> str:
    active = state.get("active_mission") if isinstance(state, dict) else None
    declared = state.get("status") if isinstance(state, dict) else None
    if not isinstance(active, dict):
        return str(declared or "HARNESS_READY")
    complete_claim = declared == "MISSION_COMPLETE" or active.get("complete") is True
    if complete_claim and not readiness:
        return "MISSION_COMPLETE"
    if complete_claim and readiness:
        return "INVALID_COMPLETION"
    if readiness:
        nxt = _next_from_findings(readiness)
        return "SYSTEM_BLOCKED" if nxt.get("handoff_class") == "SYSTEM_BLOCKED" else "MISSION_ACTIVE"
    if active_errors:
        return "SYSTEM_BLOCKED"
    return "MISSION_ACTIVE"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def cmd_status() -> int:
    state = strict_load(ROOT / "config/control/project-state.v1.json")
    active_errors = validate_active(ROOT)
    readiness = validate_active(ROOT, require_completion=True) if isinstance(state, dict) and state.get("active_mission") else []
    complete_claim = isinstance(state, dict) and (state.get("status") == "MISSION_COMPLETE" or (isinstance(state.get("active_mission"), dict) and state["active_mission"].get("complete") is True))
    completion_proven = bool(complete_claim and not readiness)
    effective = _effective_status(state, readiness, active_errors)
    print("HYBRID HARNESS STATUS R12")
    print(f"declared_status={state.get('status')}")
    print(f"effective_status={effective}")
    print(f"active_checkpoint={state.get('active_checkpoint')}")
    print(f"active_epoch={state.get('active_epoch')}")
    print(f"active_work_order={state.get('active_work_order')}")
    print(f"active_mission={state.get('active_mission')}")
    print(f"mutation_lease={state.get('mutation_lease')}")
    print(f"completion_proven={completion_proven}")
    if active_errors:
        print("active_validation=FAIL")
        print_findings(active_errors, limit=6)
    else:
        print("active_validation=PASS")
    if state.get("active_mission") and not completion_proven:
        nxt = _next_from_findings(readiness)
        print("continuation=" + json.dumps(nxt, ensure_ascii=False, sort_keys=True))
        print(f"readiness_findings={len(readiness)}")
    elif completion_proven:
        print('continuation={"handoff_class":"MISSION_COMPLETE","next_actor":null,"next_action":null}')
    return 0


def cmd_resume(args: list[str]) -> int:
    """Re-enter a mission from durable state after timeout/new session without chat reconstruction."""
    runtime_reason = args[0] if args else "UNSPECIFIED"
    state = strict_load(ROOT / "config/control/project-state.v1.json")
    active = state.get("active_mission") if isinstance(state, dict) else None
    readiness = validate_active(ROOT, require_completion=True) if isinstance(active, dict) else []
    active_errors = validate_active(ROOT)
    effective = _effective_status(state, readiness, active_errors)
    print("HYBRID HARNESS RESUME R12")
    print(f"runtime_termination_reason={runtime_reason}")
    print("runtime_reason_is_diagnostic_only=true")
    print(f"declared_status={state.get('status') if isinstance(state, dict) else None}")
    print(f"effective_status={effective}")
    if not isinstance(active, dict):
        print("resume_action=DISPATCH_OR_SELECT_MISSION")
        print("RESUME: PASS")
        return 0
    nxt = {"handoff_class":"MISSION_COMPLETE","next_actor":None,"next_action":None} if effective == "MISSION_COMPLETE" else _next_from_findings(readiness)
    print("continuation=" + json.dumps(nxt, ensure_ascii=False, sort_keys=True))
    if nxt.get("handoff_class") == "ROLE_BOUNDARY":
        print("human_courier_required=false")
        print("resume_action=ROUTE_TO_DECLARED_ROLE_AND_CONTINUE")
    elif nxt.get("handoff_class") == "SYSTEM_BLOCKED":
        print("resume_action=REPAIR_DURABLE_FINDINGS_WITHOUT_HISTORY_REWRITE")
    elif nxt.get("handoff_class") == "MISSION_COMPLETE":
        print("resume_action=NONE")
    print(f"readiness_findings={len(readiness)}")
    print("RESUME: PASS")
    return 0


def cmd_attempt_retry(args: list[str]) -> int:
    """Supersede a failed attempt and dispatch a fresh attempt without reset/rebase.

    Safe automation is intentionally bounded: run on clean canonical main after
    committing any failure evidence from the old attempt. The old branch is kept.
    """
    if len(args) < 4:
        print("usage: attempt-retry NEW_WORK_ORDER_ID NEW_ATTEMPT_ID NEW_BRANCH REASON", file=sys.stderr)
        return 2
    new_wo_id, new_attempt_id, new_branch = args[:3]
    reason = " ".join(args[3:]).strip()
    from gitproof import head as git_head, branch as git_branch, worktree_changed_paths, git
    state_path = ROOT / "config/control/project-state.v1.json"
    state = strict_load(state_path)
    if not isinstance(state, dict) or not isinstance(state.get("active_work_order"), str):
        print("ATTEMPT_RETRY: FAIL no active_work_order", file=sys.stderr); return 2
    canonical = str(state.get("canonical_branch") or "main")
    if git_branch(ROOT) != canonical:
        print(f"ATTEMPT_RETRY: FAIL switch to canonical branch {canonical} without reset; old attempt branch must remain intact", file=sys.stderr); return 2
    dirty = worktree_changed_paths(ROOT)
    if dirty:
        print("ATTEMPT_RETRY: FAIL clean worktree required; commit failure evidence first: " + ",".join(dirty[:12]), file=sys.stderr); return 2
    if git(ROOT, "show-ref", "--verify", f"refs/heads/{new_branch}", check=False):
        print(f"ATTEMPT_RETRY: FAIL branch already exists {new_branch}", file=sys.stderr); return 2
    old_wo_id = state["active_work_order"]
    old_path = ROOT / f"config/control/missions/{old_wo_id}.json"
    if not old_path.is_file():
        print(f"ATTEMPT_RETRY: FAIL missing {old_path.relative_to(ROOT)}", file=sys.stderr); return 2
    old = strict_load(old_path)
    if not isinstance(old, dict):
        print("ATTEMPT_RETRY: FAIL invalid old work order", file=sys.stderr); return 2
    old_attempt = old.get("attempt_id")
    if not isinstance(old_attempt, str) or not old_attempt:
        print("ATTEMPT_RETRY: FAIL old attempt_id missing", file=sys.stderr); return 2
    if new_attempt_id == old_attempt or (ROOT / f"config/control/missions/{new_wo_id}.json").exists():
        print("ATTEMPT_RETRY: FAIL new IDs must be unique", file=sys.stderr); return 2
    req_old = ROOT / str(old.get("requirements_manifest")); ac_old = ROOT / str(old.get("acceptance_contract"))
    if not req_old.is_file() or not ac_old.is_file():
        print("ATTEMPT_RETRY: FAIL immutable requirements/acceptance source missing", file=sys.stderr); return 2
    base = git_head(ROOT)
    mission = old.get("mission") if isinstance(old.get("mission"), dict) else {}
    mission_id = mission.get("mission_id")
    attempts_dir = ROOT / "config/control/missions/attempts"; attempts_dir.mkdir(parents=True, exist_ok=True)
    supersede_path = attempts_dir / f"{old_attempt}.json"
    if supersede_path.exists():
        print(f"ATTEMPT_RETRY: FAIL attempt record already exists {supersede_path.relative_to(ROOT)}", file=sys.stderr); return 2
    failed_head = git(ROOT, "rev-parse", str(old.get("branch")), check=False).strip() or None
    supersede = {
        "schema":"hybrid_harness.attempt_record.v1", "attempt_id":old_attempt, "work_order_id":old_wo_id,
        "mission_id":mission_id, "state":"SUPERSEDED", "reason":reason, "failed_head":failed_head,
        "superseded_at_utc":_utc_now(), "superseded_by_attempt_id":new_attempt_id, "superseded_by_work_order_id":new_wo_id,
        "rule":"Old branch/history remains intact; no reset/amend/rebase is part of retry."
    }
    supersede_path.write_text(json.dumps(supersede, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    req = copy.deepcopy(strict_load(req_old)); ac = copy.deepcopy(strict_load(ac_old)); new = copy.deepcopy(old)
    req_rel = f"config/control/requirements/{new_wo_id}.json"; ac_rel = f"config/control/acceptance/{new_wo_id}.json"; wo_rel=f"config/control/missions/{new_wo_id}.json"
    req["manifest_id"] = f"RM-{new_wo_id}"; req["work_order_id"] = new_wo_id
    ac["contract_id"] = f"AC-{new_wo_id}"; ac["work_order_id"] = new_wo_id
    new.update({"work_order_id":new_wo_id, "attempt_id":new_attempt_id, "base_sha":base, "started_at_utc":_utc_now(), "branch":new_branch, "requirements_manifest":req_rel, "acceptance_contract":ac_rel, "supersedes_work_order_id":old_wo_id})
    (ROOT/req_rel).write_text(json.dumps(req, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    (ROOT/ac_rel).write_text(json.dumps(ac, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    (ROOT/wo_rel).write_text(json.dumps(new, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    state["state_revision"] = int(state.get("state_revision", 0)) + 1
    state["active_work_order"] = new_wo_id; state["active_checkpoint"] = new_wo_id; state["active_epoch"] = mission_id
    state["active_mission"] = {"mission_id":mission_id, "candidate_head":None, "complete":False}
    state["mutation_lease"] = None; state["status"] = "MISSION_ACTIVE"
    state["note"] = f"Fresh attempt {new_attempt_id}; supersedes {old_attempt}. Old history preserved."
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    paths=[str(supersede_path.relative_to(ROOT)), req_rel, ac_rel, wo_rel, "config/control/project-state.v1.json"]
    subprocess.run(["git","-C",str(ROOT),"add","--",*paths], check=True)
    subprocess.run(["git","-C",str(ROOT),"commit","-m",f"control(harness): supersede {old_attempt} and dispatch {new_attempt_id}"], check=True)
    dispatch = git_head(ROOT)
    subprocess.run(["git","-C",str(ROOT),"switch","-c",new_branch], check=True)
    print(f"ATTEMPT_SUPERSEDED={old_attempt}")
    print(f"ATTEMPT_DISPATCHED={new_attempt_id}")
    print(f"WORK_ORDER={new_wo_id}")
    print(f"DISPATCH_PARENT={base}")
    print(f"DISPATCH_COMMIT={dispatch}")
    print(f"BRANCH={new_branch}")
    print("ATTEMPT_RETRY: PASS")
    return 0



def cmd_semantic_scan(args: list[str]) -> int:
    if len(args) != 1:
        print("usage: semantic-scan SPECIFICATION_PATH", file=sys.stderr)
        return 2
    path = ROOT / args[0]
    if not path.is_file():
        print(f"SEMANTIC_SCAN: FAIL missing {args[0]}", file=sys.stderr)
        return 2
    policy = strict_load(ROOT / "config/control/harness/semantic-risk-policy.v1.json")
    text = path.read_text(encoding="utf-8")
    tags = infer_semantic_tags(policy, {"mission":{}}, {}, text)
    floor = risk_floor(policy, tags)
    print("SEMANTIC SCAN R12")
    print(f"specification={args[0]}")
    print(f"machine_risk_floor={floor}")
    for tag in sorted(tags):
        print(f"tag={tag} required_partitions={','.join(sorted(required_partitions(policy, tag)))}")
    if not tags:
        print("tag=<none> required_partitions=<project-specific review still required>")
    print("SEMANTIC_SCAN: PASS")
    return 0


def cmd_requirements_scan(args: list[str]) -> int:
    if len(args) != 1:
        print("usage: requirements-scan SPECIFICATION_PATH", file=sys.stderr)
        return 2
    path = ROOT / args[0]
    if not path.is_file():
        print(f"REQUIREMENTS_SCAN: FAIL missing {args[0]}", file=sys.stderr)
        return 2
    clauses = extract_normative_clauses(path.read_text(encoding="utf-8"))
    print("REQUIREMENTS SCAN R12")
    print(f"specification={args[0]}")
    print(f"normative_clause_count={len(clauses)}")
    for clause in clauses:
        print(f"{clause.clause_id}	line={clause.line}	sha256={clause.sha256}	{clause.text}")
    print("REQUIREMENTS_SCAN: PASS")
    return 0


def cmd_requirements_check(args: list[str]) -> int:
    state = strict_load(ROOT / "config/control/project-state.v1.json")
    wo_id = args[0] if args else (state.get("active_work_order") if isinstance(state, dict) else None)
    if not isinstance(wo_id, str) or not wo_id:
        print("REQUIREMENTS_CHECK: FAIL no work order id", file=sys.stderr)
        return 2
    wo_path = ROOT / f"config/control/missions/{wo_id}.json"
    if not wo_path.is_file():
        print(f"REQUIREMENTS_CHECK: FAIL missing {wo_path.relative_to(ROOT)}", file=sys.stderr)
        return 2
    wo = strict_load(wo_path)
    if not isinstance(wo, dict):
        print("REQUIREMENTS_CHECK: FAIL work order must be object", file=sys.stderr)
        return 2
    spec_rel = wo.get("specification"); req_rel = wo.get("requirements_manifest"); ac_rel = wo.get("acceptance_contract")
    if not all(isinstance(x, str) and (ROOT / x).is_file() for x in (spec_rel, req_rel, ac_rel)):
        print(f"REQUIREMENTS_CHECK: FAIL missing spec/requirements/acceptance refs: {spec_rel} {req_rel} {ac_rel}")
        return 1
    spec_text = (ROOT / spec_rel).read_text(encoding="utf-8")
    manifest = strict_load(ROOT / req_rel); acceptance = strict_load(ROOT / ac_rel)
    semantic_policy = strict_load(ROOT / "config/control/harness/semantic-risk-policy.v1.json")
    errors, clauses = validate_requirements_manifest(
        manifest, specification_text=spec_text, specification_rel=spec_rel,
        specification_sha256=hashlib.sha256(spec_text.encode("utf-8")).hexdigest(),
        mission_id=str((wo.get("mission") or {}).get("mission_id")), work_order_id=wo_id,
        acceptance=acceptance, semantic_policy=semantic_policy,
    )
    print("REQUIREMENTS CHECK R12")
    print(f"work_order={wo_id}")
    print(f"normative_clause_count={len(clauses)}")
    print(f"requirement_count={len(manifest.get('requirements', [])) if isinstance(manifest, dict) else 0}")
    for err in errors:
        print(f"ERROR {err}")
    if errors:
        print(f"REQUIREMENTS_CHECK: FAIL ({len(errors)} errors)")
        return 1
    print("REQUIREMENTS_CHECK: PASS")
    return 0


def cmd_acceptance_check(args: list[str]) -> int:
    state = strict_load(ROOT / "config/control/project-state.v1.json")
    wo_id = args[0] if args else (state.get("active_work_order") if isinstance(state, dict) else None)
    if not isinstance(wo_id, str) or not wo_id:
        print("ACCEPTANCE_CHECK: FAIL no work order id", file=sys.stderr)
        return 2
    wo_path = ROOT / f"config/control/missions/{wo_id}.json"
    if not wo_path.is_file():
        print(f"ACCEPTANCE_CHECK: FAIL missing {wo_path.relative_to(ROOT)}", file=sys.stderr)
        return 2
    wo = strict_load(wo_path)
    if not isinstance(wo, dict):
        print("ACCEPTANCE_CHECK: FAIL work order must be object", file=sys.stderr)
        return 2
    rel = wo.get("acceptance_contract")
    if not isinstance(rel, str) or not (ROOT / rel).is_file():
        print(f"ACCEPTANCE_CHECK: FAIL acceptance contract missing: {rel}")
        return 1
    contract = strict_load(ROOT / rel)
    semantic_policy = strict_load(ROOT / "config/control/harness/semantic-risk-policy.v1.json")
    schema = strict_load(ROOT / "config/control/harness/acceptance-contract.schema.v1.json")
    spec_text = ""
    spec_rel = wo.get("specification")
    if isinstance(spec_rel, str) and (ROOT / spec_rel).is_file():
        spec_text = (ROOT / spec_rel).read_text(encoding="utf-8")
    errors, tags, floor = semantic_contract_errors(semantic_policy, schema, wo, contract, spec_text)
    req_rel = wo.get("requirements_manifest")
    if isinstance(req_rel, str) and (ROOT / req_rel).is_file() and isinstance(spec_rel, str):
        manifest = strict_load(ROOT / req_rel)
        req_errors, _ = validate_requirements_manifest(
            manifest, specification_text=spec_text, specification_rel=spec_rel,
            specification_sha256=hashlib.sha256(spec_text.encode("utf-8")).hexdigest(),
            mission_id=str((wo.get("mission") or {}).get("mission_id")), work_order_id=wo_id,
            acceptance=contract, semantic_policy=semantic_policy,
        )
        errors.extend(req_errors)
    else:
        errors.append(f"REQUIREMENTS_MANIFEST_MISSING:{req_rel}")
    declared = wo.get("risk")
    print("ACCEPTANCE CHECK R12")
    print(f"work_order={wo_id}")
    print(f"semantic_tags={','.join(sorted(tags))}")
    print(f"machine_risk_floor={floor}")
    print(f"declared_risk={declared}")
    if declared not in RISK_ORDER or RISK_ORDER.get(declared, -1) < RISK_ORDER[floor]:
        errors.append(f"RISK_BELOW_MACHINE_SEMANTIC_FLOOR:{declared}<{floor}")
    for err in errors:
        print(f"ERROR {err}")
    if errors:
        print(f"ACCEPTANCE_CHECK: FAIL ({len(errors)} errors)")
        return 1
    print("ACCEPTANCE_CHECK: PASS")
    return 0


def cmd_validate() -> int:
    findings = all_findings(ROOT)
    print_findings(findings)
    errors = [f for f in findings if f.severity == "ERROR"]
    if errors:
        print(f"VALIDATE: FAIL ({len(errors)} errors)")
        return 1
    print("VALIDATE: PASS")
    return 0


def _print_active(findings, label: str) -> int:
    if findings:
        print_findings(findings, limit=8)
        print(f"{label}: FAIL ({len(findings)} errors; compact output)")
        return 1
    print(f"{label}: PASS")
    return 0


def cmd_validate_active() -> int:
    return _print_active(validate_active(ROOT), "VALIDATE_ACTIVE")


def _portable_errors() -> list[str]:
    if os.environ.get("HARNESS_PORTABLE_CHILD") == "1":
        return []
    return portable_clone_validate(ROOT)


def cmd_validate_ready() -> int:
    findings = validate_active(ROOT, require_completion=True)
    rc = _print_active(findings, "VALIDATE_READY")
    if rc != 0:
        return rc
    portable = _portable_errors()
    for err in portable:
        print(f"ERROR {err}")
    if portable:
        print(f"PORTABLE_READY: FAIL ({len(portable)} errors)")
        return 1
    print("PORTABLE_READY: PASS")
    return 0


def cmd_portable_check() -> int:
    errors = portable_clone_validate(ROOT)
    for err in errors:
        print(f"ERROR {err}")
    print(f"PORTABLE_CHECK: {'PASS' if not errors else 'FAIL'}")
    return 0 if not errors else 1


def cmd_hygiene() -> int:
    findings = all_findings(ROOT)
    hygiene_codes = {
        "RULE_LIFECYCLE_INCOMPLETE", "PROTECTED_RULE_RETIREMENT_UNSAFE", "AUTO_RULE_DELETION_FORBIDDEN",
        "MUTABLE_STATE_IN_PROSE", "ROOT_ROUTER_TOO_LONG", "ROOT_ROUTER_TOO_HEAVY",
        "CONTEXT_BUDGET_EXCEEDED", "CONDITIONAL_CONTEXT_BUDGET_EXCEEDED", "CONTEXT_ROUTE_TARGET_MISSING",
        "MACHINE_RULE_PROSE_BLOAT"
    }
    selected = [f for f in findings if f.code in hygiene_codes]
    print_findings(selected)
    s = summary(ROOT)
    print("HYGIENE SUMMARY")
    for k, v in s.items():
        print(f"{k}={v}")
    errors = [f for f in selected if f.severity == "ERROR"]
    if errors:
        print(f"HYGIENE: FAIL ({len(errors)} errors)")
        return 1
    print("HYGIENE: PASS")
    return 0


def cmd_selftest() -> int:
    ok, lines = run_selftest(ROOT)
    if _verbose_output() or not ok:
        for line in lines:
            print(line)
    mutation_count = sum(1 for line in lines if line.startswith("bad-") and line.endswith(": PASS"))
    print(f"SELFTEST: {'PASS' if ok else 'FAIL'} mutations={mutation_count}")
    if ok and not _verbose_output():
        print("SELFTEST_DETAIL=omitted (HARNESS_VERBOSE=1 for full mutation list)")
    return 0 if ok else 1


def cmd_continuation_demo() -> int:
    scenarios = [
        ("review-needed", {"work_order_id":"DEMO-WO","mission":{"mission_id":"DEMO-MISSION"},"review_required":True,"review":{"durable":False},"review_evidence_sink":"SIGNED_ATTESTATION"}),
        ("review-pass", {"work_order_id":"DEMO-WO","mission":{"mission_id":"DEMO-MISSION"},"review_required":True,"review":{"durable":True,"verdict":"PASS","fresh":True},"evidence_fresh":True,"predicates_complete":True}),
        ("human-decision", {"work_order_id":"DEMO-WO","mission":{"mission_id":"DEMO-MISSION"},"blocking_human_attention":True}),
        ("fake-complete", {"work_order_id":"DEMO-WO","mission":{"mission_id":"DEMO-MISSION","complete":True},"completion_proven":False}),
        ("proven-complete", {"work_order_id":"DEMO-WO","mission":{"mission_id":"DEMO-MISSION","complete":True},"completion_proven":True}),
    ]
    for name, state in scenarios:
        print(name)
        print(json.dumps(build_continuation(state), ensure_ascii=False, indent=2))
    print("CONTINUATION_DEMO: PASS")
    return 0


def cmd_freeze_candidate() -> int:
    policy = strict_load(ROOT / "config/control/harness/harness-policy.v1.json")
    patterns = policy.get("closure_tail", {}).get("allowed_paths", [])
    if not patterns:
        print("FREEZE_CANDIDATE: FAIL closure_tail.allowed_paths missing", file=sys.stderr)
        return 2
    path = write_candidate_lock(ROOT, patterns)
    print(f"CANDIDATE_FROZEN: {path.relative_to(ROOT)}")
    return 0


def cmd_evidence_run(args: list[str]) -> int:
    if len(args) < 2:
        print("usage: evidence-run RECEIPT_ID [--subject SHA|candidate] [--input PATH ...] [--env KEY=VALUE ...] -- COMMAND [ARGS...]", file=sys.stderr)
        return 2
    receipt_id = args[0]
    rest = args[1:]
    env_overrides: dict[str, str] = {}
    input_paths: list[str] = []
    subject_override: str | None = None
    while rest:
        if rest[0] == "--env":
            if len(rest) < 2 or "=" not in rest[1]:
                print("--env requires KEY=VALUE", file=sys.stderr); return 2
            key, value = rest[1].split("=", 1)
            if not key or not key.replace("_", "A").isalnum():
                print("invalid environment key", file=sys.stderr); return 2
            env_overrides[key] = value; rest = rest[2:]; continue
        if rest[0] == "--subject":
            if len(rest) < 2:
                print("--subject requires SHA or candidate", file=sys.stderr); return 2
            subject_override = rest[1]; rest = rest[2:]; continue
        if rest[0] == "--input":
            if len(rest) < 2:
                print("--input requires PATH", file=sys.stderr); return 2
            input_paths.append(rest[1]); rest = rest[2:]; continue
        break
    if rest and rest[0] == "--":
        rest = rest[1:]
    if not rest:
        print("evidence command required", file=sys.stderr); return 2
    return write_receipt(ROOT, receipt_id, rest, env_overrides, subject_override=subject_override, input_paths=input_paths)


def cmd_event_add(args: list[str]) -> int:
    if len(args) < 3:
        print("usage: event-add PHASE ACTOR_ID VERDICT [--json JSON_OBJECT]", file=sys.stderr); return 2
    phase, actor, verdict = args[:3]
    rest = args[3:]
    extra = {}
    if rest:
        if len(rest) != 2 or rest[0] != "--json":
            print("event-add accepts only --json JSON_OBJECT", file=sys.stderr); return 2
        value = json.loads(rest[1])
        if not isinstance(value, dict):
            print("--json must be object", file=sys.stderr); return 2
        extra = value
    path = write_event(ROOT, phase, actor, verdict, extra)
    print(f"EVENT_WRITTEN={path.relative_to(ROOT)}")
    print("Commit this event without amending any previous evidence event.")
    return 0


def cmd_report() -> int:
    from gitproof import head as git_head, branch as git_branch
    state = strict_load(ROOT / "config/control/project-state.v1.json")
    readiness = validate_active(ROOT, require_completion=True) if isinstance(state, dict) and state.get("active_mission") else []
    events = sorted((ROOT / "evidence/events").glob("*.json")) if (ROOT / "evidence/events").is_dir() else []
    receipts = sorted((ROOT / "evidence/receipts").glob("*.json")) if (ROOT / "evidence/receipts").is_dir() else []
    semantic_summary = None
    if isinstance(state, dict) and isinstance(state.get("active_work_order"), str):
        wo_path = ROOT / f"config/control/missions/{state['active_work_order']}.json"
        if wo_path.is_file():
            wo = strict_load(wo_path)
            rel = wo.get("acceptance_contract") if isinstance(wo, dict) else None
            if isinstance(rel, str) and (ROOT / rel).is_file():
                contract = strict_load(ROOT / rel)
                semantic_policy = strict_load(ROOT / "config/control/harness/semantic-risk-policy.v1.json")
                schema = strict_load(ROOT / "config/control/harness/acceptance-contract.schema.v1.json")
                spec_text = ""
                spec_rel = wo.get("specification")
                if isinstance(spec_rel, str) and (ROOT / spec_rel).is_file():
                    spec_text = (ROOT / spec_rel).read_text(encoding="utf-8")
                errs, tags, floor = semantic_contract_errors(semantic_policy, schema, wo, contract, spec_text)
                req_summary = None
                req_rel = wo.get("requirements_manifest")
                if isinstance(req_rel, str) and (ROOT / req_rel).is_file() and isinstance(spec_rel, str):
                    manifest = strict_load(ROOT / req_rel)
                    req_errs, clauses = validate_requirements_manifest(
                        manifest, specification_text=spec_text, specification_rel=spec_rel,
                        specification_sha256=hashlib.sha256(spec_text.encode("utf-8")).hexdigest(),
                        mission_id=str((wo.get("mission") or {}).get("mission_id")), work_order_id=state['active_work_order'],
                        acceptance=contract, semantic_policy=semantic_policy,
                    )
                    req_summary = {"normative_clause_count":len(clauses),"requirement_count":len(manifest.get('requirements',[])),"errors":req_errs}
                semantic_summary = {"tags":sorted(tags),"machine_risk_floor":floor,"declared_risk":wo.get("risk"),"contract_errors":errs,"requirement_traceability":req_summary}
    payload = {
        "schema":"hybrid_harness.machine_report.v1",
        "harness_revision":"HYBRID-HARNESS-R12",
        "semantic_assurance": semantic_summary,
        "head": git_head(ROOT) if (ROOT / ".git").exists() else None,
        "branch": git_branch(ROOT) if (ROOT / ".git").exists() else None,
        "declared_status": state.get("status") if isinstance(state, dict) else None,
        "effective_status": _effective_status(state, readiness),
        "active_mission": state.get("active_mission") if isinstance(state, dict) else None,
        "active_work_order": state.get("active_work_order") if isinstance(state, dict) else None,
        "mutation_lease": state.get("mutation_lease") if isinstance(state, dict) else None,
        "completion_proven": bool(isinstance(state, dict) and state.get("active_mission") and not readiness and (state.get("status")=="MISSION_COMPLETE" or state.get("active_mission",{}).get("complete") is True)),
        "readiness_findings": [{"code":f.code,"message":f.message} for f in readiness],
        "event_count": len(events),
        "receipt_count": len(receipts),
        "events": [p.relative_to(ROOT).as_posix() for p in events],
        "receipts": [p.relative_to(ROOT).as_posix() for p in receipts],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not readiness else 1



def _declared_unittest_method_count(start_dir: Path) -> int:
    if not start_dir.is_dir():
        return 0
    count = 0
    for path in sorted(start_dir.rglob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            return -1
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                count += 1
    return count


def cmd_final_report() -> int:
    """Emit the final human-consumable summary entirely from machine state.

    It intentionally distinguishes discovered test cases from durable receipt
    results instead of allowing an agent to merge those concepts in prose.
    """
    from gitproof import head as git_head, branch as git_branch, replace_refs

    state = strict_load(ROOT / "config/control/project-state.v1.json")
    active = state.get("active_mission") if isinstance(state, dict) else None
    readiness = validate_active(ROOT, require_completion=True) if isinstance(active, dict) else []
    audit_findings = all_findings(ROOT)
    audit_errors = [f for f in audit_findings if f.severity == "ERROR"]
    portable_errors = portable_clone_validate(ROOT) if (ROOT / ".git").exists() else ["NOT_A_GIT_REPOSITORY"]
    selftest_ok, selftest_lines = run_selftest(ROOT)
    selftest_mutations = sum(1 for line in selftest_lines if line.startswith("bad-") and line.endswith(": PASS"))

    events = sorted((ROOT / "evidence/events").glob("*.json")) if (ROOT / "evidence/events").is_dir() else []
    receipts = sorted((ROOT / "evidence/receipts").glob("*.json")) if (ROOT / "evidence/receipts").is_dir() else []
    receipt_rows = []
    for path in receipts:
        try:
            obj = strict_load(path)
        except Exception as exc:
            receipt_rows.append((path.relative_to(ROOT).as_posix(), None, f"INVALID:{exc}"))
            continue
        if isinstance(obj, dict):
            receipt_rows.append((path.relative_to(ROOT).as_posix(), obj.get("exit_code"), obj.get("receipt_id")))

    mission_id = None
    attempt_id = None
    risk = None
    normative_clause_count = 0
    requirement_count = 0
    predicate_count = 0
    verifier_case_count = 0
    verifier_predicate_coverage_count = 0
    verifier_runtime_pass_test_ids: set[str] = set()
    wo_id = state.get("active_work_order") if isinstance(state, dict) else None
    if isinstance(wo_id, str):
        wo_path = ROOT / f"config/control/missions/{wo_id}.json"
        if wo_path.is_file():
            wo = strict_load(wo_path)
            if isinstance(wo, dict):
                mission_id = (wo.get("mission") if isinstance(wo.get("mission"), dict) else {}).get("mission_id")
                attempt_id = wo.get("attempt_id")
                risk = wo.get("risk")
                spec_rel = wo.get("specification")
                req_rel = wo.get("requirements_manifest")
                ac_rel = wo.get("acceptance_contract")
                if isinstance(spec_rel, str) and (ROOT / spec_rel).is_file():
                    normative_clause_count = len(extract_normative_clauses((ROOT / spec_rel).read_text(encoding="utf-8")))
                if isinstance(req_rel, str) and (ROOT / req_rel).is_file():
                    req = strict_load(ROOT / req_rel)
                    if isinstance(req, dict) and isinstance(req.get("requirements"), list):
                        requirement_count = len(req["requirements"])
                if isinstance(ac_rel, str) and (ROOT / ac_rel).is_file():
                    ac = strict_load(ROOT / ac_rel)
                    if isinstance(ac, dict) and isinstance(ac.get("predicates"), list):
                        predicate_count = len(ac["predicates"])

    verifier_manifest = ROOT / "evidence/verifier/verification-manifest.json"
    if verifier_manifest.is_file():
        vm = strict_load(verifier_manifest)
        if isinstance(vm, dict):
            verifier_case_count = len(vm.get("cases", [])) if isinstance(vm.get("cases"), list) else 0
            verifier_predicate_coverage_count = len(vm.get("predicate_coverage", [])) if isinstance(vm.get("predicate_coverage"), list) else 0
    for receipt_path in receipts:
        try:
            robj = strict_load(receipt_path)
        except Exception:
            continue
        if isinstance(robj, dict) and isinstance(robj.get("verifier_result"), dict):
            for tid in robj["verifier_result"].get("passed_test_ids", []):
                if isinstance(tid, str): verifier_runtime_pass_test_ids.add(tid)

    product_test_count = _declared_unittest_method_count(ROOT / "tests/product")
    all_test_count = _declared_unittest_method_count(ROOT / "tests")
    harness_test_count = all_test_count - product_test_count if all_test_count >= 0 and product_test_count >= 0 else -1
    complete_claim = bool(isinstance(state, dict) and (state.get("status") == "MISSION_COMPLETE" or (isinstance(active, dict) and active.get("complete") is True)))
    completion_proven = bool(complete_claim and not readiness and not audit_errors and not portable_errors and selftest_ok)

    print("HYBRID HARNESS FINAL REPORT R12")
    print(f"harness_revision=HYBRID-HARNESS-R12")
    print(f"head={git_head(ROOT) if (ROOT / '.git').exists() else None}")
    print(f"branch={git_branch(ROOT) if (ROOT / '.git').exists() else None}")
    print(f"git_replace_ref_count={len(replace_refs(ROOT)) if (ROOT / '.git').exists() else -1}")
    print(f"declared_status={state.get('status') if isinstance(state, dict) else None}")
    print(f"effective_status={_effective_status(state, readiness)}")
    print(f"mission_id={mission_id}")
    print(f"work_order_id={wo_id}")
    print(f"attempt_id={attempt_id}")
    print(f"risk={risk}")
    print(f"completion_proven={str(completion_proven).lower()}")
    print(f"readiness_finding_count={len(readiness)}")
    print(f"audit_error_count={len(audit_errors)}")
    print(f"portable_check={'PASS' if not portable_errors else 'FAIL'}")
    print(f"selftest={'PASS' if selftest_ok else 'FAIL'}")
    print(f"selftest_mutation_count={selftest_mutations}")
    print(f"harness_unittest_methods_declared={harness_test_count}")
    print(f"product_unittest_methods_declared={product_test_count}")
    print(f"verifier_manifest_case_count={verifier_case_count}")
    print(f"verifier_predicate_coverage_count={verifier_predicate_coverage_count}")
    print(f"verifier_runtime_pass_test_count={len(verifier_runtime_pass_test_ids)}")
    print(f"normative_clause_count={normative_clause_count}")
    print(f"requirement_count={requirement_count}")
    print(f"acceptance_predicate_count={predicate_count}")
    print(f"event_count={len(events)}")
    print(f"receipt_count={len(receipts)}")
    for rel, exit_code, rid in receipt_rows:
        print(f"receipt={rel}\treceipt_id={rid}\texit_code={exit_code}")
    if _verbose_output():
        for f in readiness:
            print(f"readiness_finding={f.code}\t{f.message}")
        for f in audit_errors:
            print(f"audit_error={f.code}\t{f.message}")
        for err in portable_errors:
            print(f"portable_error={err}")
    else:
        rrows, romitted = _compact_finding_rows(readiness, 8)
        for f, count in rrows:
            print(f"readiness_code={f.code}\tcount={count}")
        if romitted:
            print(f"readiness_codes_omitted={romitted}")
        arows, aomitted = _compact_finding_rows(audit_errors, 6)
        for f, count in arows:
            print(f"audit_code={f.code}\tcount={count}")
        if aomitted:
            print(f"audit_codes_omitted={aomitted}")
        if portable_errors:
            print(f"portable_error_count={len(portable_errors)}")
    if not selftest_ok:
        for line in selftest_lines:
            if line.endswith(": FAIL") or line.startswith("baseline must"):
                print(f"selftest_error={line}")
    print("FINAL_REPORT: " + ("PASS" if (not active or completion_proven) and not audit_errors and not portable_errors and selftest_ok else "FAIL"))
    return 0 if (not active or completion_proven) and not audit_errors and not portable_errors and selftest_ok else 1

def cmd_verifier_run(args: list[str]) -> int:
    """Run verifier-owned tests through the base-owned R12 semantic runner.

    The resulting receipt contains the exact PASS test IDs plus runtime case /
    partition / oracle observations used by completion validation.
    """
    if not args:
        print("usage: verifier-run RECEIPT_ID [START_DIR] [PATTERN]", file=sys.stderr)
        return 2
    receipt_id = args[0]
    start_dir = args[1] if len(args) > 1 else "evidence/verifier"
    pattern = args[2] if len(args) > 2 else "test_*.py"
    start = ROOT / start_dir
    if not start.is_dir():
        print(f"VERIFIER_RUN: FAIL missing {start_dir}", file=sys.stderr)
        return 2
    input_paths = [p.relative_to(ROOT).as_posix() for p in sorted(start.rglob("*.py")) if p.is_file()]
    # Include current implementation files as cryptographically named inputs.
    try:
        state = strict_load(ROOT / "config/control/project-state.v1.json")
        wo_id = state.get("active_work_order") if isinstance(state, dict) else None
        if isinstance(wo_id, str):
            wo = strict_load(ROOT / f"config/control/missions/{wo_id}.json")
            for pat in wo.get("implementation_paths", []) if isinstance(wo, dict) else []:
                if isinstance(pat, str):
                    for path in sorted(ROOT.glob(pat)):
                        if path.is_file():
                            rel = path.relative_to(ROOT).as_posix()
                            if rel not in input_paths:
                                input_paths.append(rel)
    except Exception:
        pass
    command = [sys.executable, "scripts/harness/verifier_runner.py", start_dir, pattern]
    return write_receipt(ROOT, receipt_id, command, subject_override="candidate", input_paths=input_paths)



def _git_commit_paths(paths: list[str], message: str) -> str | None:
    """Commit a bounded generated-evidence set if it changed."""
    paths = [p for p in paths if (ROOT / p).exists()]
    if not paths:
        return None
    subprocess.run(["git", "-C", str(ROOT), "add", "--", *paths], check=True)
    staged = subprocess.run(
        ["git", "-C", str(ROOT), "diff", "--cached", "--quiet", "--exit-code"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if staged.returncode == 0:
        return None
    subprocess.run(["git", "-C", str(ROOT), "commit", "-m", message], check=True)
    from gitproof import head as git_head
    return git_head(ROOT)


def cmd_brief(args: list[str]) -> int:
    """Bounded agent context. This is the normal R12 entry point."""
    runtime_reason = args[0] if args else "UNSPECIFIED"
    state = strict_load(ROOT / "config/control/project-state.v1.json")
    active = state.get("active_mission") if isinstance(state, dict) else None
    active_errors = validate_active(ROOT)
    readiness = validate_active(ROOT, require_completion=True) if isinstance(active, dict) else []
    effective = _effective_status(state, readiness, active_errors)
    if not isinstance(active, dict):
        nxt = {"handoff_class":"ROLE_BOUNDARY","next_actor":"DIRECTOR","next_action":"DISPATCH_OR_SELECT_MISSION"}
    elif effective == "MISSION_COMPLETE":
        nxt = {"handoff_class":"MISSION_COMPLETE","next_actor":None,"next_action":None}
    elif active_errors:
        nxt = _next_from_findings(active_errors)
    else:
        nxt = _next_from_findings(readiness)

    print("HYBRID HARNESS BRIEF R12")
    print(f"runtime_reason={runtime_reason}")
    print(f"effective_status={effective}")
    print(f"work_order={state.get('active_work_order') if isinstance(state, dict) else None}")
    print(f"next_actor={nxt.get('next_actor')}")
    print(f"next_action={nxt.get('next_action')}")
    print(f"handoff_class={nxt.get('handoff_class')}")
    print(f"active_errors={len(active_errors)}")
    print(f"completion_findings={len(readiness)}")

    read_paths: list[str] = []
    wo_id = state.get("active_work_order") if isinstance(state, dict) else None
    if isinstance(wo_id, str):
        wo_rel = f"config/control/missions/{wo_id}.json"
        read_paths.append(wo_rel)
        try:
            wo = strict_load(ROOT / wo_rel)
        except Exception:
            wo = {}
        if isinstance(wo, dict):
            for key in ("specification", "requirements_manifest", "acceptance_contract"):
                rel = wo.get(key)
                if isinstance(rel, str) and rel not in read_paths:
                    read_paths.append(rel)
    print("must_read=" + (",".join(read_paths) if read_paths else "<none>"))
    print("normal_context_forbidden=scripts/harness/**,config/control/harness/**,raw evidence logs")
    print("diagnostic_rule=use './CONTROL_HARNESS.sh diagnose'; inspect Harness source only for SYSTEM_BLOCKED harness defect")

    source = active_errors if active_errors else readiness
    rows, omitted = _compact_finding_rows(source, 6)
    if rows:
        print("primary_codes=" + ",".join(f"{f.code}:{count}" for f, count in rows))
        if omitted:
            print(f"primary_codes_omitted={omitted}")

    action = str(nxt.get("next_action"))
    if action == "PRODUCE_AND_FREEZE_CANDIDATE":
        print("command_hint=commit product/tests, run them locally until green, then candidate-check RECEIPT -- TEST_COMMAND")
    elif action in {"RUN_MACHINE_RECEIPTED_CANDIDATE_VALIDATION", "BUILD_ADVERSARIAL_PREDICATE_COVERAGE"}:
        print("command_hint=commit verifier tests+manifest, then verifier-check verifier-adversarial evidence/verifier 'test_*.py'")
    elif action == "PERSIST_CAUSALLY_BOUND_BASE_TRUSTED_SIGNED_REVIEW":
        print("command_hint=event-record REVIEW_REQUEST reviewer-actor REQUESTED; route compact review packet to external custody")
    elif action == "DECIDE_INTEGRATION_READINESS":
        print("command_hint=record director readiness only after review+verification evidence validates")
    elif action == "CLOSE_MISSION_AFTER_PROOF":
        print("command_hint=validate-ready && portable-check && final-report")
    print("BRIEF: PASS")
    return 0


def cmd_diagnose(args: list[str]) -> int:
    """Explain blockers without dumping the whole completion matrix by default."""
    full = "--full" in args
    state = strict_load(ROOT / "config/control/project-state.v1.json")
    active = state.get("active_mission") if isinstance(state, dict) else None
    active_errors = validate_active(ROOT)
    readiness = validate_active(ROOT, require_completion=True) if isinstance(active, dict) else []
    findings = active_errors if active_errors else readiness
    print("HYBRID HARNESS DIAGNOSE R12")
    print(f"scope={'active_validation' if active_errors else 'completion_readiness'}")
    print(f"finding_count={len(findings)}")
    if full:
        for f in findings:
            print(f"{f.severity} {f.code}: {f.message}")
    else:
        counts: dict[str, int] = {}
        first = {}
        for f in findings:
            counts[f.code] = counts.get(f.code, 0) + 1
            first.setdefault(f.code, f)
        for code in list(first)[:12]:
            f = first[code]
            print(f"{code}\tcount={counts[code]}\tfirst={f.message}")
        if len(first) > 12:
            print(f"codes_omitted={len(first)-12}")
        print("detail_hint=rerun with --full only for a targeted repair")
    print("DIAGNOSE: " + ("PASS" if not findings else "BLOCKED"))
    return 0 if not findings else 1


def cmd_candidate_check(args: list[str]) -> int:
    """Freeze a clean product candidate, run one machine test receipt, and commit evidence.

    This replaces the common 5-10 call sequence: freeze -> inspect -> add -> commit ->
    evidence-run -> inspect -> add -> commit.
    """
    if len(args) < 3:
        print("usage: candidate-check RECEIPT_ID -- COMMAND [ARGS...]", file=sys.stderr)
        return 2
    receipt_id = args[0]
    rest = args[1:]
    if rest and rest[0] == "--":
        rest = rest[1:]
    if not rest:
        print("candidate-check requires a test command", file=sys.stderr)
        return 2
    state = strict_load(ROOT / "config/control/project-state.v1.json")
    active = state.get("active_mission") if isinstance(state, dict) else None
    wo_id = state.get("active_work_order") if isinstance(state, dict) else None
    if not isinstance(active, dict) or not isinstance(wo_id, str):
        print("CANDIDATE_CHECK: FAIL active mission/work order required", file=sys.stderr)
        return 1
    try:
        wo = strict_load(ROOT / f"config/control/missions/{wo_id}.json")
        expected_branch = wo.get("branch") if isinstance(wo, dict) else None
        from gitproof import branch as git_branch
        current_branch = git_branch(ROOT)
        if isinstance(expected_branch, str) and current_branch != expected_branch:
            print(f"CANDIDATE_CHECK: FAIL branch mismatch current={current_branch} expected={expected_branch}", file=sys.stderr)
            return 1
        active_errors = validate_active(ROOT)
        if active_errors:
            print("CANDIDATE_CHECK: FAIL active validation", file=sys.stderr)
            print_findings(active_errors, limit=6)
            return 1
        lock = write_candidate_lock(ROOT, strict_load(ROOT / "config/control/harness/harness-policy.v1.json").get("closure_tail", {}).get("allowed_paths", []))
    except Exception as exc:
        print(f"CANDIDATE_CHECK: FAIL freeze: {exc}", file=sys.stderr)
        return 1
    _git_commit_paths([lock.relative_to(ROOT).as_posix()], "evidence(candidate): freeze exact candidate")
    try:
        rc = write_receipt(ROOT, receipt_id, rest, subject_override="candidate")
    except Exception as exc:
        print(f"CANDIDATE_CHECK: FAIL receipt: {exc}", file=sys.stderr)
        return 1
    raw = f"evidence/raw/{receipt_id}.log"
    receipt = f"evidence/receipts/{receipt_id}.json"
    commit = _git_commit_paths([raw, receipt], f"evidence(candidate): machine receipt {receipt_id}")
    try:
        lock_data = strict_load(lock)
        candidate = lock_data.get("candidate_head") if isinstance(lock_data, dict) else None
    except Exception:
        candidate = None
    print(f"CANDIDATE_HEAD={candidate}")
    print(f"EVIDENCE_COMMIT={commit}")
    print(f"CANDIDATE_CHECK: {'PASS' if rc == 0 else 'FAIL'}")
    return rc


def cmd_verifier_check(args: list[str]) -> int:
    """Run R11/R12 semantic verifier receipt and durably commit only its generated output."""
    if not args:
        print("usage: verifier-check RECEIPT_ID [START_DIR] [PATTERN]", file=sys.stderr)
        return 2
    receipt_id = args[0]
    rc = cmd_verifier_run(args)
    raw = f"evidence/raw/{receipt_id}.log"
    receipt = f"evidence/receipts/{receipt_id}.json"
    commit = _git_commit_paths([raw, receipt], f"evidence(verifier): machine receipt {receipt_id}")
    print(f"VERIFIER_EVIDENCE_COMMIT={commit}")
    print(f"VERIFIER_CHECK: {'PASS' if rc == 0 else 'FAIL'}")
    return rc


def cmd_event_record(args: list[str]) -> int:
    """Append and commit one immutable event in one deterministic call."""
    if len(args) < 3:
        print("usage: event-record PHASE ACTOR_ID VERDICT [--json JSON_OBJECT]", file=sys.stderr)
        return 2
    phase, actor, verdict = args[:3]
    rest = args[3:]
    extra = {}
    if rest:
        if len(rest) != 2 or rest[0] != "--json":
            print("event-record accepts only --json JSON_OBJECT", file=sys.stderr)
            return 2
        try:
            extra = json.loads(rest[1])
        except json.JSONDecodeError as exc:
            print(f"event-record JSON invalid: {exc}", file=sys.stderr)
            return 2
        if not isinstance(extra, dict):
            print("--json must be object", file=sys.stderr)
            return 2
    try:
        path = write_event(ROOT, phase, actor, verdict, extra)
        commit = _git_commit_paths([path.relative_to(ROOT).as_posix()], f"evidence(event): {phase} {verdict}")
    except Exception as exc:
        print(f"EVENT_RECORD: FAIL {exc}", file=sys.stderr)
        return 1
    print(f"EVENT={path.relative_to(ROOT)}")
    print(f"EVENT_COMMIT={commit}")
    print("EVENT_RECORD: PASS")
    return 0


def cmd_demo() -> int:
    rc1 = cmd_validate()
    rc2 = cmd_hygiene() if rc1 == 0 else 1
    rc3 = cmd_selftest() if rc1 == 0 else 1
    ok = rc1 == rc2 == rc3 == 0
    print(f"DEMO: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main() -> int:
    args = sys.argv[1:]
    cmd = args[0] if args else "status"
    simple = {
        "status": cmd_status, "validate": cmd_validate, "validate-active": cmd_validate_active,
        "validate-ready": cmd_validate_ready, "portable-check": cmd_portable_check, "hygiene": cmd_hygiene, "selftest": cmd_selftest,
        "continuation-demo": cmd_continuation_demo, "freeze-candidate": cmd_freeze_candidate, "report": cmd_report, "final-report": cmd_final_report, "demo": cmd_demo,
    }
    if cmd == "resume": return cmd_resume(args[1:])
    if cmd == "brief": return cmd_brief(args[1:])
    if cmd == "diagnose": return cmd_diagnose(args[1:])
    if cmd == "attempt-retry": return cmd_attempt_retry(args[1:])
    if cmd == "candidate-check": return cmd_candidate_check(args[1:])
    if cmd == "evidence-run": return cmd_evidence_run(args[1:])
    if cmd == "verifier-run": return cmd_verifier_run(args[1:])
    if cmd == "verifier-check": return cmd_verifier_check(args[1:])
    if cmd == "event-add": return cmd_event_add(args[1:])
    if cmd == "event-record": return cmd_event_record(args[1:])
    if cmd == "acceptance-check": return cmd_acceptance_check(args[1:])
    if cmd == "requirements-scan": return cmd_requirements_scan(args[1:])
    if cmd == "requirements-check": return cmd_requirements_check(args[1:])
    if cmd == "semantic-scan": return cmd_semantic_scan(args[1:])
    if cmd not in simple:
        print("usage: control.py status|resume|brief|diagnose|attempt-retry|candidate-check|validate|validate-active|validate-ready|portable-check|semantic-scan|requirements-scan|requirements-check|acceptance-check|hygiene|selftest|continuation-demo|freeze-candidate|evidence-run|verifier-run|verifier-check|event-add|event-record|report|final-report|demo", file=sys.stderr)
        return 2
    return simple[cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
