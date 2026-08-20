from pathlib import Path
import ast
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/harness"))

from audit import all_findings


class HybridHarnessTests(unittest.TestCase):
    def test_baseline_is_green(self):
        errors = [f for f in all_findings(ROOT) if f.severity == "ERROR"]
        self.assertEqual([], errors)

    def test_mutation_selftest_is_not_embedded_in_unit_discovery(self):
        source = (ROOT / "tests/test_harness.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertFalse(any(isinstance(n, ast.ImportFrom) and n.module == "selftest" for n in ast.walk(tree)))
        self.assertFalse(any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "run_selftest" for n in ast.walk(tree)))


if __name__ == "__main__":
    unittest.main()
