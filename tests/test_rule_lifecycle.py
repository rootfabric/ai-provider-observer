from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/harness"))

from rules import add_rule, health_report, lifecycle_issues, review_rule


def write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def fixture(tmp_path: Path):
    write(tmp_path / "config/control/harness/rule-lifecycle-policy.v1.json", {
        "schema":"hybrid_harness.rule_lifecycle_policy.v1",
        "default_review_every_days":90,
        "review_every_days_by_class":{"SECURITY_INVARIANT":30,"CONTROL_INVARIANT":90},
        "stale_is_error_for_classes":["SECURITY_INVARIANT"],
        "allowed_rule_statuses":["ACTIVE","DRAFT","DEPRECATED","SUPERSEDED"]
    })
    write(tmp_path / "config/control/harness/rule-registry.v1.json", {
        "schema":"hybrid_harness.rule_registry.v3",
        "rules":[{
            "id":"SEC-1","class":"SECURITY_INVARIANT","source":"test","applies_when":"always",
            "enforcement":"machine","enforced_by":["guard.py"],"retirement":"SECURITY_REVISION_REQUIRED","prose_mode":"router_only"
        }]
    })
    write(tmp_path / "config/control/harness/rule-review-state.v1.json", {
        "schema":"hybrid_harness.rule_review_state.v1",
        "reviews":{"SEC-1":{"last_reviewed":"2026-08-01","reviewed_by":"test","note":"ok"}}
    })
    write(tmp_path / "scripts/harness/guard.py", "x=1\n")
    write(tmp_path / "tests/test_guard.py", "def test_guard(): pass\n")


def test_active_then_stale(tmp_path):
    fixture(tmp_path)
    report = health_report(tmp_path, today=date(2026,8,20))
    assert report["rules"][0]["status"] == "ACTIVE"
    issues = lifecycle_issues(tmp_path, today=date(2026,10,1))
    assert issues[0]["code"] == "RULE_STALE"
    assert issues[0]["severity"] == "ERROR"


def test_orphaned_enforcement_target(tmp_path):
    fixture(tmp_path)
    (tmp_path / "scripts/harness/guard.py").unlink()
    issues = lifecycle_issues(tmp_path, today=date(2026,8,20))
    assert issues[0]["code"] == "RULE_ORPHANED"


def test_review_refreshes_rule(tmp_path):
    fixture(tmp_path)
    review_rule(tmp_path, "SEC-1", reviewed_on="2026-09-20", reviewed_by="director")
    report = health_report(tmp_path, today=date(2026,10,1))
    assert report["rules"][0]["status"] == "ACTIVE"


def test_add_rule_requires_real_test_and_enforcement_targets(tmp_path):
    fixture(tmp_path)
    rule = {
      "id":"CTRL-2","class":"CONTROL_INVARIANT","source":"new","applies_when":"candidate",
      "enforcement":"machine","enforced_by":["guard.py"],"retirement":"CONTROL_REVISION_REQUIRED",
      "prose_mode":"router_only","owner":"proof-kernel","tests":["test_guard.py"]
    }
    add_rule(tmp_path, rule)
    report = health_report(tmp_path, today=date.today())
    assert {x["rule_id"] for x in report["rules"]} == {"SEC-1","CTRL-2"}
    added = next(x for x in report["rules"] if x["rule_id"] == "CTRL-2")
    assert added["status"] == "ACTIVE"
