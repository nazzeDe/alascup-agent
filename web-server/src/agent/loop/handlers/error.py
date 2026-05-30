"""LLM error recovery handler."""

from src.agent.loop.audit import audit_transition
from src.agent.state import Transition
from src.models.audit import AuditActor


async def handle_llm_error(state: dict, *, error_recovery, context_manager, llm, audit_logger) -> bool:
    """Attempt recovery from an LLM error. Returns True if recovered, False if exhausted."""
    from src.services.llm_adapter import classify_error

    error = state["llm_error"]
    error_type = classify_error(
        error.get("code", 0),
        error.get("message", ""),
        error.get("stop_reason"),
    )
    if not error_type:
        return False

    strategy = error_recovery.get_strategy(error_type)
    if not strategy.get("recoverable"):
        return False

    action = strategy["action"]

    turn_id = state.get("_turn_id")
    iteration = state.get("_iteration")

    if action == "compress_context":
        compressed = await context_manager.compress(state.get("messages", []))
        state["messages"] = compressed
        await audit_transition(audit_logger, Transition.CONTEXT_COMPACTED,
                               turn_id=turn_id, iteration=iteration,
                               actor=AuditActor.SYSTEM)
    elif action == "aggressive_compress":
        compressed = await context_manager.compress(state.get("messages", []))
        state["messages"] = compressed
        await audit_transition(audit_logger, Transition.CONTEXT_COMPACTED,
                               turn_id=turn_id, iteration=iteration,
                               actor=AuditActor.SYSTEM)
    elif action == "escalate_token_limit":
        llm.escalate_max_tokens()
    elif action == "continue_inject":
        msgs = list(state.get("messages", []))
        msgs.append({"role": "user", "content": "Please continue from where you stopped."})
        state["messages"] = msgs
    elif action == "switch_fallback_model":
        llm.switch_to_fallback()

    error_recovery.record_attempt(error_type, action)
    state["llm_error"] = None
    return True
