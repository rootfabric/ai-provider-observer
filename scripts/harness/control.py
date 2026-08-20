from __future__ import annotations

import json
import os
import sys
import hashlib
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


def print_findings(findings):
    for f in findings:
        print(f"{f.severity} {f.code}: {f.message}")


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


def cmd_status() -> int:
    state = strict_load(ROOT / "config/control/project-state.v1.json")
    active_errors = validate_active(ROOT)
    readiness = validate_active(ROOT, require_completion=True) if isinstance(state, dict) and state.get("active_mission") else []
    complete_claim = isinstance(state, dict) and (state.get("status") == "MISSION_COMPLETE" or (isinstance(state.get("active_mission"), dict) and state["active_mission"].get("complete") is True))
    completion_proven = bool(complete_claim and not readiness)
    print("HYBRID HARNESS STATUS R8")
    print(f"status={state.get('status')}")
    print(f"active_checkpoint={state.get('active_checkpoint')}")
    print(f"active_epoch={state.get('active_epoch')}")
    print(f"active_work_order={state.get('active_work_order')}")
    print(f"active_mission={state.get('active_mission')}")
    print(f"mutation_lease={state.get('mutation_lease')}")
    print(f"completion_proven={completion_proven}")
    if active_errors:
        print("active_validation=FAIL")
        for f in active_errors:
            print(f"ERROR {f.code}: {f.message}")
    else:
        print("active_validation=PASS")
    if state.get("active_mission") and not completion_proven:
        nxt = _next_from_findings(readiness)
        print("continuation=" + json.dumps(nxt, ensure_ascii=False, sort_keys=True))
        print(f"readiness_findings={len(readiness)}")
    elif completion_proven:
        print('continuation={"handoff_class":"MISSION_COMPLETE","next_actor":null,"next_action":null}')
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
    print("SEMANTIC SCAN R8")
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
    print("REQUIREMENTS SCAN R8")
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
    print("REQUIREMENTS CHECK R8")
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
    print("ACCEPTANCE CHECK R8")
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
    for f in findings:
        print(f"ERROR {f.code}: {f.message}")
    if findings:
        print(f"{label}: FAIL ({len(findings)} errors)")
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
    for line in lines:
        print(line)
    print(f"SELFTEST: {'PASS' if ok else 'FAIL'}")
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
        "harness_revision":"HYBRID-HARNESS-R8",
        "semantic_assurance": semantic_summary,
        "head": git_head(ROOT) if (ROOT / ".git").exists() else None,
        "branch": git_branch(ROOT) if (ROOT / ".git").exists() else None,
        "status": state.get("status") if isinstance(state, dict) else None,
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
        "continuation-demo": cmd_continuation_demo, "freeze-candidate": cmd_freeze_candidate, "report": cmd_report, "demo": cmd_demo,
    }
    if cmd == "evidence-run": return cmd_evidence_run(args[1:])
    if cmd == "event-add": return cmd_event_add(args[1:])
    if cmd == "acceptance-check": return cmd_acceptance_check(args[1:])
    if cmd == "requirements-scan": return cmd_requirements_scan(args[1:])
    if cmd == "requirements-check": return cmd_requirements_check(args[1:])
    if cmd == "semantic-scan": return cmd_semantic_scan(args[1:])
    if cmd not in simple:
        print("usage: control.py status|validate|validate-active|validate-ready|portable-check|semantic-scan|requirements-scan|requirements-check|acceptance-check|hygiene|selftest|continuation-demo|freeze-candidate|evidence-run|event-add|report|demo", file=sys.stderr)
        return 2
    return simple[cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
