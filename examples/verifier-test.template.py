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
        actual = 2 + 2  # replace with real verifier-owned observation
        self.assert_oracle_equal("ORACLE-PRED-001-BOUNDARY", actual, 4)
