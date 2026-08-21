from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/harness"))

from control import _compact_finding_rows
from evidence import parse_verifier_result


class _F:
    def __init__(self, code: str, message: str = "x"):
        self.code = code; self.message = message; self.severity = "ERROR"


class R12LeanTests(unittest.TestCase):
    def test_brief_is_bounded_and_avoids_harness_source_as_normal_context(self):
        proc = subprocess.run([str(ROOT/"CONTROL_HARNESS.sh"), "brief", "NEW_SESSION"], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(0, proc.returncode, proc.stdout)
        self.assertLess(len(proc.stdout), 2400)
        self.assertIn("BRIEF: PASS", proc.stdout)
        self.assertIn("normal_context_forbidden=scripts/harness/**", proc.stdout)

    def test_default_finding_rows_are_deduplicated_and_bounded(self):
        findings = [_F(f"CODE-{i%20}", str(i)) for i in range(100)]
        rows, omitted = _compact_finding_rows(findings, 8)
        self.assertEqual(8, len(rows))
        self.assertEqual(12, omitted)
        self.assertTrue(all(count == 5 for _, count in rows))

    def test_verifier_runner_uses_compact_compressed_transport_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td)
            (d/"test_probe.py").write_text(
                "import sys\n"
                f"sys.path.insert(0, {str(ROOT/'scripts/harness')!r})\n"
                "from verifier_api import VerifierTestCase, verifier_case\n"
                "class Probe(VerifierTestCase):\n"
                " @verifier_case('C', partitions=['p'], oracle_ids=['O'])\n"
                " def test_x(self): self.assert_oracle_equal('O', 2+2, 4)\n",
                encoding="utf-8")
            proc=subprocess.run([sys.executable, str(ROOT/"scripts/harness/verifier_runner.py"), str(d), "test_*.py"], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            self.assertEqual(0, proc.returncode, proc.stdout)
            lines=proc.stdout.splitlines()
            packed=next(x.split("=",1)[1] for x in lines if x.startswith("HARNESS_VERIFIER_RESULT_ZLIB_B64="))
            payload=json.loads(zlib.decompress(base64.b64decode(packed)).decode())
            self.assertEqual(1, len(payload["passed_test_ids"]))
            self.assertLess(len(proc.stdout), 3000)
            self.assertIsNotNone(parse_verifier_result(proc.stdout.encode()))

    def test_r12_policy_keeps_lean_guards_machine_declared(self):
        policy=json.loads((ROOT/"config/control/harness/harness-policy.v1.json").read_text())
        self.assertEqual("HYBRID-HARNESS-R12", policy["revision"])
        required={
            "agent_context_uses_compact_command_api",
            "default_validation_output_is_bounded",
            "raw_machine_output_is_durable_not_echoed",
            "evidence_transitions_support_atomic_commit",
            "completion_findings_are_not_normal_implementation_context",
            "harness_source_is_not_normal_agent_context",
        }
        self.assertTrue(all(policy["principles"].get(k) is True for k in required))

    def test_candidate_check_refuses_idle_template(self):
        proc=subprocess.run([str(ROOT/"CONTROL_HARNESS.sh"),"candidate-check","probe","--",sys.executable,"-c","print('ok')"],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        self.assertNotEqual(0,proc.returncode)
        self.assertIn("active mission/work order required",proc.stdout)


if __name__ == "__main__":
    unittest.main()
