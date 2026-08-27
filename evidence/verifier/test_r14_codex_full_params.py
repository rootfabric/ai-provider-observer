"""R12 verifier-owned checks for the r14 codex full parameter surface.

Runs under scripts/harness/verifier_runner.py (unittest semantics): each test
executes the exact REQ predicate against the exact candidate implementation.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.providers.codex import CodexProvider, _app_server_to_payload


class R14CodexFullParams(unittest.TestCase):
    def _usage_payload(self) -> dict:
        return {
            "plan_type": "plus",
            "rate_limit": {
                "allowed": True,
                "limit_reached": False,
                "primary_window": {"used_percent": 40, "limit_window_seconds": 18000,
                                   "reset_at": 1787811125},
                "secondary_window": {"used_percent": 35, "limit_window_seconds": 604800,
                                     "reset_at": 1788309358},
            },
            "additional_rate_limits": [
                {"limit_name": "gpt-reserve", "metered_feature": "base_model_inference",
                 "rate_limit": {"primary_window": {"used_percent": 0,
                                                   "limit_window_seconds": 604800,
                                                   "reset_at": 1788402434},
                                "secondary_window": None}},
            ],
            "credits": {"has_credits": False, "unlimited": False,
                        "overage_limit_reached": False, "balance": "0"},
            "spend_control": {"reached": False, "individual_limit": None},
            "rate_limit_reset_credits": {"available_count": 1,
                                         "applicable_available_count": 0},
        }

    def test_usage_endpoint_keeps_every_reported_parameter(self):
        snap = CodexProvider.parse(self._usage_payload(), 100)
        d = snap.details
        # Predicate: every non-secret parameter survives into details.
        self.assertFalse(d["has_credits"])
        self.assertFalse(d["unlimited"])
        self.assertEqual(d["spend_control"], {"reached": False, "individual_limit": None})
        self.assertEqual(d["rate_limit_reset_credits"]["available_count"], 1)
        extras = d["additional_rate_limits"]
        self.assertEqual(extras[0]["name"], "gpt-reserve")
        self.assertEqual(extras[0]["windows"][0]["period"], "week")
        self.assertEqual(extras[0]["windows"][0]["used_percent"], 0.0)
        # Windows themselves unchanged (5h / week).
        self.assertEqual([w.name for w in snap.windows], ["5h", "week"])

    def test_app_server_maps_non_codex_limit_entries_and_reset_credits(self):
        result = {
            "rateLimitsByLimitId": {
                "codex": {"limitId": "codex",
                          "primary": {"usedPercent": 10, "windowDurationMins": 300,
                                      "resetsAt": 1},
                          "secondary": None,
                          "credits": {"hasCredits": True, "unlimited": False,
                                      "balance": "3"},
                          "individualLimit": None, "spendControlReached": False,
                          "planType": "plus", "rateLimitReachedType": None},
                "base_model_inference": {"limitName": "gpt-reserve",
                                         "primary": {"usedPercent": 2,
                                                     "windowDurationMins": 10080,
                                                     "resetsAt": 2},
                                         "secondary": None},
            },
            "rateLimitResetCredits": {"availableCount": 1,
                                      "credits": [{"title": "Full reset (Weekly + 5 hr)"}]},
        }
        payload = _app_server_to_payload(result)
        self.assertTrue(payload["credits"]["has_credits"])
        extras = payload["additional_rate_limits"]
        self.assertEqual(len(extras), 1)
        self.assertEqual(extras[0]["metered_feature"], "base_model_inference")
        self.assertEqual(extras[0]["rate_limit"]["primary_window"]["used_percent"], 2)
        reset = payload["rate_limit_reset_credits"]
        self.assertEqual(reset["available_count"], 1)
        self.assertEqual(reset["credits"][0]["title"], "Full reset (Weekly + 5 hr)")


if __name__ == "__main__":
    unittest.main()
