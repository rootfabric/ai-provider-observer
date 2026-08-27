from app.providers.codex import CodexProvider
from app.providers.deepseek import DeepSeekProvider
from app.providers.minimax import MiniMaxProvider
from app.providers.openrouter import OpenRouterProvider
from app.providers.zai import ZaiProvider


def test_zai_credit_windows():
    p={"data":{"level":"lite","limits":[
        {"type":"CREDIT_LIMIT","unit":3,"number":5,"usage":2000,"currentValue":1653,"remaining":347,"percentage":82,"nextResetTime":1787176502893},
        {"type":"CREDIT_LIMIT","unit":6,"number":1,"usage":10000,"currentValue":4562,"remaining":5438,"percentage":45,"nextResetTime":1787607163997}]}}
    s=ZaiProvider.parse(p,100)
    assert [w.name for w in s.windows]==["5h","week"]
    assert s.windows[0].remaining_percent==18
    assert s.plan=="lite"


def test_minimax_remaining_semantics():
    p={"model_remains":[{"model_name":"general","current_interval_remaining_percent":73,"current_weekly_remaining_percent":52,"current_interval_status":1,"current_weekly_status":1,"end_time":1787000000000,"weekly_end_time":1787600000000}],"base_resp":{"status_code":0}}
    s=MiniMaxProvider.parse(p,100)
    assert s.windows[0].used_percent==27
    assert s.windows[1].used_percent==48


def test_deepseek_balance():
    p={"is_available":True,"balance_infos":[{"currency":"USD","total_balance":"12.50","granted_balance":"2.00","topped_up_balance":"10.50"}]}
    s=DeepSeekProvider.parse(p,100)
    assert s.balances[0]["total"]==12.5
    assert s.details["available"] is True


def test_openrouter_key_limit():
    p={"data":{"limit":100,"limit_remaining":74.5,"limit_reset":"monthly","usage":25.5,"usage_daily":1,"usage_weekly":5,"usage_monthly":25.5,"label":"key"}}
    s=OpenRouterProvider.parse_key(p,100)
    assert s.windows[0].used_percent==25.5
    assert s.details["usage_weekly_usd"]==5


def test_codex_windows():
    p={"plan_type":"plus","rate_limit":{"allowed":True,"limit_reached":False,"primary_window":{"used_percent":44,"limit_window_seconds":18000,"reset_at":1787000000},"secondary_window":{"used_percent":71,"limit_window_seconds":604800,"reset_at":1787600000}},"credits":{"balance":"5.5"}}
    s=CodexProvider.parse(p,100)
    assert [w.name for w in s.windows]==["5h","week"]
    assert s.windows[1].remaining_percent==29
    assert s.balances[0]["total"]==5.5


def test_codex_extended_params_from_usage_endpoint():
    """The usage endpoint reports more parameters than two windows: they all
    must survive into details (credits flags, spend control, promo, free
    reset credits, additional metered limits)."""
    p = {
        "plan_type": "plus",
        "rate_limit": {
            "allowed": True,
            "limit_reached": False,
            "primary_window": {"used_percent": 40, "limit_window_seconds": 18000, "reset_at": 1787811125},
            "secondary_window": {"used_percent": 35, "limit_window_seconds": 604800, "reset_at": 1788309358},
        },
        "code_review_rate_limit": None,
        "additional_rate_limits": [
            {
                "limit_name": "gpt-reserve",
                "metered_feature": "base_model_inference",
                "rate_limit": {
                    "allowed": True,
                    "limit_reached": False,
                    "primary_window": {"used_percent": 0, "limit_window_seconds": 604800, "reset_at": 1788402434},
                    "secondary_window": None,
                },
            }
        ],
        "credits": {"has_credits": False, "unlimited": False, "overage_limit_reached": False, "balance": "0"},
        "spend_control": {"reached": False, "individual_limit": None},
        "promo": None,
        "rate_limit_reset_credits": {"available_count": 1, "applicable_available_count": 0},
    }
    s = CodexProvider.parse(p, 100)
    d = s.details
    assert d["has_credits"] is False and d["unlimited"] is False
    assert d["spend_control"] == {"reached": False, "individual_limit": None}
    assert d["rate_limit_reset_credits"]["available_count"] == 1
    # The gpt-reserve weekly window must be visible with name + percent.
    assert d["additional_rate_limits"][0]["name"] == "gpt-reserve"
    row = d["additional_rate_limits"][0]["windows"][0]
    assert row["period"] == "week" and row["used_percent"] == 0.0
    # NULL code_review_rate_limit must not fabricate a parameter row.
    assert "code_review_rate_limit" not in d


def test_codex_app_server_maps_all_by_limit_id_entries():
    """The app-server result carries every limit under rateLimitsByLimitId;
    entries other than 'codex' plus reset credits must map into the snake_case
    payload instead of being dropped by the single-'codex' picker."""
    from app.providers.codex import _app_server_to_payload
    result = {
        "rateLimits": {"limitId": "codex", "primary": {"usedPercent": 10, "windowDurationMins": 300, "resetsAt": 1}},
        "rateLimitsByLimitId": {
            "codex": {"limitId": "codex", "limitName": None,
                      "primary": {"usedPercent": 10, "windowDurationMins": 300, "resetsAt": 1},
                      "secondary": None, "credits": {"hasCredits": True, "unlimited": False, "balance": "3"},
                      "individualLimit": None, "spendControlReached": False,
                      "planType": "plus", "rateLimitReachedType": None},
            "base_model_inference": {"limitId": "base_model_inference", "limitName": "gpt-reserve",
                                     "primary": {"usedPercent": 2, "windowDurationMins": 10080, "resetsAt": 2},
                                     "secondary": None, "credits": None,
                                     "individualLimit": None, "spendControlReached": None,
                                     "planType": "plus", "rateLimitReachedType": None},
        },
        "rateLimitResetCredits": {"availableCount": 1, "credits": [
            {"id": "x", "title": "Full reset (Weekly + 5 hr)", "status": "available"}]},
    }
    payload = _app_server_to_payload(result)
    assert payload["plan_type"] == "plus"
    assert payload["credits"]["has_credits"] is True
    extras = payload["additional_rate_limits"]
    assert len(extras) == 1
    assert extras[0]["metered_feature"] == "base_model_inference"
    assert extras[0]["rate_limit"]["primary_window"]["used_percent"] == 2
    reset = payload["rate_limit_reset_credits"]
    assert reset["available_count"] == 1
    assert reset["credits"][0]["title"] == "Full reset (Weekly + 5 hr)"
