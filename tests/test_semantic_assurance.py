from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/harness"))

from semantic import semantic_contract_errors, infer_semantic_tags, risk_floor
from active_validation import validate_active
from tests.test_hardening import ActiveFixture, write_json


def policy(name: str) -> dict:
    return json.loads((ROOT / f"config/control/harness/{name}").read_text(encoding="utf-8"))


class SemanticAssuranceTests(unittest.TestCase):
    def test_miniledger_semantics_force_medium(self):
        wo = {
            "mission": {"success_condition": "persistent transaction ledger with exactly-once commands and total balance conservation"},
            "required_evidence": ["tests"],
            "handoff": {"next_actor": "VERIFIER"},
            "stop_conditions": [],
        }
        contract = {
            "schema":"hybrid_harness.acceptance_contract.v1", "contract_id":"AC", "mission_id":"M", "work_order_id":"WO",
            "semantic_tags":["persistence","transaction","exactly_once","conservation"],
            "predicates":[
                {"predicate_id":"P1","statement":"persistent", "class":"PERSISTENCE","semantic_tags":["persistence"],"partitions":["restart","write_failure","replace_failure"],"required_evidence":["VERIFIER_TEST"],"verifier_owned":True},
                {"predicate_id":"P2","statement":"transaction", "class":"ATOMICITY","semantic_tags":["transaction"],"partitions":["success","rejection","failure_atomicity"],"required_evidence":["VERIFIER_TEST"],"verifier_owned":True},
                {"predicate_id":"P3","statement":"exactly once", "class":"IDEMPOTENCY","semantic_tags":["exactly_once"],"partitions":["first_execution","exact_replay","payload_conflict","terminal_rejection_replay"],"required_evidence":["VERIFIER_TEST"],"verifier_owned":True},
                {"predicate_id":"P4","statement":"total balance conservation", "class":"CONSERVATION","semantic_tags":["conservation"],"partitions":["distinct_entities","same_entity","boundary_amount"],"required_evidence":["PROPERTY_CHECK"],"verifier_owned":True}
            ],
            "completion_rule":"all"
        }
        tags = infer_semantic_tags(policy("semantic-risk-policy.v1.json"), wo, contract)
        self.assertTrue({"persistence","transaction","exactly_once","conservation"}.issubset(tags))
        self.assertEqual("MEDIUM", risk_floor(policy("semantic-risk-policy.v1.json"), tags))

    def test_conservation_without_same_entity_is_rejected(self):
        wo = {"mission":{"success_condition":"total balance conservation for transfer"},"required_evidence":[],"handoff":{},"stop_conditions":[]}
        contract = {
            "schema":"hybrid_harness.acceptance_contract.v1", "contract_id":"AC", "mission_id":"M", "work_order_id":"WO",
            "semantic_tags":["conservation","transaction"],
            "predicates":[
                {"predicate_id":"P-C","statement":"total balance conservation", "class":"CONSERVATION","semantic_tags":["conservation"],"partitions":["distinct_entities","boundary_amount"],"required_evidence":["PROPERTY_CHECK"],"verifier_owned":True},
                {"predicate_id":"P-T","statement":"atomic transfer", "class":"ATOMICITY","semantic_tags":["transaction"],"partitions":["success","rejection","failure_atomicity"],"required_evidence":["VERIFIER_TEST"],"verifier_owned":True}
            ],"completion_rule":"all"
        }
        errors, _, _ = semantic_contract_errors(policy("semantic-risk-policy.v1.json"), policy("acceptance-contract.schema.v1.json"), wo, contract)
        self.assertIn("ACCEPTANCE_PARTITION_MISSING:conservation:same_entity", errors)

    def test_missing_verifier_partition_blocks_completion(self):
        with tempfile.TemporaryDirectory() as td:
            fx = ActiveFixture(Path(td))
            p = fx.root / "evidence/verifier/verification-manifest.json"
            j = json.loads(p.read_text(encoding="utf-8"))
            j["predicate_coverage"][0]["covered_partitions"] = ["success", "rejection"]
            write_json(p, j)
            codes = {f.code for f in validate_active(fx.root, require_completion=True)}
            self.assertIn("PREDICATE_PARTITION_UNPROVEN", codes)

    def test_implementer_owned_verifier_case_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            fx = ActiveFixture(Path(td))
            p = fx.root / "evidence/verifier/verification-manifest.json"
            j = json.loads(p.read_text(encoding="utf-8"))
            for case in j["cases"]:
                case["owner"] = "IMPLEMENTER"
            write_json(p, j)
            codes = {f.code for f in validate_active(fx.root, require_completion=True)}
            self.assertIn("VERIFIER_OWNED_EVIDENCE_MISSING", codes)

    def test_r5_shaped_work_order_without_acceptance_contract_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            fx = ActiveFixture(Path(td))
            p = fx.root / "config/control/missions/WO1.json"
            j = json.loads(p.read_text(encoding="utf-8"))
            j.pop("acceptance_contract", None)
            write_json(p, j)
            codes = {f.code for f in validate_active(fx.root, require_completion=True)}
            self.assertIn("ACCEPTANCE_CONTRACT_REF_INVALID", codes)


if __name__ == "__main__":
    unittest.main()
