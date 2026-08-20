from pathlib import Path
import sys, unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/harness"))
from continuation import build_continuation

class ContinuationTests(unittest.TestCase):
    def test_chat_only_pass_does_not_advance(self):
        r=build_continuation({"mission":{"mission_id":"M"},"review_required":True,"review":{"verdict":"PASS","durable":False,"fresh":True},"review_evidence_sink":"GITHUB_PR_REVIEW"})
        self.assertEqual("REVIEWER", r["next_actor"])
        self.assertEqual("GITHUB_PR_REVIEW", r["evidence_sink"])
        self.assertFalse(r["mission_complete"])

    def test_fresh_durable_pass_advances_to_director_when_ready(self):
        r=build_continuation({"mission":{"mission_id":"M"},"review_required":True,"review":{"verdict":"PASS","durable":True,"fresh":True},"evidence_fresh":True,"predicates_complete":True})
        self.assertEqual("DIRECTOR", r["next_actor"])
        self.assertEqual("ROLE_BOUNDARY", r["handoff_class"])

    def test_stale_review_returns_to_reviewer(self):
        r=build_continuation({"mission":{"mission_id":"M"},"review_required":True,"review":{"verdict":"PASS","durable":True,"fresh":False}})
        self.assertEqual("REVIEWER", r["next_actor"])

    def test_human_only_for_explicit_attention(self):
        r=build_continuation({"mission":{"mission_id":"M"},"blocking_human_attention":True})
        self.assertEqual("HUMAN_DECISION_REQUIRED", r["handoff_class"])
        self.assertTrue(r["human_decision_required"])

    def test_mission_complete_is_terminal(self):
        r=build_continuation({"mission":{"mission_id":"M","complete":True},"completion_proven":True})
        self.assertEqual("MISSION_COMPLETE", r["handoff_class"])
        self.assertIsNone(r["next_actor"])

if __name__ == '__main__': unittest.main()
