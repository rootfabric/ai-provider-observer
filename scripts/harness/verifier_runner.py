from __future__ import annotations

import ast
import inspect
import json
import sys
import textwrap
import unittest
from typing import Any

MARKER = "HARNESS_VERIFIER_RESULT_JSON="


def _call_name(node: ast.Call) -> str | None:
    f = node.func
    return f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)


def _same_ast(a: ast.AST, b: ast.AST) -> bool:
    return ast.dump(a, include_attributes=False) == ast.dump(b, include_attributes=False)


def oracle_quality_findings(test: unittest.case.TestCase) -> list[str]:
    """Reject obvious no-op semantic oracle patterns before they become evidence."""
    method = getattr(test, getattr(test, "_testMethodName", ""), None)
    try:
        src = textwrap.dedent(inspect.getsource(method))
        tree = ast.parse(src)
    except Exception:
        return ["ORACLE_SOURCE_UNAVAILABLE"]
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name == "assert_oracle":
            out.append("UNSTRUCTURED_ORACLE_CALL_FORBIDDEN")
        elif name == "assert_oracle_equal" and len(node.args) >= 3:
            a, b = node.args[1], node.args[2]
            if _same_ast(a, b):
                out.append("TRIVIAL_ORACLE_EQUAL_SAME_EXPRESSION")
            if isinstance(a, ast.Constant) and isinstance(b, ast.Constant):
                out.append("TRIVIAL_ORACLE_EQUAL_CONSTANTS")
        elif name == "assert_oracle_unchanged" and len(node.args) >= 3:
            if _same_ast(node.args[1], node.args[2]):
                out.append("TRIVIAL_ORACLE_UNCHANGED_SAME_EXPRESSION")
    return sorted(set(out))


class HarnessResult(unittest.TextTestResult):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.records: list[dict[str, Any]] = []

    @staticmethod
    def _metadata(test: unittest.case.TestCase) -> dict[str, Any]:
        method = getattr(test, getattr(test, "_testMethodName", ""), None)
        meta = getattr(method, "_harness_verifier_case", None)
        if not isinstance(meta, dict):
            meta = {}
        observations = list(getattr(test, "_harness_oracle_observations", []) or [])
        observed = sorted({x.get("oracle_id") for x in observations if isinstance(x, dict) and isinstance(x.get("oracle_id"), str)})
        return {
            "test_id": test.id(),
            "case_id": meta.get("case_id"),
            "partitions": list(meta.get("partitions", [])) if isinstance(meta.get("partitions"), list) else [],
            "oracle_ids": list(meta.get("oracle_ids", [])) if isinstance(meta.get("oracle_ids"), list) else [],
            "observed_oracle_ids": observed,
            "oracle_observations": observations,
            "oracle_quality_findings": oracle_quality_findings(test),
        }

    def _add(self, test: unittest.case.TestCase, status: str, **extra: Any) -> None:
        row = self._metadata(test); row["status"] = status; row.update(extra); self.records.append(row)

    def addSuccess(self, test: unittest.case.TestCase) -> None:
        super().addSuccess(test); self._add(test, "PASS")
    def addFailure(self, test: unittest.case.TestCase, err: Any) -> None:
        super().addFailure(test, err); self._add(test, "FAIL")
    def addError(self, test: unittest.case.TestCase, err: Any) -> None:
        super().addError(test, err); self._add(test, "ERROR")
    def addSkip(self, test: unittest.case.TestCase, reason: str) -> None:
        super().addSkip(test, reason); self._add(test, "SKIP", skip_reason=reason)


class HarnessRunner(unittest.TextTestRunner):
    resultclass = HarnessResult


def main(argv: list[str]) -> int:
    start_dir = argv[1] if len(argv) > 1 else "evidence/verifier"
    pattern = argv[2] if len(argv) > 2 else "test_*.py"
    suite = unittest.defaultTestLoader.discover(start_dir=start_dir, pattern=pattern)
    result: HarnessResult = HarnessRunner(verbosity=2, stream=sys.stdout).run(suite)  # type: ignore[assignment]
    records = sorted(result.records, key=lambda r: str(r.get("test_id")))
    quality = sorted({f"{r.get('test_id')}:{q}" for r in records for q in (r.get("oracle_quality_findings") or [])})
    payload = {
        "schema": "hybrid_harness.verifier_execution.v2",
        "start_dir": start_dir,
        "pattern": pattern,
        "tests": records,
        "executed_test_ids": [r["test_id"] for r in records],
        "passed_test_ids": [r["test_id"] for r in records if r.get("status") == "PASS"],
        "failed_test_ids": [r["test_id"] for r in records if r.get("status") in {"FAIL", "ERROR"}],
        "skipped_test_ids": [r["test_id"] for r in records if r.get("status") == "SKIP"],
        "oracle_quality_findings": quality,
    }
    print(MARKER + json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0 if result.wasSuccessful() and not quality else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
