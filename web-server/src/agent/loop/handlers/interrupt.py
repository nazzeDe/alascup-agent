"""Approval handler: yield approval_required SSE events for pending tool calls."""

import json
from uuid import UUID, uuid4

from src.agent.loop.audit import audit_transition
from src.agent.state import Transition


def _safe_uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except (ValueError, AttributeError):
        return None


async def handle_pending_approval(
    pending: list[dict], *, request_id: str,
    bridge, audit_logger, chat_id: str,
    turn_id: UUID | None = None,
    iteration: int | None = None,
):
    """Yield approval_required SSE events for each pending tool call.

    Registers with the bridge so the orchestrator can await decisions.
    """
    if bridge:
        bridge.create(request_id, chat_id)

    await audit_transition(
        audit_logger, Transition.APPROVAL_PENDING,
        chat_id=_safe_uuid(chat_id),
        turn_id=turn_id, iteration=iteration,
    )

    if not pending:
        yield {
            "event": "tool_approval_required",
            "data": json.dumps({
                "chat_id": chat_id,
                "request_id": request_id,
                "tool_name": "",
                "params": {},
                "reason": "No tool calls to approve",
            }, default=str),
        }
        return

    for tc in pending:
        fn = tc.get("function", {}) if isinstance(tc, dict) else {}
        params = fn.get("arguments", "{}")
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except (json.JSONDecodeError, TypeError):
                params = {}

        yield {
            "event": "tool_approval_required",
            "data": json.dumps({
                "chat_id": chat_id,
                "request_id": request_id,
                "tool_name": fn.get("name", ""),
                "params": params,
                "reason": fn.get("name", "") + " needs your approval to execute",
            }, default=str),
        }
