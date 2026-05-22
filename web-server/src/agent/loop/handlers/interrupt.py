"""Interrupt handler: detect LangGraph interrupt, map request→session, yield approval event."""

import json
from uuid import uuid4

from src.agent.loop.audit import log_transition
from src.agent.state import Transition


async def handle_interrupt(state: dict, *, bridge, audit_logger, chat_id: str):
    """Process LangGraph interrupt and yield an approval_required SSE event.

    Only call when has_interrupt(state) is True.

    Transforms the raw LangGraph interrupt value (which has a list of
    pending_tool_calls) into individual tool_approval_required SSE events,
    one per pending tool call.  This keeps the frontend's single-tool
    approval modal working without change.
    """
    interrupts = state["__interrupt__"]
    interrupt_obj = interrupts[0] if interrupts else None
    value = getattr(interrupt_obj, "value", interrupt_obj) if interrupt_obj else {}
    request_id = (
        value.get("request_id", str(uuid4()))
        if isinstance(value, dict)
        else str(uuid4())
    )

    if bridge:
        bridge.create(request_id, chat_id)

    await log_transition(audit_logger, Transition.APPROVAL_PENDING)

    pending = value.get("pending_tool_calls", []) if isinstance(value, dict) else []

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
