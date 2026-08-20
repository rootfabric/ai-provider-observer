from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any

MARKER = "HARNESS_VERIFIER_RESULT_JSON="


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
        observed = sorted(getattr(test, "_harness_observed_oracles", set()) or set())
        return {
            "test_id": test.id(),
            "case_id": meta.get("case_id"),
            "partitions": list(meta.get("partitions", [])) if isinstance(meta.get("partitions"), list) else [],
            "oracle_ids": list(meta.get("oracle_ids", [])) if isinstance(meta.get("oracle_ids"), list) else [],
            "observed_oracle_ids": observed,
        }

    def addSuccess(self, test: unittest.case.TestCase) -> None:
        super().addSuccess(test)
        row = self._metadata(test); row["status"] = "PASS"; self.records.append(row)

    def addFailure(self, test: unittest.case.TestCase, err: Any) -> None:
        super().addFailure(test, err)
        row = self._metadata(test); row["status"] = "FAIL"; self.records.append(row)

    def addError(self, test: unittest.case.TestCase, err: Any) -> None:
        super().addError(test, err)
        row = self._metadata(test); row["status"] = "ERROR"; self.records.append(row)

    def addSkip(self, test: unittest.case.TestCase, reason: str) -> None:
        super().addSkip(test, reason)
        row = self._metadata(test); row["status"] = "SKIP"; row["skip_reason"] = reason; self.records.append(row)


class HarnessRunner(unittest.TextTestRunner):
    resultclass = HarnessResult


def main(argv: list[str]) -> int:
    start_dir = argv[1] if len(argv) > 1 else "evidence/verifier"
    pattern = argv[2] if len(argv) > 2 else "test_*.py"
    loader = unittest.defaultTestLoader
    suite = loader.discover(start_dir=start_dir, pattern=pattern)
    runner = HarnessRunner(verbosity=2, stream=sys.stdout)
    result: HarnessResult = runner.run(suite)  # type: ignore[assignment]
    records = sorted(result.records, key=lambda r: str(r.get("test_id")))
    payload = {
        "schema": "hybrid_harness.verifier_execution.v1",
        "start_dir": start_dir,
        "pattern": pattern,
        "tests": records,
        "executed_test_ids": [r["test_id"] for r in records],
        "passed_test_ids": [r["test_id"] for r in records if r.get("status") == "PASS"],
        "failed_test_ids": [r["test_id"] for r in records if r.get("status") in {"FAIL", "ERROR"}],
        "skipped_test_ids": [r["test_id"] for r in records if r.get("status") == "SKIP"],
    }
    print(MARKER + json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
