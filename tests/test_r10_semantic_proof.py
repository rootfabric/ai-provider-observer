from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/harness"))

from requirements import extract_normative_clauses
from semantic import coverage_errors


def contract() -> dict:
    return {
        "predicates": [{
            "predicate_id": "P-IDEMP",
            "statement": "Replay semantics including invalid conflicting payloads",
            "class": "IDEMPOTENCY",
            "semantic_tags": ["exactly_once"],
            "partitions": ["payload_conflict", "payload_conflict_invalid_payload"],
            "partition_oracles": {
                "payload_conflict": [{"oracle_id": "O-CONFLICT", "statement": "Changed payload for an existing command conflicts and does not mutate state."}],
                "payload_conflict_invalid_payload": [{"oracle_id": "O-CONFLICT-INVALID", "statement": "Changed invalid payload for an existing command still conflicts and does not mutate state."}],
            },
            "required_evidence": ["VERIFIER_TEST"],
            "verifier_owned": True,
            "requirement_ids": ["REQ-X"],
        }]
    }


def runtime_row(test_id: str, case_id: str, parts: list[str], oracles: list[str], observed: list[str] | None = None) -> dict:
    return {
        "test_id": test_id, "case_id": case_id, "partitions": parts,
        "oracle_ids": oracles, "observed_oracle_ids": oracles if observed is None else observed,
        "status": "PASS",
    }


def receipt(rows: list[dict]) -> dict:
    tids = [r["test_id"] for r in rows]
    return {"verifier_result": {
        "schema": "hybrid_harness.verifier_execution.v1",
        "tests": rows, "executed_test_ids": tids, "passed_test_ids": tids,
        "failed_test_ids": [], "skipped_test_ids": [],
    }}


def valid_manifest() -> tuple[dict, dict, dict]:
    rows = [
        runtime_row("test_v.C.test_conflict", "CASE-CONFLICT", ["payload_conflict"], ["O-CONFLICT"]),
        runtime_row("test_v.C.test_invalid_conflict", "CASE-INVALID-CONFLICT", ["payload_conflict_invalid_payload"], ["O-CONFLICT-INVALID"]),
    ]
    manifest = {
        "schema": "hybrid_harness.verification_manifest.v2",
        "cases": [
            {"case_id": "CASE-CONFLICT", "kind": "ADVERSARIAL", "owner": "VERIFIER", "partitions": ["payload_conflict"], "semantic_tags": ["exactly_once"], "test_ids": ["test_v.C.test_conflict"], "oracle_ids": ["O-CONFLICT"]},
            {"case_id": "CASE-INVALID-CONFLICT", "kind": "ADVERSARIAL", "owner": "VERIFIER", "partitions": ["payload_conflict_invalid_payload"], "semantic_tags": ["exactly_once"], "test_ids": ["test_v.C.test_invalid_conflict"], "oracle_ids": ["O-CONFLICT-INVALID"]},
        ],
        "predicate_coverage": [{
            "predicate_id": "P-IDEMP", "case_ids": ["CASE-CONFLICT", "CASE-INVALID-CONFLICT"],
            "receipt_refs": ["evidence/receipts/verifier.json"],
            "covered_partitions": ["payload_conflict", "payload_conflict_invalid_payload"],
        }],
    }
    em = {"candidate_receipts": [], "verifier_receipts": ["evidence/receipts/verifier.json"]}
    receipts = {"evidence/receipts/verifier.json": receipt(rows)}
    return manifest, em, receipts


class R10SemanticProofTests(unittest.TestCase):
    def test_valid_case_derived_runtime_bound_coverage_passes(self):
        manifest, em, receipts = valid_manifest()
        self.assertEqual([], coverage_errors(contract(), manifest, em, medium_plus=True, receipt_objects=receipts))

    def test_h99_style_partition_overclaim_is_rejected(self):
        manifest, em, receipts = valid_manifest()
        # Shape of the escaped h-99 defect: one case only proves payload_conflict,
        # while predicate_coverage claims the invalid-payload intersection too.
        manifest["cases"] = [manifest["cases"][0]]
        manifest["predicate_coverage"][0]["case_ids"] = ["CASE-CONFLICT"]
        codes = coverage_errors(contract(), manifest, em, medium_plus=True, receipt_objects=receipts)
        self.assertTrue(any(x.startswith("PREDICATE_PARTITION_OVERCLAIMED:P-IDEMP:payload_conflict_invalid_payload") for x in codes))
        self.assertTrue(any(x.startswith("PREDICATE_PARTITION_NOT_BACKED_BY_CASE:P-IDEMP:payload_conflict_invalid_payload") for x in codes))

    def test_manifest_test_id_not_seen_as_pass_is_rejected(self):
        manifest, em, receipts = valid_manifest()
        manifest["cases"][1]["test_ids"] = ["test_v.C.test_never_executed"]
        codes = coverage_errors(contract(), manifest, em, medium_plus=True, receipt_objects=receipts)
        self.assertTrue(any(x.startswith("VERIFIER_TEST_NOT_EXECUTED_PASS:CASE-INVALID-CONFLICT") for x in codes))

    def test_runtime_case_metadata_must_match_manifest(self):
        manifest, em, receipts = valid_manifest()
        receipts["evidence/receipts/verifier.json"]["verifier_result"]["tests"][1]["partitions"] = ["payload_conflict"]
        codes = coverage_errors(contract(), manifest, em, medium_plus=True, receipt_objects=receipts)
        self.assertTrue(any(x.startswith("VERIFIER_TEST_PARTITION_BINDING_MISMATCH:CASE-INVALID-CONFLICT") for x in codes))

    def test_declared_oracle_must_have_succeeded_at_runtime(self):
        manifest, em, receipts = valid_manifest()
        receipts["evidence/receipts/verifier.json"]["verifier_result"]["tests"][1]["observed_oracle_ids"] = []
        codes = coverage_errors(contract(), manifest, em, medium_plus=True, receipt_objects=receipts)
        self.assertTrue(any(x.startswith("VERIFIER_ORACLE_NOT_OBSERVED:CASE-INVALID-CONFLICT") for x in codes))
        self.assertIn("PREDICATE_ORACLE_NOT_OBSERVED:P-IDEMP:O-CONFLICT-INVALID", codes)

    def test_plain_prose_wrapping_has_stable_clause_identity(self):
        one = """# Goal\nThe implementation MUST preserve state across restart and MUST reject corrupt input.\n"""
        wrapped = """# Goal\nThe implementation MUST preserve state across restart and MUST\nreject corrupt input.\n"""
        a = extract_normative_clauses(one)
        b = extract_normative_clauses(wrapped)
        self.assertEqual([(x.text, x.sha256) for x in a], [(x.text, x.sha256) for x in b])
        self.assertEqual(1, len(a))

    def test_base_owned_runner_emits_exact_test_and_oracle_observation(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "test_probe.py").write_text(
                "import sys\n"
                f"sys.path.insert(0, {str(ROOT / 'scripts/harness')!r})\n"
                "from verifier_api import VerifierTestCase, verifier_case\n"
                "class Probe(VerifierTestCase):\n"
                "  @verifier_case('CASE-P', partitions=['boundary'], oracle_ids=['O-P'])\n"
                "  def test_probe(self):\n"
                "    self.assert_oracle_equal('O-P', 2+2, 4)\n",
                encoding="utf-8",
            )
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts/harness/verifier_runner.py"), str(d), "test_*.py"],
                cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            self.assertEqual(0, proc.returncode, proc.stdout)
            line = next(x for x in proc.stdout.splitlines() if x.startswith("HARNESS_VERIFIER_RESULT_JSON="))
            result = json.loads(line.split("=", 1)[1])
            self.assertEqual(1, len(result["passed_test_ids"]))
            row = result["tests"][0]
            self.assertEqual("CASE-P", row["case_id"])
            self.assertEqual(["boundary"], row["partitions"])
            self.assertEqual(["O-P"], row["oracle_ids"])
            self.assertEqual(["O-P"], row["observed_oracle_ids"])


if __name__ == "__main__":
    unittest.main()
