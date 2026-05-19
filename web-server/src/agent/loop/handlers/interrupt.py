"""Interrupt handler: detect LangGraph interrupt, map request→session, yield approval event."""

import json
from uuid import uuid4

from src.agent.loop.audit import log_transition
from src.agent.state import Transition


async def handle_interrupt(state: dict, *, bridge, audit_logger, session_id: str):
    """Process LangGraph interrupt and yield an approval_required SSE event.

    Only call when has_interrupt(state) is True.
    """
    interrupts = state["__interrupt__"]
    interrupt_obj = interrupts[0] if interrupts else None
    value = getattr(interrupt_obj, "value", interrupt_obj) if interrupt_obj else {}
    request_id = value.get("request_id", str(uuid4())) if isinstance(value, dict) else str(uuid4())

    if bridge:
        bridge.create(request_id, session_id)

    await log_transition(audit_logger, Transition.APPROVAL_PENDING)

    yield {
        "event": "tool_approval_required",
        "data": json.dumps(value, default=str),
    }
