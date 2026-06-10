"""SSE event emission from agent state."""

import json
from uuid import uuid4

from loguru import logger

from src.agent.state import ROLE_MAP


def emit_events(state: dict, chat_id: str = "", skip_assistant_count: int = 0) -> list[dict]:
    """Convert agent state into SSE events for streaming to client.

    Only emits assistant messages beyond ``skip_assistant_count`` to
    avoid re-sending messages that were already emitted in prior loop
    iterations.
    """
    events: list[dict] = []
    _emit_assistant_messages(state, chat_id, skip_assistant_count, events)
    _emit_tool_calls(state, chat_id, events)
    _emit_tool_results(state, chat_id, events)
    return events


def _emit_assistant_messages(state: dict, chat_id: str, skip_count: int, events: list[dict]) -> None:
    """Deduplicate and emit assistant messages as SSE events."""
    seen: set[str] = set()
    deduped: list = []
    for m in state.get("messages", []):
        if _msg_role(m) != "assistant":
            continue
        mid = _msg_id(m)
        if mid in seen:
            continue
        seen.add(mid)
        deduped.append(m)

    logger.debug("EMIT_EVENTS: total_msgs={t} assistant_msgs={a} skip={s}",
                 t=len(state.get("messages", [])), a=len(deduped), s=skip_count)
    # Reasoning tokens are streamed in real-time via _forward_to_queue →
    # drain_queue.  _emit_assistant_messages only emits the content delta;
    # the frontend receives `thinking_done` to close the reasoning bubble.
    for m in deduped[skip_count:]:
        msg_id = m.get("id") or str(uuid4()) if isinstance(m, dict) else str(getattr(m, "id", uuid4()))
        delta = _msg_content(m)
        if delta:
            events.append({"event": "assistant", "data": _json_dumps({"chat_id": chat_id, "message_id": msg_id, "delta": delta})})


def _emit_tool_calls(state: dict, chat_id: str, events: list[dict]) -> None:
    """Emit pending tool calls as SSE events."""
    for tc in state.get("tool_calls") or []:
        fn = tc.get("function", {})
        params = fn.get("arguments", "{}")
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except (json.JSONDecodeError, TypeError):
                params = {}
        events.append({"event": "tool_call", "data": _json_dumps({
            "chat_id": chat_id,
            "message_id": tc.get("id") or str(uuid4()),
            "tool_name": fn.get("name", ""),
            "params": params,
            "is_read_only": tc.get("is_read_only", False),
            "server": tc.get("server_name", ""),
        })})


def _emit_tool_results(state: dict, chat_id: str, events: list[dict]) -> None:
    """Emit tool results and streaming pre-executed results."""
    for r in state.get("tool_results") or []:
        events.append(_build_tool_result_event(r, chat_id))

    emitted = state.get("_emitted_results") or state.get("streaming_tool_results") or []
    for sr in emitted:
        events.append({"event": "tool_call", "data": _json_dumps({
            "chat_id": chat_id,
            "message_id": sr.get("tool_call_id") or str(uuid4()),
            "tool_name": sr.get("tool_name", ""),
            "params": {},
            "is_read_only": sr.get("is_read_only", False),
        })})
        events.append(_build_tool_result_event(sr, chat_id))


def _build_tool_result_event(r: dict, chat_id: str) -> dict:
    res = r.get("result", {})
    evt = {
        "event": "tool_result",
        "data": _json_dumps(_tool_result_data(r, res, chat_id)),
    }
    return evt


def _tool_result_data(r: dict, res: dict, chat_id: str) -> dict:
    evt = {
        "chat_id": chat_id,
        "message_id": r.get("tool_call_id") or str(uuid4()),
        "tool_name": r.get("tool_name", ""),
        "execution_status": res.get("execution_status", "SUCCEEDED"),
    }
    output = res.get("output")
    if output is not None:
        evt["output"] = output
    error = res.get("error")
    if error is not None:
        evt["error"] = error
    et = res.get("execution_time_ms")
    if et is not None:
        evt["execution_time_ms"] = et
    return evt


def _msg_reasoning(m) -> str:
    if isinstance(m, dict):
        return str(m.get("reasoning_content", ""))
    return str(getattr(m, "additional_kwargs", {}).get("reasoning_content", ""))


def _json_dumps(obj) -> str:
    return json.dumps(obj, default=str)


def _msg_id(m) -> str:
    if isinstance(m, dict):
        return m.get("id") or _msg_content(m)[:80]
    mid = str(getattr(m, "id", ""))
    if mid:
        return mid
    return _msg_content(m)[:80]


def _msg_role(m) -> str:
    if isinstance(m, dict):
        r = m.get("role", m.get("type", ""))
    else:
        r = getattr(m, "type", getattr(m, "role", ""))
    return ROLE_MAP.get(r, r)


def _msg_content(m) -> str:
    if isinstance(m, dict):
        return str(m.get("content", ""))
    return str(getattr(m, "content", ""))
