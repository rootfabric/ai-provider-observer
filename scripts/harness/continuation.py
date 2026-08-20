from __future__ import annotations
from typing import Any


def build_continuation(state: dict[str, Any]) -> dict[str, Any]:
    """Compute the next legal role transition without changing project state."""
    mission = state.get("mission") or {}
    mission_id = mission.get("mission_id") or state.get("work_order_id") or "UNDECLARED_MISSION"
    if mission.get("complete") is True:
        if state.get("completion_proven") is True:
            return _result(mission_id, True, "MISSION_COMPLETE", None, None, None, "mission success condition machine-proven", None, None, False)
        return _result(mission_id, False, "SYSTEM_BLOCKED", "DIRECTOR", "RECONCILE_UNPROVEN_COMPLETION_CLAIM", "EXECUTION_LEDGER", "completion proof rebuilt from exact-head evidence", "MISSION_COMPLETE", "ROUTE_FINDING_TO_IMPLEMENTER_OR_REVIEWER", False)

    if state.get("blocking_human_attention"):
        return _result(mission_id, False, "HUMAN_DECISION_REQUIRED", "HUMAN", "RESOLVE_EXPLICIT_ATTENTION_ITEM", None, "durable human decision recorded", "RESUME_PREVIOUS_TRANSITION", "REMAIN_WAITING_HUMAN", True)

    if state.get("system_blocked"):
        return _result(mission_id, False, "SYSTEM_BLOCKED", "IMPLEMENTER", "BUILD_REPAIR_MAP_AND_REPAIR", "EXECUTION_LEDGER", "repair evidence recorded and blockers cleared", "RETURN_TO_REQUIRED_GATE", "ESCALATE_REPAIR", False)

    review_required = bool(state.get("review_required", True))
    review = state.get("review") or {}
    if review_required:
        if not review.get("durable") or review.get("verdict") != "PASS" or review.get("fresh") is not True:
            return _result(mission_id, False, "ROLE_BOUNDARY", "REVIEWER", "PERSIST_FRESH_EXACT_HEAD_REVIEW", state.get("review_evidence_sink", "EXECUTION_LEDGER"), "durable PASS bound to exact candidate head", "REFRESH_EVIDENCE_OR_VERIFY", "IMPLEMENTER_REPAIR_OR_SUPPLY_EVIDENCE", False)

    if state.get("evidence_fresh") is not True:
        return _result(mission_id, False, "ROLE_BOUNDARY", "IMPLEMENTER", "REFRESH_EVIDENCE_MAP_FROM_DURABLE_REVIEW", "EXECUTION_LEDGER", "evidence map fresh and bound to reviewed head", "VERIFY_REMAINING_PREDICATES", "REPAIR_EVIDENCE", False)

    if state.get("predicates_complete") is not True:
        return _result(mission_id, False, "ROLE_BOUNDARY", "VERIFIER", "VERIFY_REMAINING_PREDICATES", "EXECUTION_LEDGER", "all required predicates durably PASS", "DIRECTOR_EVALUATE_CHECKPOINT_OR_INTEGRATION", "IMPLEMENTER_REPAIR", False)

    return _result(mission_id, False, "ROLE_BOUNDARY", "DIRECTOR", "EVALUATE_CHECKPOINT_OR_INTEGRATION", "EXECUTION_LEDGER", "director decision/checkpoint evidence recorded", "RESUME_PARENT_MISSION", "ROUTE_FINDING_TO_IMPLEMENTER_OR_HUMAN", False)


def _result(mission_id, complete, cls, actor, action, sink, resume, success, failure, human):
    return {
        "mission_id": mission_id,
        "mission_complete": complete,
        "handoff_class": cls,
        "next_actor": actor,
        "next_action": action,
        "evidence_sink": sink,
        "resume_condition": resume,
        "on_success": success,
        "on_failure": failure,
        "human_decision_required": human,
    }
