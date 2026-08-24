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
