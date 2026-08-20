from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/harness"))
sys.path.insert(0, str(ROOT / "tests"))

from active_validation import validate_active
from gitproof import path_immutable_since_add, trajectory_violations
try:
    from tests.test_hardening import ActiveFixture, run, write_json
except ModuleNotFoundError:
    from test_hardening import ActiveFixture, run, write_json


def codes(root: Path) -> set[str]:
    return {f.code for f in validate_active(root, require_completion=True)}


class R8PortabilityHistoryTests(unittest.TestCase):
    def test_replace_refs_are_rejected_even_though_gitproof_ignores_overlay(self):
        with tempfile.TemporaryDirectory() as td:
            fx = ActiveFixture(Path(td))
            head = run(fx.root, "rev-parse", "HEAD")
            parent = run(fx.root, "rev-parse", "HEAD^")
            subprocess.run(["git", "-C", str(fx.root), "update-ref", f"refs/replace/{head}", parent], check=True)
            self.assertIn("GIT_REPLACE_REFS_PRESENT", codes(fx.root))

    def test_mutate_then_revert_does_not_restore_immutability(self):
        with tempfile.TemporaryDirectory() as td:
            fx = ActiveFixture(Path(td))
            p = fx.root / "evidence/attestations/review.json"
            original = p.read_bytes()
            obj = json.loads(original)
            obj["issued_at_utc"] = "2099-01-01T00:00:00Z"
            write_json(p, obj)
            run(fx.root, "add", str(p.relative_to(fx.root)))
            run(fx.root, "commit", "-m", "tamper review attestation")
            p.write_bytes(original)
            run(fx.root, "add", str(p.relative_to(fx.root)))
            run(fx.root, "commit", "-m", "revert review attestation bytes")
            self.assertFalse(path_immutable_since_add(fx.root, "evidence/attestations/review.json"))
            self.assertIn("EXTERNAL_ATTESTATION_MUTATED_AFTER_ADD", codes(fx.root))

    def test_forbidden_closure_path_cannot_be_laundered_by_revert(self):
        with tempfile.TemporaryDirectory() as td:
            fx = ActiveFixture(Path(td))
            p = fx.root / "tools/temporary-closure-helper.py"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("print('temporary')\n", encoding="utf-8")
            run(fx.root, "add", str(p.relative_to(fx.root)))
            run(fx.root, "commit", "-m", "forbidden closure helper")
            p.unlink()
            run(fx.root, "add", "-u")
            run(fx.root, "commit", "-m", "remove forbidden closure helper")
            self.assertIn("POST_INTEGRATION_PRODUCT_CHANGE", codes(fx.root))

    def test_review_consumer_binds_exact_attestation_sha_and_blob(self):
        with tempfile.TemporaryDirectory() as td:
            fx = ActiveFixture(Path(td))
            event = json.loads((fx.root / "evidence/events/0002-review.json").read_text(encoding="utf-8"))
            assurance = event["assurance"]
            self.assertEqual(64, len(assurance["attestation_sha256"]))
            self.assertEqual(40, len(assurance["attestation_git_blob"]))
            assurance["attestation_sha256"] = "0" * 64
            write_json(fx.root / "evidence/events/0002-review.json", event)
            run(fx.root, "add", "evidence/events/0002-review.json")
            run(fx.root, "commit", "-m", "tamper consumer attestation binding")
            self.assertIn("ATTESTATION_CONSUMER_SHA256_MISMATCH", codes(fx.root))

    def test_trajectory_helper_reports_intermediate_violation(self):
        with tempfile.TemporaryDirectory() as td:
            fx = ActiveFixture(Path(td))
            anchor = run(fx.root, "rev-parse", "HEAD")
            p = fx.root / "src/transient.py"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x=1\n", encoding="utf-8")
            run(fx.root, "add", "src/transient.py")
            run(fx.root, "commit", "-m", "transient product change")
            p.unlink(); run(fx.root, "add", "-u"); run(fx.root, "commit", "-m", "revert transient product change")
            bad = trajectory_violations(fx.root, anchor, "HEAD", ["evidence/**"])
            self.assertTrue(any(path == "src/transient.py" for _, path in bad))


if __name__ == "__main__":
    unittest.main()
