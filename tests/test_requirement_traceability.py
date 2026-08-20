from __future__ import annotations

import base64
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/harness"))

from requirements import extract_normative_clauses, validate_requirements_manifest, requirement_coverage_errors
from strictjson import load
from trust import canonical_attestation_payload
from ed25519 import sign
from active_validation import validate_active
from semantic import infer_semantic_tags
try:
    from tests.test_hardening import ActiveFixture, write_json, run
except ModuleNotFoundError:
    from test_hardening import ActiveFixture, write_json, run


class RequirementTraceabilityTests(unittest.TestCase):
    def setUp(self):
        self.semantic = load(ROOT / "config/control/harness/semantic-risk-policy.v1.json")

    def test_no_negative_balance_is_machine_extracted_as_normative(self):
        spec = "# Mandatory rules\n- No account balance may become negative.\n"
        clauses = extract_normative_clauses(spec)
        self.assertEqual(1, len(clauses))
        self.assertIn("No account balance may become negative", clauses[0].text)

    def test_unmapped_normative_clause_is_rejected(self):
        spec = "# Requirements\n- No account balance may become negative.\n"
        clause = extract_normative_clauses(spec)[0]
        acceptance = {"predicates":[{"predicate_id":"P1","requirement_ids":["REQ1"],"partitions":["positive_initial"]}]}
        manifest = {
            "schema":"hybrid_harness.requirements_manifest.v1","manifest_id":"RM1","mission_id":"M","work_order_id":"W",
            "specification":"config/control/specifications/M.md","specification_sha256":hashlib.sha256(spec.encode()).hexdigest(),
            "requirements":[{
                "requirement_id":"REQ1","statement":"Some other requirement","source_clause_ids":[],"class":"VALIDATION",
                "semantic_tags":[],"required_partitions":["nominal"],"predicate_ids":["P1"]
            }]
        }
        errors,_ = validate_requirements_manifest(manifest,specification_text=spec,specification_rel="config/control/specifications/M.md",specification_sha256=hashlib.sha256(spec.encode()).hexdigest(),mission_id="M",work_order_id="W",acceptance=acceptance,semantic_policy=self.semantic)
        self.assertTrue(any(e.startswith("NORMATIVE_CLAUSE_UNMAPPED:") for e in errors))
        self.assertTrue(any(e.startswith("REQUIREMENT_SOURCE_CLAUSE_MISSING:") for e in errors))
        self.assertTrue(clause.clause_id in "\n".join(errors))

    def test_negative_balance_requirement_forces_domain_partitions(self):
        spec = "# Requirements\n- No account balance may become negative.\n"
        clause = extract_normative_clauses(spec)[0]
        acceptance = {"predicates":[{"predicate_id":"P-BAL","requirement_ids":["REQ-BAL"],"partitions":["positive_initial","zero_initial"]}]}
        manifest = {
            "schema":"hybrid_harness.requirements_manifest.v1","manifest_id":"RM1","mission_id":"M","work_order_id":"W",
            "specification":"config/control/specifications/M.md","specification_sha256":hashlib.sha256(spec.encode()).hexdigest(),
            "requirements":[{
                "requirement_id":"REQ-BAL","statement":"No account balance may become negative.","source_clause_ids":[clause.clause_id],"class":"INVARIANT",
                "semantic_tags":["non_negative_domain"],"required_partitions":["positive_initial","zero_initial"],"predicate_ids":["P-BAL"]
            }]
        }
        errors,_ = validate_requirements_manifest(manifest,specification_text=spec,specification_rel="config/control/specifications/M.md",specification_sha256=hashlib.sha256(spec.encode()).hexdigest(),mission_id="M",work_order_id="W",acceptance=acceptance,semantic_policy=self.semantic)
        self.assertTrue(any("negative_initial" in e for e in errors))
        self.assertTrue(any("negative_persisted" in e for e in errors))
        self.assertTrue(any("operation_underflow" in e for e in errors))

    def test_requirement_must_reach_verifier_coverage(self):
        manifest={"requirements":[{"requirement_id":"REQ1","predicate_ids":["P1"]}]}
        acceptance={"predicates":[{"predicate_id":"P1","requirement_ids":["REQ1"]}]}
        verifier={"predicate_coverage":[]}
        self.assertIn("REQUIREMENT_PREDICATE_UNVERIFIED:REQ1:P1", requirement_coverage_errors(manifest,acceptance,verifier))

    def test_review_attestation_cannot_be_bound_to_same_commit_review_event(self):
        with tempfile.TemporaryDirectory() as td:
            fx=ActiveFixture(Path(td))
            p=fx.root/'evidence/attestations/review.json'
            att=json.loads(p.read_text())
            att['prerequisite_event']='evidence/events/0002-review.json'
            att['prerequisite_event_sha256']=hashlib.sha256((fx.root/'evidence/events/0002-review.json').read_bytes()).hexdigest()
            att['signature_b64']=base64.b64encode(sign(fx.REVIEW_SEED,canonical_attestation_payload(att))).decode()
            write_json(p,att)
            run(fx.root,'add',str(p.relative_to(fx.root))); run(fx.root,'commit','-m','tamper review attestation after consumption')
            codes={f.code for f in validate_active(fx.root,require_completion=True)}
            self.assertIn('EXTERNAL_ATTESTATION_MUTATED_AFTER_ADD',codes)

    def test_integration_attestation_cannot_predate_director_readiness(self):
        with tempfile.TemporaryDirectory() as td:
            fx=ActiveFixture(Path(td))
            p=fx.root/'evidence/attestations/integration.json'
            att=json.loads(p.read_text())
            # Point to the authorization event that shares the attestation add commit.
            att['prerequisite_event']='evidence/events/0005-integration-authorization.json'
            att['prerequisite_event_sha256']=hashlib.sha256((fx.root/'evidence/events/0005-integration-authorization.json').read_bytes()).hexdigest()
            att['signature_b64']=base64.b64encode(sign(fx.INTEGRATION_SEED,canonical_attestation_payload(att))).decode()
            write_json(p,att)
            run(fx.root,'add',str(p.relative_to(fx.root))); run(fx.root,'commit','-m','tamper integration attestation after consumption')
            codes={f.code for f in validate_active(fx.root,require_completion=True)}
            self.assertIn('EXTERNAL_ATTESTATION_MUTATED_AFTER_ADD',codes)

    def test_attestation_evidence_digest_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            fx=ActiveFixture(Path(td))
            p=fx.root/'evidence/attestations/review.json'
            att=json.loads(p.read_text()); att['evidence_digest']='0'*64
            att['signature_b64']=base64.b64encode(sign(fx.REVIEW_SEED,canonical_attestation_payload(att))).decode()
            write_json(p,att)
            run(fx.root,'add',str(p.relative_to(fx.root))); run(fx.root,'commit','-m','tamper review evidence digest after consumption')
            codes={f.code for f in validate_active(fx.root,require_completion=True)}
            self.assertIn('EXTERNAL_ATTESTATION_MUTATED_AFTER_ADD',codes)

    def test_semantic_trigger_respects_word_boundary(self):
        policy={"tags":{"concurrency":{"triggers":["race"]}}}
        tags=infer_semantic_tags(policy,{"mission":{"success_condition":"requirement traceability"}},{},"")
        self.assertNotIn("concurrency",tags)
        tags2=infer_semantic_tags(policy,{"mission":{"success_condition":"race condition must be handled"}},{},"")
        self.assertIn("concurrency",tags2)

    def test_implementation_branch_reset_is_detected_when_reflog_exists(self):
        from test_hardening import run
        with tempfile.TemporaryDirectory() as td:
            fx=ActiveFixture(Path(td), risk='HIGH')
            run(fx.root,'checkout','feature/test')
            run(fx.root,'reset','--hard',fx.candidate)
            run(fx.root,'checkout','main')
            codes={f.code for f in validate_active(fx.root,require_completion=True)}
            self.assertIn('ACTIVE_ATTEMPT_HISTORY_REWRITE_DETECTED',codes)


if __name__ == '__main__': unittest.main()
