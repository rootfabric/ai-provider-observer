from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
import tempfile
import shutil
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/harness"))

from active_validation import validate_active
from continuation import build_continuation
from ed25519 import public_key_from_seed, sign
from evidence import write_candidate_lock, write_event
from strictjson import StrictJSONError, loads
from trust import canonical_attestation_payload, evidence_digest_at_commit
from requirements import extract_normative_clauses


def run(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.strip()


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def receipt(subject: str, rid: str, out_rel: str, output: bytes, *, execution_head: str | None = None, input_files: list[dict] | None = None, command: list[str] | None = None, verifier_result: dict | None = None) -> dict:
    obj = {
        "schema":"hybrid_harness.evidence_receipt.v5", "receipt_id":rid, "subject_head":subject, "execution_head":execution_head or subject,
        "command":command or ["python3","-m","unittest"], "cwd":".",
        "environment_mode":"SANITIZED_PLUS_DECLARED_OVERRIDES", "environment_overrides":{}, "base_environment":{},
        "resolved_executable":"python3", "python_version":sys.version.split()[0], "clean_subject_tree":True,
        "started_at_utc":now_utc(), "finished_at_utc":now_utc(), "exit_code":0,
        "output_path":out_rel, "output_sha256":hashlib.sha256(output).hexdigest(), "input_files": input_files or [], "runner":"HARNESS_COMMAND_API_R10"
    }
    if verifier_result is not None:
        obj["verifier_result"] = verifier_result
    return obj


def signed_attestation(seed: bytes, *, root: Path, attestation_id: str, key_id: str, principal: str, purpose: str, subject: str, prerequisite_event: str, evidence_paths: list[str]) -> dict:
    parent = run(root, "rev-parse", "HEAD")
    prereq = root / prerequisite_event
    digest = evidence_digest_at_commit(root, parent, evidence_paths)
    if digest is None:
        raise AssertionError(f"fixture evidence missing at {parent}: {evidence_paths}")
    att = {
        "schema":"hybrid_harness.external_attestation.v2",
        "attestation_id":attestation_id,
        "provider_id":"TEST_EXTERNAL",
        "key_id":key_id,
        "principal":principal,
        "purpose":purpose,
        "subject_head":subject,
        "mission_id":"M1",
        "work_order_id":"WO1",
        "decision":"PASS" if purpose == "REVIEW_PASS" else "APPROVE",
        "issued_at_utc":now_utc(),
        "prerequisite_event":prerequisite_event,
        "prerequisite_event_sha256":sha(prereq),
        "evidence_paths":sorted(set(evidence_paths)),
        "evidence_digest":digest,
    }
    att["signature_b64"] = base64.b64encode(sign(seed, canonical_attestation_payload(att))).decode("ascii")
    return att



_FIXTURE_CACHE_DIR = tempfile.TemporaryDirectory(prefix="hh-r7-fixture-cache-")
_FIXTURE_CACHE: dict[str, tuple[Path, dict[str, str]]] = {}

class ActiveFixture:
    REVIEW_SEED = bytes.fromhex("11" * 32)
    INTEGRATION_SEED = bytes.fromhex("22" * 32)

    def __init__(self, root: Path, *, risk: str = "MEDIUM"):
        self.root = root
        cache = _FIXTURE_CACHE.get(risk)
        if cache is not None:
            cached_repo, meta = cache
            subprocess.run(["git","clone","-q",str(cached_repo),str(root)],check=True)
            run(root,"config","user.name","Mutation Test")
            run(root,"config","user.email","mutation@example.test")
            for k,v in meta.items(): setattr(self,k,v)
            return
        run(root, "init", "-b", "main")
        run(root, "config", "user.name", "Implementer")
        run(root, "config", "user.email", "impl@example.test")

        closure = ["evidence/**", "config/control/project-state.v1.json", "config/control/missions/**"]
        write_json(root / "config/control/harness/harness-policy.v1.json", {
            "canonical_branch":"main",
            "closure_tail":{"allowed_paths":closure},
            "integration":{"whitelists":{"LOCAL_SANDBOX_LOW_RISK":{"max_risk":"LOW"}}}
        })
        for rel in ("semantic-risk-policy.v1.json", "acceptance-contract.schema.v1.json", "review-policy.v1.json"):
            write_json(root / f"config/control/harness/{rel}", json.loads((ROOT / f"config/control/harness/{rel}").read_text(encoding="utf-8")))
        write_json(root / "config/control/harness/trust-providers.v1.json", {
            "schema":"hybrid_harness.trust_providers.v1",
            "providers":{
                "TEST_EXTERNAL":{
                    "type":"ED25519_V1", "enabled":True,
                    "keys":[
                        {"key_id":"review-key","principal":"external-reviewer","public_key_b64":base64.b64encode(public_key_from_seed(self.REVIEW_SEED)).decode(),"allowed_purposes":["REVIEW_PASS"]},
                        {"key_id":"integration-key","principal":"external-integrator","public_key_b64":base64.b64encode(public_key_from_seed(self.INTEGRATION_SEED)).decode(),"allowed_purposes":["INTEGRATION_APPROVE"]}
                    ]
                }
            }
        })
        (root / "src").mkdir(parents=True)
        (root / "src/x.py").write_text("VALUE = 0\n", encoding="utf-8")
        spec = root / "config/control/specifications/M1.md"
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text("# Requirements\n- Implement an atomic persistent transaction fixture.\n- Persistent state must survive restart.\n- Write or replace failure must leave state coherent.\n", encoding="utf-8")
        state = {
            "schema":"hybrid_harness.project_state.v7", "canonical_branch":"main", "state_revision":1,
            "active_checkpoint":None, "active_epoch":"E1", "active_work_order":None,
            "active_mission":None, "mutation_lease":None, "status":"TEMPLATE_IDLE"
        }
        write_json(root / "config/control/project-state.v1.json", state)
        run(root, "add", "."); run(root, "commit", "-m", "baseline")
        self.base = run(root, "rev-parse", "HEAD")

        wo = {
            "work_order_id":"WO1", "attempt_id":"ATTEMPT-001", "risk":risk, "risk_reasons":["machine semantic floor + independent exact-head review"],
            "base_sha":self.base, "branch":"feature/test", "started_at_utc":now_utc(),
            "implementer_actor_id":"impl-session", "implementer_git_email":"impl@example.test",
            "allowed_paths":["src/**","tests/product/**","evidence/**","config/control/project-state.v1.json","config/control/missions/**"],
            "implementation_paths":["src/**","tests/product/**"],
            "forbidden_paths":["config/control/harness/**"],
            "required_evidence":["tests","exact_head_review","durable_review_pass","verification"],
            "stop_conditions":["scope_expansion","trust_root_missing"],
            "mission":{"mission_id":"M1","success_condition":"atomic transaction fixture with failure handling"},
            "acceptance_contract":"config/control/acceptance/WO1.json",
            "requirements_manifest":"config/control/requirements/WO1.json",
            "specification":"config/control/specifications/M1.md",
            "handoff":{"next_actor":"REVIEWER","next_action":"SIGNED_REVIEW","evidence_sink":"SIGNED_ATTESTATION","resume_condition":"PASS","on_success":"VERIFY","on_failure":"REPAIR"},
            "integration":{"required":True,"target_branch":"main"}
        }
        acceptance = {
            "schema":"hybrid_harness.acceptance_contract.v1", "contract_id":"AC-WO1",
            "mission_id":"M1", "work_order_id":"WO1", "semantic_tags":["transaction","persistence"],
            "predicates":[
            {
                "predicate_id":"P-TX-ATOMIC", "statement":"Transaction remains atomic for success, rejection, and failure",
                "requirement_ids":["REQ-IMPLEMENT","REQ-FAILURE"],
                "class":"ATOMICITY", "semantic_tags":["transaction"],
                "partitions":["success","rejection","failure_atomicity"],
                "partition_oracles":{
                    "success":[{"oracle_id":"O-TX-SUCCESS","statement":"Successful transaction commits atomically."}],
                    "rejection":[{"oracle_id":"O-TX-REJECT","statement":"Rejected transaction leaves state unchanged."}],
                    "failure_atomicity":[{"oracle_id":"O-TX-FAIL","statement":"Injected persistence failure leaves state coherent."}]
                },
                "required_evidence":["VERIFIER_TEST","FAULT_INJECTION"], "verifier_owned":True
            },
            {
                "predicate_id":"P-PERSIST", "statement":"Persistent state survives restart and remains coherent across write and replace failures",
                "requirement_ids":["REQ-IMPLEMENT","REQ-PERSIST","REQ-FAILURE"],
                "class":"PERSISTENCE", "semantic_tags":["persistence"],
                "partitions":["restart","write_failure","replace_failure"],
                "partition_oracles":{
                    "restart":[{"oracle_id":"O-P-RESTART","statement":"State survives restart."}],
                    "write_failure":[{"oracle_id":"O-P-WRITE","statement":"Write failure preserves coherent state."}],
                    "replace_failure":[{"oracle_id":"O-P-REPLACE","statement":"Replace failure preserves coherent state."}]
                },
                "required_evidence":["VERIFIER_TEST","FAULT_INJECTION"], "verifier_owned":True
            }],
            "completion_rule":"Every predicate partition has durable verifier-owned machine evidence."
        }
        clauses = extract_normative_clauses(spec.read_text(encoding="utf-8"))
        clause_by_text = {c.text:c.clause_id for c in clauses}
        requirements = {
            "schema":"hybrid_harness.requirements_manifest.v1", "manifest_id":"RM-WO1",
            "mission_id":"M1", "work_order_id":"WO1", "specification":"config/control/specifications/M1.md",
            "specification_sha256":hashlib.sha256(spec.read_bytes()).hexdigest(),
            "requirements":[
                {"requirement_id":"REQ-IMPLEMENT","statement":"Implement an atomic persistent transaction fixture.","source_clause_ids":[clause_by_text["Implement an atomic persistent transaction fixture."]],"class":"ATOMICITY","semantic_tags":["transaction","persistence"],"required_partitions":["success","rejection","failure_atomicity","restart","write_failure","replace_failure"],"predicate_ids":["P-TX-ATOMIC","P-PERSIST"]},
                {"requirement_id":"REQ-PERSIST","statement":"Persistent state must survive restart.","source_clause_ids":[clause_by_text["Persistent state must survive restart."]],"class":"PERSISTENCE","semantic_tags":["persistence"],"required_partitions":["restart","write_failure","replace_failure"],"predicate_ids":["P-PERSIST"]},
                {"requirement_id":"REQ-FAILURE","statement":"Write or replace failure must leave state coherent.","source_clause_ids":[clause_by_text["Write or replace failure must leave state coherent."]],"class":"ATOMICITY","semantic_tags":["transaction","persistence"],"required_partitions":["success","rejection","failure_atomicity","restart","write_failure","replace_failure"],"predicate_ids":["P-TX-ATOMIC","P-PERSIST"]}
            ]
        }
        write_json(root / "config/control/acceptance/WO1.json", acceptance)
        write_json(root / "config/control/requirements/WO1.json", requirements)
        write_json(root / "config/control/missions/WO1.json", wo)
        state.update({
            "state_revision":2, "active_work_order":"WO1",
            "active_mission":{"mission_id":"M1","complete":False,"branch":"feature/test","candidate_head":None},
            "mutation_lease":{"holder":"impl-session"}, "status":"MISSION_OPEN"
        })
        write_json(root / "config/control/project-state.v1.json", state)
        run(root, "add", "config/control/missions/WO1.json", "config/control/acceptance/WO1.json", "config/control/requirements/WO1.json", "config/control/project-state.v1.json")
        run(root, "commit", "-m", "dispatch mission")
        self.dispatch = run(root, "rev-parse", "HEAD")

        run(root, "checkout", "-b", "feature/test")
        (root / "src/x.py").write_text("VALUE = 1\n", encoding="utf-8")
        run(root, "add", "src/x.py"); run(root, "commit", "-m", "implement candidate")
        self.candidate = run(root, "rev-parse", "HEAD")

        # Freeze + candidate receipt + review request.
        write_candidate_lock(root, closure)
        out = root / "evidence/raw/product.log"; out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"OK\n")
        write_json(root / "evidence/receipts/product.json", receipt(self.candidate, "product", "evidence/raw/product.log", b"OK\n"))
        write_event(root, "REVIEW_REQUEST", "impl-session", "REQUESTED")
        run(root, "add", "evidence"); run(root, "commit", "-m", "freeze candidate and request review")

        # External review attestation existed outside Git first; reviewer persists it.
        review_paths = ["config/control/specifications/M1.md","config/control/requirements/WO1.json","config/control/acceptance/WO1.json","evidence/candidate-lock.v1.json","evidence/receipts/product.json"]
        review_att = signed_attestation(self.REVIEW_SEED, root=root, attestation_id="review-1", key_id="review-key", principal="external-reviewer", purpose="REVIEW_PASS", subject=self.candidate, prerequisite_event="evidence/events/0001-review-request.json", evidence_paths=review_paths)
        write_json(root / "evidence/attestations/review.json", review_att)
        run(root, "config", "user.name", "Reviewer"); run(root, "config", "user.email", "review@example.test")
        write_event(root, "REVIEW", "review-session", "PASS", {"assurance":{"class":"EXTERNAL_VERIFIED","attestation":"evidence/attestations/review.json"}})
        run(root, "add", "evidence/attestations/review.json", "evidence/events"); run(root, "commit", "-m", "persist external review")

        run(root, "config", "user.name", "Verifier"); run(root, "config", "user.email", "verify@example.test")
        verifier_script = root / "evidence/verifier/adversarial_cases.py"
        verifier_script.parent.mkdir(parents=True, exist_ok=True)
        verifier_script.write_text("# verifier-owned adversarial fixture\n", encoding="utf-8")
        runtime_rows = [
            {"test_id":"adversarial_cases.FixtureVerifier.test_success","case_id":"V-SUCCESS","partitions":["success"],"oracle_ids":["O-TX-SUCCESS"],"observed_oracle_ids":["O-TX-SUCCESS"],"status":"PASS"},
            {"test_id":"adversarial_cases.FixtureVerifier.test_reject","case_id":"V-REJECT","partitions":["rejection"],"oracle_ids":["O-TX-REJECT"],"observed_oracle_ids":["O-TX-REJECT"],"status":"PASS"},
            {"test_id":"adversarial_cases.FixtureVerifier.test_failure","case_id":"V-FAIL","partitions":["failure_atomicity"],"oracle_ids":["O-TX-FAIL"],"observed_oracle_ids":["O-TX-FAIL"],"status":"PASS"},
            {"test_id":"adversarial_cases.FixtureVerifier.test_restart","case_id":"V-RESTART","partitions":["restart"],"oracle_ids":["O-P-RESTART"],"observed_oracle_ids":["O-P-RESTART"],"status":"PASS"},
            {"test_id":"adversarial_cases.FixtureVerifier.test_write_failure","case_id":"V-WRITE-FAIL","partitions":["write_failure"],"oracle_ids":["O-P-WRITE"],"observed_oracle_ids":["O-P-WRITE"],"status":"PASS"},
            {"test_id":"adversarial_cases.FixtureVerifier.test_replace_failure","case_id":"V-REPLACE-FAIL","partitions":["replace_failure"],"oracle_ids":["O-P-REPLACE"],"observed_oracle_ids":["O-P-REPLACE"],"status":"PASS"},
        ]
        verifier_result = {
            "schema":"hybrid_harness.verifier_execution.v1", "start_dir":"evidence/verifier", "pattern":"test_*.py",
            "tests":runtime_rows, "executed_test_ids":[r["test_id"] for r in runtime_rows],
            "passed_test_ids":[r["test_id"] for r in runtime_rows], "failed_test_ids":[], "skipped_test_ids":[]
        }
        marker = ("HARNESS_VERIFIER_RESULT_JSON=" + json.dumps(verifier_result, sort_keys=True, separators=(",", ":")) + "\n").encode()
        verifier_out = root / "evidence/raw/verifier.log"; verifier_out.write_bytes(marker)
        current_exec = run(root, "rev-parse", "HEAD")
        write_json(root / "evidence/receipts/verifier.json", receipt(
            self.candidate, "verifier", "evidence/raw/verifier.log", marker,
            execution_head=current_exec, input_files=[{"path":"evidence/verifier/adversarial_cases.py","sha256":sha(verifier_script)}],
            command=["python3","scripts/harness/verifier_runner.py","evidence/verifier","test_*.py"], verifier_result=verifier_result
        ))
        write_json(root / "evidence/verifier/verification-manifest.json", {
            "schema":"hybrid_harness.verification_manifest.v2", "mission_id":"M1", "work_order_id":"WO1",
            "candidate_head":self.candidate, "actor_id":"verify-session", "git_email":"verify@example.test",
            "cases":[
                {"case_id":"V-SUCCESS","kind":"PROPERTY","owner":"VERIFIER","partitions":["success"],"semantic_tags":["transaction"],"test_ids":["adversarial_cases.FixtureVerifier.test_success"],"oracle_ids":["O-TX-SUCCESS"]},
                {"case_id":"V-REJECT","kind":"ADVERSARIAL","owner":"VERIFIER","partitions":["rejection"],"semantic_tags":["transaction"],"test_ids":["adversarial_cases.FixtureVerifier.test_reject"],"oracle_ids":["O-TX-REJECT"]},
                {"case_id":"V-FAIL","kind":"FAULT_INJECTION","owner":"VERIFIER","partitions":["failure_atomicity"],"semantic_tags":["transaction"],"test_ids":["adversarial_cases.FixtureVerifier.test_failure"],"oracle_ids":["O-TX-FAIL"]},
                {"case_id":"V-RESTART","kind":"PROPERTY","owner":"VERIFIER","partitions":["restart"],"semantic_tags":["persistence"],"test_ids":["adversarial_cases.FixtureVerifier.test_restart"],"oracle_ids":["O-P-RESTART"]},
                {"case_id":"V-WRITE-FAIL","kind":"FAULT_INJECTION","owner":"VERIFIER","partitions":["write_failure"],"semantic_tags":["persistence"],"test_ids":["adversarial_cases.FixtureVerifier.test_write_failure"],"oracle_ids":["O-P-WRITE"]},
                {"case_id":"V-REPLACE-FAIL","kind":"FAULT_INJECTION","owner":"VERIFIER","partitions":["replace_failure"],"semantic_tags":["persistence"],"test_ids":["adversarial_cases.FixtureVerifier.test_replace_failure"],"oracle_ids":["O-P-REPLACE"]}
            ],
            "predicate_coverage":[{
                "predicate_id":"P-TX-ATOMIC", "case_ids":["V-SUCCESS","V-REJECT","V-FAIL"],
                "receipt_refs":["evidence/receipts/verifier.json"],
                "covered_partitions":["success","rejection","failure_atomicity"]
            },{
                "predicate_id":"P-PERSIST", "case_ids":["V-RESTART","V-WRITE-FAIL","V-REPLACE-FAIL"],
                "receipt_refs":["evidence/receipts/verifier.json"],
                "covered_partitions":["restart","write_failure","replace_failure"]
            }]
        })
        write_event(root, "VERIFICATION", "verify-session", "PASS", {
            "verification_manifest":"evidence/verifier/verification-manifest.json",
            "predicates":["P-TX-ATOMIC","P-PERSIST"]
        })
        run(root, "add", "evidence/verifier", "evidence/receipts/verifier.json", "evidence/raw/verifier.log", "evidence/events"); run(root, "commit", "-m", "verify candidate adversarially")

        run(root, "config", "user.name", "Director"); run(root, "config", "user.email", "director@example.test")
        write_event(root, "DIRECTOR_PRE_INTEGRATION", "director-session", "READY_FOR_INTEGRATION")
        run(root, "add", "evidence/events"); run(root, "commit", "-m", "director integration readiness")

        # Signed integration authorization MUST be committed before source_head is pinned/merged.
        integration_paths = ["config/control/specifications/M1.md","config/control/requirements/WO1.json","config/control/acceptance/WO1.json","evidence/candidate-lock.v1.json","evidence/verifier/verification-manifest.json","evidence/events/0002-review.json","evidence/events/0003-verification.json","evidence/events/0004-director-pre-integration.json","evidence/receipts/product.json","evidence/receipts/verifier.json","evidence/attestations/review.json"]
        integration_att = signed_attestation(self.INTEGRATION_SEED, root=root, attestation_id="integration-1", key_id="integration-key", principal="external-integrator", purpose="INTEGRATION_APPROVE", subject=self.candidate, prerequisite_event="evidence/events/0004-director-pre-integration.json", evidence_paths=integration_paths)
        write_json(root / "evidence/attestations/integration.json", integration_att)
        run(root, "config", "user.name", "External Integrator"); run(root, "config", "user.email", "integrator@example.test")
        write_event(root, "INTEGRATION_AUTHORIZATION", "external-integrator-session", "APPROVE", {"authorization":{"mode":"EXTERNAL_ATTESTATION","attestation":"evidence/attestations/integration.json"}})
        run(root, "add", "evidence/attestations/integration.json", "evidence/events"); run(root, "commit", "-m", "persist integration authorization")
        self.source_head = run(root, "rev-parse", "HEAD")

        run(root, "checkout", "main")
        self.target_pre = run(root, "rev-parse", "HEAD")
        run(root, "merge", "--ff-only", "feature/test")
        self.integration_result = run(root, "rev-parse", "HEAD")
        assert self.integration_result == self.source_head

        # Validate exact integration result before adding post-integration evidence.
        out2 = root / "evidence/raw/post-integration.log"; out2.write_bytes(b"POST OK\n")
        write_json(root / "evidence/receipts/post-integration.json", receipt(self.integration_result, "post-integration", "evidence/raw/post-integration.log", b"POST OK\n"))
        run(root, "config", "user.name", "Integrator"); run(root, "config", "user.email", "integrator@example.test")
        write_event(root, "INTEGRATION", "integrator-session", "INTEGRATED", {
            "source_head":self.source_head, "target_premerge_head":self.target_pre,
            "resulting_head":self.integration_result, "method":"FF", "authorization_event_seq":5
        })
        run(root, "add", "evidence/receipts/post-integration.json", "evidence/raw/post-integration.log", "evidence/events")
        run(root, "commit", "-m", "record integration lineage")

        run(root, "config", "user.name", "Verifier"); run(root, "config", "user.email", "verify@example.test")
        write_event(root, "POST_INTEGRATION_VALIDATION", "verify-session", "PASS", {"receipt":"evidence/receipts/post-integration.json"})
        run(root, "add", "evidence/events"); run(root, "commit", "-m", "verify integration result")

        run(root, "config", "user.name", "Director"); run(root, "config", "user.email", "director@example.test")
        write_event(root, "DIRECTOR_FINAL", "director-session", "COMPLETE")
        run(root, "add", "evidence/events"); run(root, "commit", "-m", "director final closure")

        write_json(root / "evidence/evidence-map.json", {
            "candidate_head":self.candidate, "tested_head":self.candidate, "reviewed_head":self.candidate,
            "candidate_receipts":["evidence/receipts/product.json"],
            "verifier_receipts":["evidence/receipts/verifier.json"],
            "post_integration_receipts":["evidence/receipts/post-integration.json"],
            "predicate_coverage":["P-TX-ATOMIC","P-PERSIST"],
            "requirement_coverage":["REQ-IMPLEMENT","REQ-PERSIST","REQ-FAILURE"],
            "unverified_items":[]
        })
        state.update({
            "state_revision":3,
            "active_mission":{"mission_id":"M1","complete":True,"branch":"feature/test","candidate_head":self.candidate},
            "mutation_lease":None, "status":"MISSION_COMPLETE"
        })
        write_json(root / "config/control/project-state.v1.json", state)
        run(root, "add", "evidence/evidence-map.json", "config/control/project-state.v1.json")
        run(root, "commit", "-m", "record derived mission completion")
        self.final = run(root, "rev-parse", "HEAD")
        cache_root = Path(_FIXTURE_CACHE_DIR.name) / risk
        subprocess.run(["git","clone","-q",str(root),str(cache_root)],check=True)
        _FIXTURE_CACHE[risk] = (cache_root, {
            "base":self.base, "dispatch":self.dispatch, "candidate":self.candidate,
            "source_head":self.source_head, "target_pre":self.target_pre,
            "integration_result":self.integration_result, "final":self.final
        })


class HardeningTests(unittest.TestCase):
    def test_ed25519_rfc8032_vector(self):
        seed=bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
        self.assertEqual("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", public_key_from_seed(seed).hex())
        self.assertEqual("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b", sign(seed,b"").hex())

    def test_duplicate_json_keys_are_rejected(self):
        with self.assertRaises(StrictJSONError): loads('{"a":1,"a":2}', label="dup")

    def test_declared_complete_without_machine_proof_is_blocked(self):
        r=build_continuation({"mission":{"mission_id":"M","complete":True},"completion_proven":False})
        self.assertFalse(r["mission_complete"]); self.assertEqual("SYSTEM_BLOCKED", r["handoff_class"])

    def test_valid_r7_flow_is_proven(self):
        with tempfile.TemporaryDirectory() as td:
            fx=ActiveFixture(Path(td)); self.assertEqual([], validate_active(fx.root, require_completion=True))

    def test_fake_external_signature_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            fx=ActiveFixture(Path(td)); p=fx.root/'evidence/attestations/review.json'; j=json.loads(p.read_text()); j['signature_b64']=base64.b64encode(b'x'*64).decode(); write_json(p,j)
            codes={f.code for f in validate_active(fx.root, require_completion=True)}; self.assertTrue({'ATTESTATION_SIGNATURE_INVALID','EXTERNAL_ATTESTATION_NOT_DURABLE_AT_HEAD'} & codes)

    def test_freeform_proof_ref_cannot_replace_signed_attestation(self):
        with tempfile.TemporaryDirectory() as td:
            fx=ActiveFixture(Path(td)); p=fx.root/'evidence/events/0002-review.json'; j=json.loads(p.read_text()); j['assurance']={'class':'EXTERNAL_VERIFIED','proof_ref':'banana'}; write_json(p,j)
            self.assertIn('EXTERNAL_ATTESTATION_REF_INVALID',{f.code for f in validate_active(fx.root, require_completion=True)})

    def test_uncommitted_evidence_cannot_complete(self):
        with tempfile.TemporaryDirectory() as td:
            fx=ActiveFixture(Path(td)); p=fx.root/'evidence/evidence-map.json'; j=json.loads(p.read_text()); j['unverified_items']=['x']; write_json(p,j)
            codes={f.code for f in validate_active(fx.root, require_completion=True)}
            self.assertIn('COMPLETION_WORKTREE_NOT_CLEAN',codes); self.assertIn('EVIDENCE_MAP_NOT_DURABLE_AT_HEAD',codes)

    def test_old_event_cannot_be_rewritten_even_with_rehashed_tail(self):
        with tempfile.TemporaryDirectory() as td:
            fx=ActiveFixture(Path(td)); p=fx.root/'evidence/events/0002-review.json'; j=json.loads(p.read_text()); j['actor_id']='rewritten'; write_json(p,j)
            # Rehash all following working-tree events to make the current chain internally consistent.
            events=sorted((fx.root/'evidence/events').glob('*.json'))
            for prev,cur in zip(events,events[1:]):
                c=json.loads(cur.read_text()); c['prev_event_sha256']=sha(prev); write_json(cur,c)
            run(fx.root,'add','evidence/events'); run(fx.root,'commit','-m','illegal rewrite of old evidence events')
            self.assertIn('EVIDENCE_EVENT_MUTATED_AFTER_ADD',{f.code for f in validate_active(fx.root, require_completion=True)})

    def test_raw_receipt_output_must_be_git_durable(self):
        with tempfile.TemporaryDirectory() as td:
            fx=ActiveFixture(Path(td)); run(fx.root,'rm','--cached','evidence/raw/product.log');
            self.assertIn('EVIDENCE_OUTPUT_NOT_DURABLE_AT_HEAD',{f.code for f in validate_active(fx.root, require_completion=True)})

    def test_product_change_after_candidate_invalidates_completion(self):
        with tempfile.TemporaryDirectory() as td:
            fx=ActiveFixture(Path(td)); (fx.root/'src/x.py').write_text('VALUE=2\n'); run(fx.root,'add','src/x.py'); run(fx.root,'commit','-m','illegal product mutation')
            self.assertIn('PRODUCT_CHANGED_AFTER_CANDIDATE',{f.code for f in validate_active(fx.root, require_completion=True)})

    def test_wrong_product_commit_identity_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            # Construct then rewrite declared identity in current WO to exercise both immutability and identity proof.
            fx=ActiveFixture(Path(td)); p=fx.root/'config/control/missions/WO1.json'; j=json.loads(p.read_text()); j['implementer_git_email']='other@example.test'; write_json(p,j)
            codes={f.code for f in validate_active(fx.root, require_completion=True)}
            self.assertTrue({'WORK_ORDER_MUTATED_AFTER_DISPATCH','IMPLEMENTER_GIT_IDENTITY_MISMATCH'} & codes)

    def test_trust_key_must_exist_at_base(self):
        with tempfile.TemporaryDirectory() as td:
            fx=ActiveFixture(Path(td));
            # Current trust can be broadened, but verifier resolves the immutable base and ignores it.
            p=fx.root/'config/control/harness/trust-providers.v1.json'; j=json.loads(p.read_text()); j['providers']['TEST_EXTERNAL']['keys']=[]; write_json(p,j)
            # Current mutation itself is dirty; base trust remains valid, demonstrating base resolution.
            codes={f.code for f in validate_active(fx.root, require_completion=True)}
            self.assertIn('COMPLETION_WORKTREE_NOT_CLEAN',codes)

    def test_candidate_lock_cannot_self_expand_closure_policy(self):
        with tempfile.TemporaryDirectory() as td:
            fx=ActiveFixture(Path(td)); p=fx.root/'evidence/candidate-lock.v1.json'; j=json.loads(p.read_text()); j['closure_allowed_paths'].append('src/**'); write_json(p,j)
            self.assertIn('CLOSURE_PATH_POLICY_NOT_BASE_OWNED',{f.code for f in validate_active(fx.root, require_completion=True)})

    def test_evidence_map_may_not_self_author_derived_lineage(self):
        with tempfile.TemporaryDirectory() as td:
            fx=ActiveFixture(Path(td)); p=fx.root/'evidence/evidence-map.json'; j=json.loads(p.read_text()); j['lineage']={'candidate_to_closure_diff_paths':[]}; write_json(p,j)
            self.assertIn('DERIVED_LINEAGE_MUST_NOT_BE_SELF_AUTHORED',{f.code for f in validate_active(fx.root, require_completion=True)})

    def test_integration_authorization_must_precede_source(self):
        with tempfile.TemporaryDirectory() as td:
            fx=ActiveFixture(Path(td)); p=fx.root/'evidence/events/0006-integration.json'; j=json.loads(p.read_text()); j['source_head']=run(fx.root,'rev-parse',fx.source_head+'^'); write_json(p,j)
            self.assertIn('INTEGRATION_AUTHORIZATION_NOT_BEFORE_SOURCE',{f.code for f in validate_active(fx.root, require_completion=True)})

    def test_integration_result_must_remain_ancestor_of_final(self):
        with tempfile.TemporaryDirectory() as td:
            fx=ActiveFixture(Path(td))
            # Build a sibling history from candidate, copying the final closure files.
            # Candidate stays an ancestor, but the recorded integration result does not.
            snapshot = {p.relative_to(fx.root).as_posix(): p.read_bytes() for p in fx.root.rglob('*') if p.is_file() and '.git/' not in p.as_posix()}
            run(fx.root,'checkout','-B','evil',fx.candidate)
            for rel,data in snapshot.items():
                if rel.startswith('.git/') or rel.startswith('src/'):
                    continue
                path=fx.root/rel; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(data)
            run(fx.root,'add','-A'); run(fx.root,'commit','-m','copied closure on sibling history')
            run(fx.root,'branch','-M','main')
            codes={f.code for f in validate_active(fx.root, require_completion=True)}
            self.assertIn('INTEGRATION_RESULT_NOT_ANCESTOR_OF_FINAL',codes)

    def test_canonical_amend_is_detected_locally(self):
        with tempfile.TemporaryDirectory() as td:
            fx=ActiveFixture(Path(td)); run(fx.root,'commit','--amend','-m','rewritten final closure')
            self.assertIn('CANONICAL_HISTORY_REWRITE_DETECTED',{f.code for f in validate_active(fx.root, require_completion=True)})

    def test_post_integration_receipt_is_required(self):
        with tempfile.TemporaryDirectory() as td:
            fx=ActiveFixture(Path(td)); p=fx.root/'evidence/evidence-map.json'; j=json.loads(p.read_text()); j['post_integration_receipts']=[]; write_json(p,j)
            self.assertIn('POST_INTEGRATION_RECEIPT_MISSING',{f.code for f in validate_active(fx.root, require_completion=True)})

    def test_completion_requires_released_lease(self):
        with tempfile.TemporaryDirectory() as td:
            fx=ActiveFixture(Path(td)); p=fx.root/'config/control/project-state.v1.json'; j=json.loads(p.read_text()); j['mutation_lease']={'holder':'impl'}; write_json(p,j)
            self.assertIn('COMPLETION_WITH_ACTIVE_MUTATION_LEASE',{f.code for f in validate_active(fx.root, require_completion=True)})

    def test_work_order_cannot_be_mutated_after_dispatch(self):
        with tempfile.TemporaryDirectory() as td:
            fx=ActiveFixture(Path(td)); p=fx.root/'config/control/missions/WO1.json'; j=json.loads(p.read_text()); j['risk']='LOW'; write_json(p,j)
            self.assertIn('WORK_ORDER_MUTATED_AFTER_DISPATCH',{f.code for f in validate_active(fx.root, require_completion=True)})

    def test_review_contract_forces_medium_floor(self):
        with tempfile.TemporaryDirectory() as td:
            fx=ActiveFixture(Path(td),risk='LOW')
            self.assertTrue({'RISK_BELOW_CONTRACT_FLOOR','RISK_BELOW_MACHINE_SEMANTIC_FLOOR'} & {f.code for f in validate_active(fx.root, require_completion=True)})

    def test_freeze_candidate_rejects_dirty_product_tree(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); run(root,'init','-b','feature/test'); run(root,'config','user.name','X'); run(root,'config','user.email','x@test'); (root/'src').mkdir(); (root/'src/x.py').write_text('x=1\n'); run(root,'add','.'); run(root,'commit','-m','candidate'); (root/'src/x.py').write_text('x=2\n')
            with self.assertRaises(ValueError): write_candidate_lock(root,['evidence/**'])


if __name__ == '__main__': unittest.main()
