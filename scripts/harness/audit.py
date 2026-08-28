from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from strictjson import StrictJSONError, load as strict_load
from active_validation import validate_active
from rules import health_report as rule_health_report, lifecycle_issues


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    message: str


CONFIG = Path("config/control/harness")
STATE = Path("config/control/project-state.v1.json")


def load_json(root: Path, rel: Path) -> dict:
    path = root / rel
    if not path.is_file():
        raise FileNotFoundError(rel.as_posix())
    value = strict_load(path)
    if not isinstance(value, dict):
        raise StrictJSONError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _error(code: str, message: str) -> Finding:
    return Finding(code, "ERROR", message)


def _warn(code: str, message: str) -> Finding:
    return Finding(code, "WARN", message)


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def _token_estimate(paths: Iterable[Path]) -> int:
    # Deliberately simple and deterministic. This is a budget guard, not a tokenizer.
    total = sum(p.stat().st_size for p in paths if p.is_file())
    return (total + 2) // 3


def structural_findings(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    required = [
        Path("AGENTS.md"), Path("HARNESS_CONTROL.md"), Path("PROJECT_CONTROL.md"),
        STATE,
        CONFIG / "harness-policy.v1.json",
        CONFIG / "risk-policy.v1.json",
        CONFIG / "review-policy.v1.json",
        CONFIG / "instruction-hygiene-policy.v1.json",
        CONFIG / "context-routing.v1.json",
        CONFIG / "rule-registry.v1.json",
        CONFIG / "rule-lifecycle-policy.v1.json",
        CONFIG / "rule-review-state.v1.json",
        CONFIG / "checkpoint-catalog.v1.json",
        CONFIG / "golden-cases.v1.json",
        CONFIG / "continuation-policy.v1.json",
        CONFIG / "trust-providers.v1.json",
        CONFIG / "mission-policy.v1.json",
        CONFIG / "external-attestation.schema.v1.json",
        CONFIG / "semantic-risk-policy.v1.json",
        CONFIG / "acceptance-contract.schema.v1.json",
        CONFIG / "verifier-policy.v1.json",
        CONFIG / "attempt-policy.v1.json",
        CONFIG / "requirements-manifest.schema.v1.json",
        Path("scripts/harness/verifier_runner.py"),
        Path("scripts/harness/verifier_api.py"),
        Path("scripts/harness/rules.py"),
    ]
    for rel in required:
        if not (root / rel).is_file():
            findings.append(_error("REQUIRED_FILE_MISSING", rel.as_posix()))

    # R9: these are executable architecture constraints, not prose-only intent.
    control_src = root / "scripts/harness/control.py"
    if control_src.is_file():
        text = control_src.read_text(encoding="utf-8")
        if re.search(r"^def cmd_final_report\(", text, re.MULTILINE) is None or '"final-report"' not in text:
            findings.append(_error("R9_FINAL_REPORT_COMMAND_MISSING", "machine-derived final-report command is required"))
    test_src = root / "tests/test_harness.py"
    if test_src.is_file():
        text = test_src.read_text(encoding="utf-8")
        if "from selftest import" in text or "import selftest" in text or "run_selftest(" in text:
            findings.append(_error("R9_SELFTEST_NOT_ISOLATED", "mutation selftest must not execute inside ordinary unittest discovery"))
    return findings


def control_findings(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    try:
        policy = load_json(root, CONFIG / "harness-policy.v1.json")
        review = load_json(root, CONFIG / "review-policy.v1.json")
        semantic_policy = load_json(root, CONFIG / "semantic-risk-policy.v1.json")
        verifier_policy = load_json(root, CONFIG / "verifier-policy.v1.json")
    except (FileNotFoundError, json.JSONDecodeError, StrictJSONError) as exc:
        return [_error("CONTROL_JSON_INVALID", str(exc))]

    principles = policy.get("principles", {})
    if principles.get("implementer_cannot_self_accept") is not True:
        findings.append(_error("SELF_ACCEPT_GUARD_MISSING", "implementer_cannot_self_accept must be true"))
    if principles.get("missing_evidence_fails_closed") is not True:
        findings.append(_error("MISSING_EVIDENCE_GUARD_WEAKENED", "missing evidence must fail closed"))
    if principles.get("git_is_durable_memory") is not True or principles.get("chat_not_required_for_resume") is not True:
        findings.append(_error("DURABLE_MEMORY_CONTRACT_MISSING", "resume must not depend on chat"))
    if principles.get("completion_is_derived_not_declared") is not True or principles.get("active_mission_evidence_is_semantically_validated") is not True:
        findings.append(_error("DERIVED_COMPLETION_GUARD_MISSING", "mission completion must be rebuilt from semantic evidence"))
    if principles.get("closure_tail_may_not_change_product") is not True:
        findings.append(_error("CLOSURE_TAIL_GUARD_MISSING", "post-candidate closure tail may not change product"))
    if principles.get("test_evidence_must_be_machine_receipted") is not True:
        findings.append(_error("MACHINE_RECEIPT_GUARD_MISSING", "test evidence must be emitted by command API"))
    r5_guards = {
        "completion_requires_clean_worktree",
        "completion_evidence_must_be_tracked_at_head",
        "evidence_events_are_blob_immutable_after_add",
        "external_trust_must_predate_dispatch",
        "external_claims_require_cryptographic_verification",
        "canonical_integration_lineage_is_machine_proven",
        "derived_lineage_is_not_self_authored",
        "all_product_mutating_commits_match_declared_implementer_identity",
    }
    if any(principles.get(k) is not True for k in r5_guards):
        findings.append(_error("R5_ACCEPTANCE_GUARDS_MISSING", "R5 durability/trust/integration guards must all be enabled"))
    r6_guards = {
        "semantic_risk_floor_is_machine_owned",
        "acceptance_contract_must_precede_implementation",
        "acceptance_contract_is_immutable_after_dispatch",
        "predicate_coverage_is_machine_checked",
        "medium_plus_requires_adversarial_verifier_evidence",
        "implementer_tests_are_not_sufficient_acceptance_for_medium_plus",
        "final_report_is_machine_generated",
        "abandoned_attempts_are_superseded_not_rewritten",
        "mission_specification_must_predate_dispatch",
        "semantic_inference_reads_base_specification",
    }
    if any(principles.get(k) is not True for k in r6_guards):
        findings.append(_error("R6_SEMANTIC_ASSURANCE_GUARDS_MISSING", "R6 semantic risk/acceptance/coverage guards must all be enabled"))
    r7_guards = {
        "normative_requirement_traceability_is_mandatory",
        "semantic_tags_supplement_but_do_not_replace_requirements",
        "external_attestations_are_causally_bound_to_prior_evidence",
        "attestation_evidence_digest_is_verified",
        "same_attempt_id_may_not_be_reused_after_abort",
        "managed_branch_rewrites_are_rejected_when_observable",
    }
    if any(principles.get(k) is not True for k in r7_guards):
        findings.append(_error("R7_TRACEABILITY_CAUSAL_GUARDS_MISSING", "R7 requirement traceability/causal trust/attempt guards must all be enabled"))
    r8_guards = {
        "git_replace_refs_are_forbidden",
        "git_proofs_disable_replace_objects",
        "portable_clean_clone_validation_is_mandatory",
        "immutable_evidence_is_history_immutable",
        "closure_tail_is_checked_per_commit",
        "consumer_events_bind_exact_attestation_object",
        "causal_references_are_content_addressed",
    }
    if any(principles.get(k) is not True for k in r8_guards):
        findings.append(_error("R8_PORTABLE_CAUSAL_HISTORY_GUARDS_MISSING", "R8 portability/history/content-addressing guards must all be enabled"))
    r9_guards = {
        "attestation_wall_clock_claims_are_causally_consistent",
        "normative_markdown_wrapping_does_not_create_fragment_clauses",
        "final_report_values_are_machine_derived",
        "mutation_selftest_is_separate_from_unit_test_discovery",
    }
    if any(principles.get(k) is not True for k in r9_guards):
        findings.append(_error("R9_HARDENING_GUARDS_MISSING", "R9 temporal/report/parser/test-isolation guards must all be enabled"))
    r10_guards = {
        "verifier_partition_coverage_is_case_derived",
        "verifier_cases_bind_exact_executed_test_ids",
        "verifier_receipts_capture_runtime_test_ids",
        "semantic_oracles_are_contract_owned_and_runtime_observed",
        "normative_prose_wrapping_does_not_create_fragment_clauses",
    }
    if any(principles.get(k) is not True for k in r10_guards):
        findings.append(_error("R10_SEMANTIC_PROOF_GUARDS_MISSING", "R10 case-derived coverage/runtime-test/oracle/prose guards must all be enabled"))
    r11_guards = {
        "structured_oracle_observations_required",
        "trivial_oracle_assertions_are_rejected",
        "effective_status_is_machine_derived",
        "routine_retry_is_supersede_plus_new_attempt_not_history_rewrite",
        "session_resume_is_derived_from_durable_state",
        "external_trust_keys_declare_distinct_custody_domains",
    }
    if any(principles.get(k) is not True for k in r11_guards):
        findings.append(_error("R11_EFFICIENCY_INTEGRITY_GUARDS_MISSING", "R11 structured-oracle/retry/resume/derived-state/custody guards must all be enabled"))

    r12_guards = {
        "agent_context_uses_compact_command_api",
        "default_validation_output_is_bounded",
        "raw_machine_output_is_durable_not_echoed",
        "evidence_transitions_support_atomic_commit",
        "completion_findings_are_not_normal_implementation_context",
        "harness_source_is_not_normal_agent_context",
    }
    if any(principles.get(k) is not True for k in r12_guards):
        findings.append(_error("R12_LEAN_CONTEXT_GUARDS_MISSING", "R12 compact-agent/output/atomic-evidence guards must all be enabled"))

    closure = policy.get("closure_tail", {})
    if closure.get("candidate_must_remain_ancestor") is not True or closure.get("product_change_after_candidate_invalidates_review") is not True or closure.get("closure_head_is_derived_from_git") is not True:
        findings.append(_error("CLOSURE_LINEAGE_POLICY_WEAKENED", "candidate/closure lineage invariants incomplete"))
    if closure.get("per_commit_validation_required") is not True or closure.get("revert_does_not_erase_violation") is not True:
        findings.append(_error("R8_TRAJECTORY_GUARD_MISSING", "closure/source path policy must be enforced on every commit, not net diff"))
    if policy.get("autonomy", {}).get("canonical_merge_requires_durable_authorization") is not True:
        findings.append(_error("MERGE_AUTHORIZATION_GUARD_MISSING", "canonical merge authorization must be durable"))

    capacity = policy.get("concurrency", {}).get("runtime_mutation_lease_capacity")
    if capacity != 1:
        findings.append(_error("CONCURRENCY_GUARD_WEAKENED", f"expected mutation lease capacity 1, got {capacity!r}"))

    exact = review.get("exact_head_protocol", {})
    required_exact = (
        exact.get("reviewed_head_must_equal_evidence_head") is True
        and exact.get("reviewed_head_must_equal_tested_head") is True
        and exact.get("change_after_review_makes_review_stale") is True
    )
    if not required_exact:
        findings.append(_error("EXACT_HEAD_GUARD_MISSING", "review/evidence/tested heads must be exact and fresh"))

    sep = review.get("role_separation", {})
    if sep.get("implementer_and_reviewer_distinct") is not True:
        findings.append(_error("REVIEW_ROLE_SEPARATION_MISSING", "implementer and reviewer must be distinct"))
    if sep.get("role_names_are_not_identity_proof") is not True or sep.get("git_identity_is_provenance_not_external_trust") is not True:
        findings.append(_error("ROLE_IDENTITY_ASSURANCE_MISSING", "role labels/Git email are provenance only"))
    assurance = review.get("assurance", {})
    if assurance.get("medium_plus_completion_requires") != "EXTERNAL_VERIFIED" or assurance.get("external_verified_requires_signed_attestation") is not True or assurance.get("trusted_key_must_exist_at_work_order_base") is not True:
        findings.append(_error("REVIEW_ASSURANCE_GUARD_MISSING", "MEDIUM+ needs cryptographically verified base-trusted attestation"))
    if assurance.get("attestation_must_bind_prerequisite_event") is not True or assurance.get("attestation_must_bind_evidence_digest") is not True or assurance.get("pre_signed_approval_forbidden") is not True:
        findings.append(_error("CAUSAL_ATTESTATION_GUARD_MISSING", "external approvals must bind a prior event and exact pre-existing evidence digest"))
    if assurance.get("consumer_event_must_bind_attestation_sha256") is not True or assurance.get("consumer_event_must_bind_attestation_git_blob") is not True or assurance.get("attestation_history_must_be_immutable") is not True:
        findings.append(_error("R8_ATTESTATION_CONSUMER_BINDING_GUARD_MISSING", "consumer must bind exact immutable attestation bytes and Git blob"))
    if (assurance.get("causal_wall_clock_consistency_required") is not True
            or assurance.get("issued_at_must_not_postdate_consumer_beyond_skew") is not True
            or assurance.get("issued_at_must_not_predate_prerequisite_beyond_skew") is not True
            or not isinstance(assurance.get("max_clock_skew_seconds"), int)
            or isinstance(assurance.get("max_clock_skew_seconds"), bool)
            or not (0 <= assurance.get("max_clock_skew_seconds") <= 300)):
        findings.append(_error("R9_TEMPORAL_CAUSAL_GUARD_MISSING", "signed issued_at and event recorded_at claims must respect prerequisite <= issuance <= consumer within bounded skew"))
    if (assurance.get("key_custody_metadata_required") is not True
            or assurance.get("review_and_integration_custody_domains_must_be_distinct") is not True
            or assurance.get("local_seed_possession_is_not_independence_proof") is not True
            or not {"SEPARATE_AGENT", "REMOTE_SIGNER", "HARDWARE"}.issubset(set(assurance.get("allowed_external_custody_classes", [])))):
        findings.append(_error("R11_EXTERNAL_CUSTODY_GUARD_MISSING", "external review/integration keys must declare distinct external custody domains; local key possession alone is not independence proof"))
    semantic = review.get("semantic_assurance", {})
    if semantic.get("acceptance_contract_required") is not True or semantic.get("predicate_to_evidence_coverage_required") is not True or semantic.get("medium_plus_adversarial_verification_required") is not True:
        findings.append(_error("SEMANTIC_REVIEW_ASSURANCE_GUARD_MISSING", "R6 acceptance contract and adversarial predicate coverage are mandatory"))
    if semantic.get("requirements_manifest_required") is not True or semantic.get("every_normative_clause_must_be_traced") is not True or semantic.get("every_requirement_must_reach_verifier_evidence") is not True:
        findings.append(_error("REQUIREMENT_TRACEABILITY_GUARD_MISSING", "R7 normative requirements must trace to verifier evidence"))

    tags = semantic_policy.get("tags", {}) if isinstance(semantic_policy, dict) else {}
    for tag in ("persistence", "transaction", "exactly_once", "conservation", "concurrency"):
        cfg = tags.get(tag, {}) if isinstance(tags, dict) else {}
        if cfg.get("min_risk") not in {"MEDIUM", "HIGH", "CRITICAL"}:
            findings.append(_error("SEMANTIC_RISK_POLICY_WEAKENED", f"{tag} must imply at least MEDIUM"))
    conservation_parts = set((tags.get("conservation", {}) or {}).get("required_partitions", [])) if isinstance(tags, dict) else set()
    if "same_entity" not in conservation_parts or "distinct_entities" not in conservation_parts:
        findings.append(_error("SEMANTIC_PARTITION_POLICY_WEAKENED", "conservation must cover distinct_entities and same_entity"))
    nonneg_parts = set((tags.get("non_negative_domain", {}) or {}).get("required_partitions", [])) if isinstance(tags, dict) else set()
    if not {"negative_initial","negative_persisted","operation_underflow"}.issubset(nonneg_parts):
        findings.append(_error("R7_REQUIREMENT_PARTITION_POLICY_WEAKENED", "non-negative domain must cover negative initial/persisted/underflow partitions"))
    vp = verifier_policy.get("principles", {}) if isinstance(verifier_policy, dict) else {}
    if vp.get("predicate_coverage_is_machine_checked") is not True or vp.get("adversarial_cases_required_for_medium_plus") is not True or vp.get("verifier_manifest_is_append_only") is not True:
        findings.append(_error("VERIFIER_POLICY_WEAKENED", "R6 verifier coverage/adversarial/immutability guards must remain enabled"))
    r10_vp = {
        "covered_partitions_must_be_backed_by_referenced_cases",
        "case_ids_must_bind_exact_runtime_pass_test_ids",
        "verifier_receipt_must_capture_exact_runtime_test_ids",
        "case_oracles_must_match_contract_and_be_runtime_observed",
    }
    if any(vp.get(k) is not True for k in r10_vp):
        findings.append(_error("R10_VERIFIER_POLICY_WEAKENED", "R10 verifier runtime semantic-proof guards must remain enabled"))
    r11_vp = {
        "generic_boolean_oracle_assertions_are_forbidden",
        "oracle_observations_are_structured",
        "trivial_same_expression_or_constant_oracles_are_rejected",
        "oracle_observation_kind_must_match_contract",
    }
    if any(vp.get(k) is not True for k in r11_vp) or verifier_policy.get("execution_schema") != "hybrid_harness.verifier_execution.v2":
        findings.append(_error("R11_VERIFIER_ORACLE_POLICY_WEAKENED", "R11 structured runtime oracle evidence and execution schema v2 are required"))
    if verifier_policy.get("manifest_schema") != "hybrid_harness.verification_manifest.v2":
        findings.append(_error("R10_VERIFIER_MANIFEST_SCHEMA_GUARD_MISSING", "verification manifest v2 is required"))

    forbidden = set(review.get("review_context", {}).get("must_not_include", []))
    expected_forbidden = {"implementer_private_reasoning", "implementer_chat_history", "implementer_self_verdict"}
    if not expected_forbidden.issubset(forbidden):
        findings.append(_error("REVIEW_CONTEXT_NOT_ISOLATED", "fresh reviewer context exclusions are incomplete"))

    if review.get("missing_proof") != "INSUFFICIENT_EVIDENCE":
        findings.append(_error("MISSING_PROOF_VERDICT_UNSAFE", "missing proof must map to INSUFFICIENT_EVIDENCE"))
    return findings



def continuation_findings(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    try:
        policy = load_json(root, CONFIG / "harness-policy.v1.json")
        review = load_json(root, CONFIG / "review-policy.v1.json")
        continuation = load_json(root, CONFIG / "continuation-policy.v1.json")
    except (FileNotFoundError, json.JSONDecodeError, StrictJSONError) as exc:
        return [_error("CONTINUATION_CONFIG_INVALID", str(exc))]
    principles = policy.get("principles", {})
    required = {
        "human_is_not_routine_role_result_courier",
        "mission_remains_open_across_role_boundaries",
        "routine_role_boundary_is_not_human_blocker",
    }
    if any(principles.get(k) is not True for k in required):
        findings.append(_error("CONTINUATION_GUARD_MISSING", "mission/role-boundary invariants must be enabled"))
    durable = review.get("durable_handoff", {})
    if durable.get("chat_is_authority") is not False or durable.get("reviewer_must_persist_own_result") is not True:
        findings.append(_error("DURABLE_REVIEW_HANDOFF_WEAKENED", "reviewer must persist own result; chat is not authority"))
    sinks = set(durable.get("allowed_sinks", []))
    if not {"EXECUTION_LEDGER", "GITHUB_PR_REVIEW"}.issubset(sinks):
        findings.append(_error("DURABLE_REVIEW_SINK_MISSING", "durable review sinks incomplete"))
    cp = continuation.get("principles", {})
    if cp.get("next_actor_and_next_action_required_when_mission_open") is not True:
        findings.append(_error("CONTINUATION_NEXT_STEP_NOT_REQUIRED", "open mission must expose next actor/action"))
    if "CHAT" not in set(continuation.get("forbidden_evidence_sinks", [])):
        findings.append(_error("CHAT_EVIDENCE_SINK_NOT_FORBIDDEN", "chat must not be a durable review sink"))
    return findings


def rule_findings(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    try:
        hygiene = load_json(root, CONFIG / "instruction-hygiene-policy.v1.json")
        registry = load_json(root, CONFIG / "rule-registry.v1.json")
    except (FileNotFoundError, json.JSONDecodeError, StrictJSONError) as exc:
        return [_error("RULE_CONFIG_INVALID", str(exc))]

    protected = set(hygiene.get("protected_rule_classes", []))
    never_auto = hygiene.get("never_auto_delete_rules") is True
    if not never_auto:
        findings.append(_error("AUTO_RULE_DELETION_FORBIDDEN", "rules must never be auto-deleted"))

    seen: set[str] = set()
    required_fields = {"id", "class", "source", "applies_when", "enforcement", "enforced_by", "retirement", "prose_mode"}
    for rule in registry.get("rules", []):
        rid = rule.get("id", "<missing>")
        missing = sorted(required_fields - set(rule))
        if missing:
            findings.append(_error("RULE_LIFECYCLE_INCOMPLETE", f"{rid}: missing {', '.join(missing)}"))
            continue
        if rid in seen:
            findings.append(_error("RULE_ID_DUPLICATE", rid))
        seen.add(rid)
        if not rule.get("source") or not rule.get("applies_when") or not rule.get("retirement"):
            findings.append(_error("RULE_LIFECYCLE_INCOMPLETE", f"{rid}: empty lifecycle field"))
        if rule.get("class") in protected:
            retirement = str(rule.get("retirement", ""))
            if retirement not in {"CONTROL_REVISION_REQUIRED", "ARCHITECTURE_REVISION_REQUIRED", "SECURITY_REVISION_REQUIRED", "SAFETY_REVISION_REQUIRED"}:
                findings.append(_error("PROTECTED_RULE_RETIREMENT_UNSAFE", f"{rid}: {retirement}"))
        if rule.get("enforcement") == "machine" and rule.get("prose_mode") not in {"router_only", "none"}:
            findings.append(_warn("MACHINE_RULE_PROSE_BLOAT", f"{rid}: machine rule should use router_only/none prose"))
    if len(seen) < 5:
        findings.append(_error("RULE_REGISTRY_TOO_SMALL", "template should demonstrate multiple rule classes"))
    return findings


def rule_health_findings(root: Path) -> list[Finding]:
    """R13.2: detect stale, broken and orphaned rules from Git-tracked lifecycle state."""
    return [
        Finding(str(i["code"]), str(i["severity"]), str(i["message"]))
        for i in lifecycle_issues(root)
    ]


def mutable_prose_findings(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    try:
        hygiene = load_json(root, CONFIG / "instruction-hygiene-policy.v1.json")
    except (FileNotFoundError, json.JSONDecodeError, StrictJSONError) as exc:
        return [_error("HYGIENE_POLICY_INVALID", str(exc))]
    patterns = [re.compile(p) for p in hygiene.get("forbidden_mutable_prose_patterns", [])]
    for rel in hygiene.get("root_files_scanned_for_mutable_state", []):
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            m = pattern.search(text)
            if m:
                findings.append(_error("MUTABLE_STATE_IN_PROSE", f"{rel}: matched {m.group(0)!r}"))
                break
    return findings


def context_findings(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    try:
        hygiene = load_json(root, CONFIG / "instruction-hygiene-policy.v1.json")
        routing = load_json(root, CONFIG / "context-routing.v1.json")
    except (FileNotFoundError, json.JSONDecodeError, StrictJSONError) as exc:
        return [_error("CONTEXT_CONFIG_INVALID", str(exc))]
    budgets = hygiene.get("budgets", {})
    agents = root / "AGENTS.md"
    if agents.is_file():
        lines = _line_count(agents)
        if lines > int(budgets.get("root_router_max_lines", 150)):
            findings.append(_error("ROOT_ROUTER_TOO_LONG", f"AGENTS.md has {lines} lines"))
        size = agents.stat().st_size
        if size > int(budgets.get("root_router_max_bytes", 18000)):
            findings.append(_error("ROOT_ROUTER_TOO_HEAVY", f"AGENTS.md has {size} bytes"))

    always_rel = routing.get("always", [])
    always_paths: list[Path] = []
    for rel in always_rel:
        p = root / rel
        if not p.is_file():
            findings.append(_error("CONTEXT_ROUTE_TARGET_MISSING", rel))
        else:
            always_paths.append(p)
    always_tokens = _token_estimate(always_paths)
    if always_tokens > int(budgets.get("always_context_max_estimated_tokens", 5000)):
        findings.append(_error("CONTEXT_BUDGET_EXCEEDED", f"always context ~{always_tokens} tokens"))

    route_limit = int(budgets.get("conditional_route_max_estimated_tokens", 9000))
    for route, rels in routing.get("routes", {}).items():
        route_paths = list(always_paths)
        for rel in rels:
            p = root / rel
            if not p.is_file():
                findings.append(_error("CONTEXT_ROUTE_TARGET_MISSING", f"{route}: {rel}"))
            else:
                route_paths.append(p)
        tokens = _token_estimate(route_paths)
        if tokens > route_limit:
            findings.append(_error("CONDITIONAL_CONTEXT_BUDGET_EXCEEDED", f"{route}: ~{tokens} tokens"))
    return findings


def golden_findings(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    try:
        golden = load_json(root, CONFIG / "golden-cases.v1.json")
    except (FileNotFoundError, json.JSONDecodeError, StrictJSONError) as exc:
        return [_error("GOLDEN_SET_INVALID", str(exc))]
    cases = golden.get("cases", [])
    if golden.get("runs_per_case", 0) < 3:
        findings.append(_error("GOLDEN_RUN_COUNT_TOO_LOW", "runs_per_case must be >= 3"))
    if len(cases) < 8:
        findings.append(_error("GOLDEN_SET_TOO_SMALL", f"only {len(cases)} cases"))
    ids = [c.get("id") for c in cases]
    if len(ids) != len(set(ids)):
        findings.append(_error("GOLDEN_ID_DUPLICATE", "case ids must be unique"))
    categories = {c.get("category") for c in cases}
    if len(categories) < 3:
        findings.append(_error("GOLDEN_DIVERSITY_TOO_LOW", f"only {len(categories)} categories"))
    if "safety" not in categories:
        findings.append(_error("GOLDEN_SAFETY_CASE_MISSING", "at least one safety case required"))
    if sum(1 for c in cases if c.get("difficulty") == 3) < 2:
        findings.append(_error("GOLDEN_HARD_CASES_TOO_FEW", "at least two difficulty=3 cases required"))
    for c in cases:
        if not c.get("prompt") or not c.get("accept") or not c.get("reject") or not c.get("baseline"):
            findings.append(_error("GOLDEN_CASE_INCOMPLETE", str(c.get("id"))))
    return findings


def all_findings(root: Path) -> list[Finding]:
    findings = structural_findings(root)
    if any(f.severity == "ERROR" for f in findings):
        return findings
    for fn in (control_findings, continuation_findings, rule_findings, rule_health_findings, mutable_prose_findings, context_findings, golden_findings):
        findings.extend(fn(root))
    findings.extend(Finding(f.code, "ERROR", f.message) for f in validate_active(root))
    return findings


def summary(root: Path) -> dict:
    routing = load_json(root, CONFIG / "context-routing.v1.json")
    hygiene = load_json(root, CONFIG / "instruction-hygiene-policy.v1.json")
    registry = load_json(root, CONFIG / "rule-registry.v1.json")
    always_paths = [root / p for p in routing.get("always", []) if (root / p).is_file()]
    rule_health = rule_health_report(root)
    return {
        "root_router_lines": _line_count(root / "AGENTS.md"),
        "always_context_estimated_tokens": _token_estimate(always_paths),
        "rule_count": len(registry.get("rules", [])),
        "protected_rule_count": sum(1 for r in registry.get("rules", []) if r.get("class") in set(hygiene.get("protected_rule_classes", []))),
        "rule_health_counts": rule_health.get("counts", {}),
        "routes": sorted(routing.get("routes", {}).keys()),
        "auto_rule_deletion": False,
    }
