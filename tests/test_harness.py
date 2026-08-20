from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/harness"))

from audit import all_findings
from selftest import run as run_selftest


class HybridHarnessTests(unittest.TestCase):
    def test_baseline_is_green(self):
        errors = [f for f in all_findings(ROOT) if f.severity == "ERROR"]
        self.assertEqual([], errors)

    def test_negative_selftests_all_detected(self):
        ok, details = run_selftest(ROOT)
        self.assertTrue(ok, "\n".join(details))


if __name__ == "__main__":
    unittest.main()
