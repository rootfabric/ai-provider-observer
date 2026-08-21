from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from audit import all_findings


def codes(root: Path) -> set[str]:
    return {f.code for f in all_findings(root) if f.severity == "ERROR"}


def write_json(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_mutation(source_root: Path, name: str, mutate, expected: str) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory(prefix=f"hybrid-harness-{name}-") as td:
        dst = Path(td) / "repo"
        shutil.copytree(source_root, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        mutate(dst)
        found = codes(dst)
        ok = expected in found
        return ok, f"{name}: expected {expected}; got {sorted(found)}"


def run(source_root: Path) -> tuple[bool, list[str]]:
    out: list[str] = []
    baseline = codes(source_root)
    if baseline:
        return False, [f"baseline must be green, got {sorted(baseline)}"]
    out.append("good-baseline: PASS")

    mutations = []

    def self_accept(r: Path):
        p = r / "config/control/harness/harness-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["principles"]["implementer_cannot_self_accept"] = False; write_json(p, j)
    mutations.append(("bad-self-accept", self_accept, "SELF_ACCEPT_GUARD_MISSING"))

    def stale_head(r: Path):
        p = r / "config/control/harness/review-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["exact_head_protocol"]["reviewed_head_must_equal_tested_head"] = False; write_json(p, j)
    mutations.append(("bad-exact-head", stale_head, "EXACT_HEAD_GUARD_MISSING"))

    def missing_retirement(r: Path):
        p = r / "config/control/harness/rule-registry.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); del j["rules"][4]["retirement"]; write_json(p, j)
    mutations.append(("bad-rule-lifecycle", missing_retirement, "RULE_LIFECYCLE_INCOMPLETE"))

    def unsafe_retirement(r: Path):
        p = r / "config/control/harness/rule-registry.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["rules"][0]["retirement"] = "DELETE_AFTER_30_DAYS"; write_json(p, j)
    mutations.append(("bad-protected-retirement", unsafe_retirement, "PROTECTED_RULE_RETIREMENT_UNSAFE"))

    def stale_prose(r: Path):
        p = r / "HARNESS_CONTROL.md"
        p.write_text(p.read_text(encoding="utf-8") + "\nCurrent checkpoint: H0.1\n", encoding="utf-8")
    mutations.append(("bad-stale-prose", stale_prose, "MUTABLE_STATE_IN_PROSE"))

    def context_bloat(r: Path):
        p = r / "AGENTS.md"
        p.write_text(p.read_text(encoding="utf-8") + ("\n- filler rule that should not be here" * 220), encoding="utf-8")
    mutations.append(("bad-context-bloat", context_bloat, "ROOT_ROUTER_TOO_LONG"))

    def reviewer_leak(r: Path):
        p = r / "config/control/harness/review-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["review_context"]["must_not_include"] = []; write_json(p, j)
    mutations.append(("bad-review-context", reviewer_leak, "REVIEW_CONTEXT_NOT_ISOLATED"))

    def golden_shrink(r: Path):
        p = r / "config/control/harness/golden-cases.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["cases"] = j["cases"][:2]; write_json(p, j)
    mutations.append(("bad-golden-set", golden_shrink, "GOLDEN_SET_TOO_SMALL"))


    def chat_authority(r: Path):
        p = r / "config/control/harness/review-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["durable_handoff"]["chat_is_authority"] = True; write_json(p, j)
    mutations.append(("bad-chat-review-authority", chat_authority, "DURABLE_REVIEW_HANDOFF_WEAKENED"))

    def human_courier(r: Path):
        p = r / "config/control/harness/harness-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["principles"]["human_is_not_routine_role_result_courier"] = False; write_json(p, j)
    mutations.append(("bad-human-courier", human_courier, "CONTINUATION_GUARD_MISSING"))

    def continuation_next_missing(r: Path):
        p = r / "config/control/harness/continuation-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["principles"]["next_actor_and_next_action_required_when_mission_open"] = False; write_json(p, j)
    mutations.append(("bad-lost-next-action", continuation_next_missing, "CONTINUATION_NEXT_STEP_NOT_REQUIRED"))

    def concurrency(r: Path):
        p = r / "config/control/harness/harness-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["concurrency"]["runtime_mutation_lease_capacity"] = 2; write_json(p, j)
    mutations.append(("bad-concurrency", concurrency, "CONCURRENCY_GUARD_WEAKENED"))

    def declared_completion(r: Path):
        p = r / "config/control/harness/harness-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["principles"]["completion_is_derived_not_declared"] = False; write_json(p, j)
    mutations.append(("bad-declared-completion", declared_completion, "DERIVED_COMPLETION_GUARD_MISSING"))

    def closure_product_change(r: Path):
        p = r / "config/control/harness/harness-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["closure_tail"]["product_change_after_candidate_invalidates_review"] = False; write_json(p, j)
    mutations.append(("bad-closure-lineage", closure_product_change, "CLOSURE_LINEAGE_POLICY_WEAKENED"))

    def machine_receipt_off(r: Path):
        p = r / "config/control/harness/harness-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["principles"]["test_evidence_must_be_machine_receipted"] = False; write_json(p, j)
    mutations.append(("bad-machine-receipt", machine_receipt_off, "MACHINE_RECEIPT_GUARD_MISSING"))

    def role_identity_weak(r: Path):
        p = r / "config/control/harness/review-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["role_separation"]["role_names_are_not_identity_proof"] = False; write_json(p, j)
    mutations.append(("bad-role-identity", role_identity_weak, "ROLE_IDENTITY_ASSURANCE_MISSING"))

    def merge_auth_off(r: Path):
        p = r / "config/control/harness/harness-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["autonomy"]["canonical_merge_requires_durable_authorization"] = False; write_json(p, j)
    mutations.append(("bad-merge-authorization", merge_auth_off, "MERGE_AUTHORIZATION_GUARD_MISSING"))


    def weaken_r5_guards(r: Path):
        p = r / "config/control/harness/harness-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["principles"]["external_claims_require_cryptographic_verification"] = False; write_json(p, j)
    mutations.append(("bad-cryptographic-external-proof", weaken_r5_guards, "R5_ACCEPTANCE_GUARDS_MISSING"))

    def weaken_signed_attestation(r: Path):
        p = r / "config/control/harness/review-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["assurance"]["external_verified_requires_signed_attestation"] = False; write_json(p, j)
    mutations.append(("bad-signed-attestation", weaken_signed_attestation, "REVIEW_ASSURANCE_GUARD_MISSING"))

    def remove_trust_registry(r: Path):
        (r / "config/control/harness/trust-providers.v1.json").unlink()
    mutations.append(("bad-missing-trust-registry", remove_trust_registry, "REQUIRED_FILE_MISSING"))


    def weaken_semantic_risk(r: Path):
        p = r / "config/control/harness/semantic-risk-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["tags"]["persistence"]["min_risk"] = "LOW"; write_json(p, j)
    mutations.append(("bad-semantic-risk-floor", weaken_semantic_risk, "SEMANTIC_RISK_POLICY_WEAKENED"))

    def remove_same_entity_partition(r: Path):
        p = r / "config/control/harness/semantic-risk-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["tags"]["conservation"]["required_partitions"] = ["distinct_entities", "boundary_amount"]; write_json(p, j)
    mutations.append(("bad-conservation-domain", remove_same_entity_partition, "SEMANTIC_PARTITION_POLICY_WEAKENED"))

    def weaken_verifier_policy(r: Path):
        p = r / "config/control/harness/verifier-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["principles"]["adversarial_cases_required_for_medium_plus"] = False; write_json(p, j)
    mutations.append(("bad-adversarial-verifier", weaken_verifier_policy, "VERIFIER_POLICY_WEAKENED"))

    def weaken_spec_capture(r: Path):
        p = r / "config/control/harness/harness-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["principles"]["mission_specification_must_predate_dispatch"] = False; write_json(p, j)
    mutations.append(("bad-mission-spec-capture", weaken_spec_capture, "R6_SEMANTIC_ASSURANCE_GUARDS_MISSING"))

    def weaken_requirement_traceability(r: Path):
        p = r / "config/control/harness/harness-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["principles"]["normative_requirement_traceability_is_mandatory"] = False; write_json(p, j)
    mutations.append(("bad-requirement-traceability", weaken_requirement_traceability, "R7_TRACEABILITY_CAUSAL_GUARDS_MISSING"))

    def weaken_causal_attestation(r: Path):
        p = r / "config/control/harness/review-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["assurance"]["pre_signed_approval_forbidden"] = False; write_json(p, j)
    mutations.append(("bad-causal-attestation", weaken_causal_attestation, "CAUSAL_ATTESTATION_GUARD_MISSING"))

    def weaken_requirement_review(r: Path):
        p = r / "config/control/harness/review-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["semantic_assurance"]["every_normative_clause_must_be_traced"] = False; write_json(p, j)
    mutations.append(("bad-requirement-review", weaken_requirement_review, "REQUIREMENT_TRACEABILITY_GUARD_MISSING"))

    def weaken_nonnegative_domain(r: Path):
        p = r / "config/control/harness/semantic-risk-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["tags"]["non_negative_domain"]["required_partitions"] = ["positive_initial","zero_initial"]; write_json(p, j)
    mutations.append(("bad-nonnegative-domain", weaken_nonnegative_domain, "R7_REQUIREMENT_PARTITION_POLICY_WEAKENED"))

    def weaken_r8_portability(r: Path):
        p = r / "config/control/harness/harness-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["principles"]["git_replace_refs_are_forbidden"] = False; write_json(p, j)
    mutations.append(("bad-r8-replace-policy", weaken_r8_portability, "R8_PORTABLE_CAUSAL_HISTORY_GUARDS_MISSING"))

    def weaken_r8_trajectory(r: Path):
        p = r / "config/control/harness/harness-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["closure_tail"]["per_commit_validation_required"] = False; write_json(p, j)
    mutations.append(("bad-r8-trajectory", weaken_r8_trajectory, "R8_TRAJECTORY_GUARD_MISSING"))

    def weaken_r8_consumer_binding(r: Path):
        p = r / "config/control/harness/review-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["assurance"]["consumer_event_must_bind_attestation_sha256"] = False; write_json(p, j)
    mutations.append(("bad-r8-consumer-binding", weaken_r8_consumer_binding, "R8_ATTESTATION_CONSUMER_BINDING_GUARD_MISSING"))

    def weaken_r9_temporal(r: Path):
        p = r / "config/control/harness/review-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["assurance"]["causal_wall_clock_consistency_required"] = False; write_json(p, j)
    mutations.append(("bad-r9-temporal-causality", weaken_r9_temporal, "R9_TEMPORAL_CAUSAL_GUARD_MISSING"))

    def weaken_r9_parser(r: Path):
        p = r / "config/control/harness/harness-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["principles"]["normative_markdown_wrapping_does_not_create_fragment_clauses"] = False; write_json(p, j)
    mutations.append(("bad-r9-bullet-parser", weaken_r9_parser, "R9_HARDENING_GUARDS_MISSING"))

    def break_r9_final_report(r: Path):
        p = r / "scripts/harness/control.py"
        p.write_text(p.read_text(encoding="utf-8").replace("def cmd_final_report", "def cmd_final_report_removed", 1), encoding="utf-8")
    mutations.append(("bad-r9-final-report", break_r9_final_report, "R9_FINAL_REPORT_COMMAND_MISSING"))

    def break_r9_test_isolation(r: Path):
        p = r / "tests/test_harness.py"
        p.write_text(p.read_text(encoding="utf-8") + "\nfrom selftest import run as run_selftest\n", encoding="utf-8")
    mutations.append(("bad-r9-selftest-isolation", break_r9_test_isolation, "R9_SELFTEST_NOT_ISOLATED"))

    def weaken_r10_case_coverage(r: Path):
        p = r / "config/control/harness/harness-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["principles"]["verifier_partition_coverage_is_case_derived"] = False; write_json(p, j)
    mutations.append(("bad-r10-case-derived-coverage", weaken_r10_case_coverage, "R10_SEMANTIC_PROOF_GUARDS_MISSING"))

    def weaken_r10_verifier_runtime(r: Path):
        p = r / "config/control/harness/verifier-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["principles"]["case_ids_must_bind_exact_runtime_pass_test_ids"] = False; write_json(p, j)
    mutations.append(("bad-r10-runtime-test-binding", weaken_r10_verifier_runtime, "R10_VERIFIER_POLICY_WEAKENED"))

    def remove_r10_runner(r: Path):
        (r / "scripts/harness/verifier_runner.py").unlink()
    mutations.append(("bad-r10-missing-verifier-runner", remove_r10_runner, "REQUIRED_FILE_MISSING"))

    def weaken_r11_structured_oracle(r: Path):
        p = r / "config/control/harness/harness-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["principles"]["structured_oracle_observations_required"] = False; write_json(p, j)
    mutations.append(("bad-r11-structured-oracle", weaken_r11_structured_oracle, "R11_EFFICIENCY_INTEGRITY_GUARDS_MISSING"))

    def weaken_r11_verifier_oracle_policy(r: Path):
        p = r / "config/control/harness/verifier-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["principles"]["generic_boolean_oracle_assertions_are_forbidden"] = False; write_json(p, j)
    mutations.append(("bad-r11-verifier-oracle-policy", weaken_r11_verifier_oracle_policy, "R11_VERIFIER_ORACLE_POLICY_WEAKENED"))

    def weaken_r11_custody(r: Path):
        p = r / "config/control/harness/review-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["assurance"]["review_and_integration_custody_domains_must_be_distinct"] = False; write_json(p, j)
    mutations.append(("bad-r11-external-custody", weaken_r11_custody, "R11_EXTERNAL_CUSTODY_GUARD_MISSING"))

    def weaken_r11_retry(r: Path):
        p = r / "config/control/harness/harness-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["principles"]["routine_retry_is_supersede_plus_new_attempt_not_history_rewrite"] = False; write_json(p, j)
    mutations.append(("bad-r11-retry-policy", weaken_r11_retry, "R11_EFFICIENCY_INTEGRITY_GUARDS_MISSING"))

    def weaken_r12_lean_context(r: Path):
        p = r / "config/control/harness/harness-policy.v1.json"
        j = json.loads(p.read_text(encoding="utf-8")); j["principles"]["default_validation_output_is_bounded"] = False; write_json(p, j)
    mutations.append(("bad-r12-lean-context", weaken_r12_lean_context, "R12_LEAN_CONTEXT_GUARDS_MISSING"))

    for name, mutate, expected in mutations:
        ok, detail = run_mutation(source_root, name, mutate, expected)
        out.append(f"{name}: {'PASS' if ok else 'FAIL'}")
        if not ok:
            out.append(detail)
            return False, out
    return True, out
