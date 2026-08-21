from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/harness"))

from verifier_api import VerifierTestCase, verifier_case


class ExampleVerifierTests(VerifierTestCase):
    @verifier_case(
        "CASE-V-001",
        partitions=["boundary"],
        oracle_ids=["ORACLE-PRED-001-BOUNDARY"],
    )
    def test_boundary_behavior(self) -> None:
        # Observe the real SUT. Do not replace this with assert_oracle(..., True)
        # or assert_oracle_equal(..., 1, 1): R11 rejects trivial oracle evidence.
        actual = 2 + 2  # replace with verifier-owned SUT observation
        expected = 4
        self.assert_oracle_equal("ORACLE-PRED-001-BOUNDARY", actual, expected)

    # For an exception contract use:
    # self.assert_oracle_raises("ORACLE-X", ExpectedError, sut.operation, arg)
    # For no-mutation/state preservation use:
    # self.assert_oracle_unchanged("ORACLE-Y", before_snapshot, after_snapshot)
