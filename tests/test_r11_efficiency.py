from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/harness"))

from control import _effective_status
from semantic import coverage_errors

MARKER = "HARNESS_VERIFIER_RESULT_JSON="


def run_runner(source: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "test_probe.py").write_text(source, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts/harness/verifier_runner.py"), str(d), "test_*.py"],
            cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        line = next(x for x in proc.stdout.splitlines() if x.startswith(MARKER))
        return proc, json.loads(line.split("=", 1)[1])


class R11EfficiencyTests(unittest.TestCase):
    def test_h10_style_assert_true_oracle_is_rejected(self):
        proc, result = run_runner(
            "import sys\n"
            f"sys.path.insert(0, {str(ROOT / 'scripts/harness')!r})\n"
            "from verifier_api import VerifierTestCase, verifier_case\n"
            "class Probe(VerifierTestCase):\n"
            " @verifier_case('CASE-H10', partitions=['negative_initial'], oracle_ids=['O-H10'])\n"
            " def test_probe(self):\n"
            "  self.assert_oracle('O-H10', True)\n"
        )
        self.assertNotEqual(0, proc.returncode)
        self.assertTrue(any("UNSTRUCTURED_ORACLE_CALL_FORBIDDEN" in x for x in result["oracle_quality_findings"]))

    def test_trivial_constant_equal_oracle_is_rejected_even_if_test_passes(self):
        proc, result = run_runner(
            "import sys\n"
            f"sys.path.insert(0, {str(ROOT / 'scripts/harness')!r})\n"
            "from verifier_api import VerifierTestCase, verifier_case\n"
            "class Probe(VerifierTestCase):\n"
            " @verifier_case('CASE-T', partitions=['p'], oracle_ids=['O-T'])\n"
            " def test_probe(self):\n"
            "  self.assert_oracle_equal('O-T', 1, 1)\n"
        )
        self.assertNotEqual(0, proc.returncode)
        self.assertIn("test_probe", result["passed_test_ids"][0])
        self.assertTrue(any("TRIVIAL_ORACLE_EQUAL_CONSTANTS" in x for x in result["oracle_quality_findings"]))

    def test_structured_raises_oracle_is_machine_observable(self):
        proc, result = run_runner(
            "import sys\n"
            f"sys.path.insert(0, {str(ROOT / 'scripts/harness')!r})\n"
            "from verifier_api import VerifierTestCase, verifier_case\n"
            "class Probe(VerifierTestCase):\n"
            " @verifier_case('CASE-R', partitions=['invalid'], oracle_ids=['O-R'])\n"
            " def test_probe(self):\n"
            "  def operation(): raise ValueError('bad')\n"
            "  self.assert_oracle_raises('O-R', ValueError, operation)\n"
        )
        self.assertEqual(0, proc.returncode, proc.stdout)
        obs = result["tests"][0]["oracle_observations"][0]
        self.assertEqual("raises", obs["kind"])
        self.assertEqual("ValueError", obs["observed_exception"])

    def test_contract_observation_kind_mismatch_blocks_coverage(self):
        contract = {"predicates":[{
            "predicate_id":"P", "partitions":["invalid"], "verifier_owned":True,
            "partition_oracles":{"invalid":[{"oracle_id":"O","statement":"must raise","observation_kind":"raises","expected_exception":"ValueError"}]}
        }]}
        manifest = {"schema":"hybrid_harness.verification_manifest.v2","cases":[{
            "case_id":"C","kind":"ADVERSARIAL","owner":"VERIFIER","partitions":["invalid"],"oracle_ids":["O"],"test_ids":["m.C.test_x"]
        }],"predicate_coverage":[{"predicate_id":"P","case_ids":["C"],"receipt_refs":["evidence/receipts/v.json"],"covered_partitions":["invalid"]}]}
        row={"test_id":"m.C.test_x","case_id":"C","partitions":["invalid"],"oracle_ids":["O"],"observed_oracle_ids":["O"],"oracle_observations":[{"oracle_id":"O","kind":"equal","matched":True}],"oracle_quality_findings":[],"status":"PASS"}
        receipt={"verifier_result":{"schema":"hybrid_harness.verifier_execution.v2","tests":[row],"executed_test_ids":["m.C.test_x"],"passed_test_ids":["m.C.test_x"],"failed_test_ids":[],"skipped_test_ids":[],"oracle_quality_findings":[]}}
        errors=coverage_errors(contract, manifest, {"verifier_receipts":["evidence/receipts/v.json"]}, medium_plus=True, receipt_objects={"evidence/receipts/v.json":receipt})
        self.assertTrue(any(x.startswith("VERIFIER_ORACLE_KIND_MISMATCH") for x in errors))

    def test_declared_complete_with_findings_has_invalid_completion_effective_state(self):
        state={"status":"MISSION_COMPLETE","active_mission":{"mission_id":"M","complete":True}}
        self.assertEqual("INVALID_COMPLETION", _effective_status(state, [object()]))

    def test_resume_keeps_runtime_timeout_diagnostic_separate_from_durable_state(self):
        proc=subprocess.run([str(ROOT/"CONTROL_HARNESS.sh"),"resume","TIMEOUT"],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        self.assertEqual(0,proc.returncode,proc.stdout)
        self.assertIn("runtime_termination_reason=TIMEOUT",proc.stdout)
        self.assertIn("runtime_reason_is_diagnostic_only=true",proc.stdout)
        self.assertIn("effective_status=HARNESS_READY",proc.stdout)

    def test_attempt_retry_preserves_old_history_and_creates_new_dispatch(self):
        with tempfile.TemporaryDirectory() as td:
            repo=Path(td)/"repo"
            shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns(".git","__pycache__","*.pyc"))
            subprocess.run(["git","-C",str(repo),"init","-b","main"],check=True,stdout=subprocess.DEVNULL)
            subprocess.run(["git","-C",str(repo),"config","user.name","Retry Test"],check=True)
            subprocess.run(["git","-C",str(repo),"config","user.email","retry@example.invalid"],check=True)
            subprocess.run(["git","-C",str(repo),"add","."],check=True); subprocess.run(["git","-C",str(repo),"commit","-m","r11 baseline"],check=True,stdout=subprocess.DEVNULL)
            spec=repo/"config/control/specifications/M.md"; spec.parent.mkdir(parents=True,exist_ok=True); spec.write_text("# M\n- Product MUST work.\n",encoding="utf-8")
            subprocess.run(["git","-C",str(repo),"add",str(spec.relative_to(repo))],check=True); subprocess.run(["git","-C",str(repo),"commit","-m","spec"],check=True,stdout=subprocess.DEVNULL)
            base=subprocess.check_output(["git","-C",str(repo),"rev-parse","HEAD"],text=True).strip()
            req={"schema":"hybrid_harness.requirements_manifest.v1","manifest_id":"RM-WO-OLD","mission_id":"M","work_order_id":"WO-OLD","specification":"config/control/specifications/M.md","specification_sha256":"x","requirements":[]}
            ac={"schema":"hybrid_harness.acceptance_contract.v1","contract_id":"AC-WO-OLD","mission_id":"M","work_order_id":"WO-OLD","semantic_tags":[],"predicates":[],"completion_rule":"x"}
            wo={"schema":"hybrid_harness.work_order.v1","work_order_id":"WO-OLD","attempt_id":"ATTEMPT-A","risk":"LOW","risk_reasons":[],"base_sha":base,"started_at_utc":"2026-08-21T00:00:00Z","branch":"feature/old","implementer_actor_id":"impl","implementer_git_email":"impl@example.invalid","allowed_paths":["src/**"],"implementation_paths":["src/**"],"forbidden_paths":["config/control/harness/**"],"required_evidence":[],"stop_conditions":[],"mission":{"mission_id":"M","success_condition":"x"},"specification":"config/control/specifications/M.md","requirements_manifest":"config/control/requirements/WO-OLD.json","acceptance_contract":"config/control/acceptance/WO-OLD.json","handoff":{"next_actor":"IMPLEMENTER","next_action":"WORK","evidence_sink":"EXECUTION_LEDGER","resume_condition":"DONE","on_success":"REVIEW","on_failure":"RETRY"},"integration":{"required":False,"target_branch":"main"}}
            (repo/"config/control/requirements/WO-OLD.json").write_text(json.dumps(req,indent=2)+"\n")
            (repo/"config/control/acceptance").mkdir(parents=True,exist_ok=True); (repo/"config/control/acceptance/WO-OLD.json").write_text(json.dumps(ac,indent=2)+"\n")
            (repo/"config/control/missions/WO-OLD.json").write_text(json.dumps(wo,indent=2)+"\n")
            state=json.loads((repo/"config/control/project-state.v1.json").read_text()); state.update({"active_work_order":"WO-OLD","active_checkpoint":"WO-OLD","active_epoch":"M","active_mission":{"mission_id":"M","candidate_head":None,"complete":False},"status":"MISSION_ACTIVE"}); (repo/"config/control/project-state.v1.json").write_text(json.dumps(state,indent=2)+"\n")
            subprocess.run(["git","-C",str(repo),"add","config/control"],check=True); subprocess.run(["git","-C",str(repo),"commit","-m","dispatch old attempt"],check=True,stdout=subprocess.DEVNULL)
            old_dispatch=subprocess.check_output(["git","-C",str(repo),"rev-parse","HEAD"],text=True).strip()
            proc=subprocess.run([str(repo/"CONTROL_HARNESS.sh"),"attempt-retry","WO-NEW","ATTEMPT-B","feature/new","test failure"],cwd=repo,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            self.assertEqual(0,proc.returncode,proc.stdout)
            self.assertIn("ATTEMPT_SUPERSEDED=ATTEMPT-A",proc.stdout)
            self.assertEqual("feature/new",subprocess.check_output(["git","-C",str(repo),"branch","--show-current"],text=True).strip())
            new_wo=json.loads((repo/"config/control/missions/WO-NEW.json").read_text())
            dispatch=subprocess.check_output(["git","-C",str(repo),"rev-parse","HEAD"],text=True).strip()
            parent=subprocess.check_output(["git","-C",str(repo),"rev-parse",dispatch+"^"],text=True).strip()
            self.assertEqual(old_dispatch,parent)
            self.assertEqual(parent,new_wo["base_sha"])
            record=json.loads((repo/"config/control/missions/attempts/ATTEMPT-A.json").read_text())
            self.assertEqual("SUPERSEDED",record["state"])
            self.assertEqual("ATTEMPT-B",record["superseded_by_attempt_id"])


if __name__ == "__main__":
    unittest.main()
