from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/harness"))
sys.path.insert(0, str(ROOT / "tests"))

from active_validation import validate_active, _temporal_claim_findings
from requirements import extract_normative_clauses
try:
    from tests.test_hardening import ActiveFixture, run, write_json
except ModuleNotFoundError:
    from test_hardening import ActiveFixture, run, write_json


class R9HardeningTests(unittest.TestCase):
    def test_wrapped_normative_bullet_is_one_logical_clause_not_fragments(self):
        spec = """# Requirements
- REQ-NONNEG: Any account balance MUST remain non-negative under
  every accepted operation and MUST remain non-negative after
  persistence and restart.
"""
        clauses = extract_normative_clauses(spec)
        self.assertEqual(1, len(clauses))
        self.assertEqual(
            "REQ-NONNEG: Any account balance MUST remain non-negative under every accepted operation and MUST remain non-negative after persistence and restart.",
            clauses[0].text,
        )
        self.assertNotEqual("Any", clauses[0].text)

    def test_logical_clause_hash_is_stable_across_markdown_line_wrapping(self):
        one_line = """# Requirements
- REQ-X: Persistent state MUST survive restart and MUST reject corrupt data.
"""
        wrapped = """# Requirements
- REQ-X: Persistent state MUST survive restart and MUST reject
  corrupt data.
"""
        a = extract_normative_clauses(one_line)
        b = extract_normative_clauses(wrapped)
        self.assertEqual([(c.text, c.sha256) for c in a], [(c.text, c.sha256) for c in b])

    def test_future_dated_review_attestation_claim_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            fx = ActiveFixture(Path(td))
            p = fx.root / "evidence/events/0002-review.json"
            event = json.loads(p.read_text(encoding="utf-8"))
            event["recorded_at_utc"] = "2000-01-01T00:00:00Z"
            write_json(p, event)
            run(fx.root, "add", "evidence/events/0002-review.json")
            run(fx.root, "commit", "-m", "simulate consumer timestamp predating signed issuance")
            codes = {f.code for f in validate_active(fx.root, require_completion=True)}
            self.assertIn("ATTESTATION_ISSUED_AFTER_CONSUMER_TIME", codes)

    def test_attestation_prerequisite_time_after_issuance_is_rejected(self):
        findings = _temporal_claim_findings(
            "2026-08-20T03:20:00Z",
            "2026-08-20T03:30:00Z",
            "2026-08-20T03:31:00Z",
            120,
        )
        self.assertIn("ATTESTATION_ISSUED_BEFORE_PREREQUISITE_TIME", {f.code for f in findings})

    def test_h8_style_future_issued_time_is_rejected_by_pure_temporal_gate(self):
        findings = _temporal_claim_findings(
            "2026-08-20T03:40:00Z",
            "2026-08-20T03:22:00Z",
            "2026-08-20T03:22:58Z",
            120,
        )
        self.assertIn("ATTESTATION_ISSUED_AFTER_CONSUMER_TIME", {f.code for f in findings})



if __name__ == "__main__":
    unittest.main()
