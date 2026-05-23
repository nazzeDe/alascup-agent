"""SSE event emission from agent state."""

import json
from uuid import uuid4

from src.agent.state import ROLE_MAP


def emit_events(state: dict, chat_id: str = "", skip_assistant_count: int = 0) -> list[dict]:
    """Convert agent state into SSE events for streaming to client.

    Only emits assistant messages beyond ``skip_assistant_count`` to
    avoid re-sending messages that were already emitted in prior loop
    iterations.
    """
    events: list[dict] = []

    assistant_msgs = [m for m in state.get("messages", []) if _msg_role(m) == "assistant"]
    for i, m in enumerate(assistant_msgs[skip_assistant_count:]):
        if isinstance(m, dict):
            msg_id = m.get("id") or str(uuid4())
        else:
            msg_id = str(getattr(m, "id", uuid4()))
        delta = _msg_content(m)
        rc = _msg_reasoning(m)
        if rc:
            events.append({
                "event": "reasoning",
                "data": _json_dumps({"chat_id": chat_id, "message_id": msg_id, "delta": rc, "done": True}),
            })
        if not delta:
            continue
        events.append({"event": "assistant", "data": _json_dumps({"chat_id": chat_id, "message_id": msg_id, "delta": delta})})

    for tc in state.get("tool_calls") or []:
        fn = tc.get("function", {})
        params = fn.get("arguments", "{}")
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except (json.JSONDecodeError, TypeError):
                params = {}
        events.append({
            "event": "tool_call",
            "data": _json_dumps({
                "chat_id": chat_id,
                "message_id": tc.get("id") or str(uuid4()),
                "tool_name": fn.get("name", ""),
                "params": params,
                "is_read_only": tc.get("is_read_only", False),
                "server": tc.get("server_name", ""),
            }),
        })

    for r in state.get("tool_results") or []:
        events.append(_build_tool_result_event(r, chat_id))

    emitted = state.get("_emitted_results") or state.get("streaming_tool_results") or []
    for sr in emitted:
        events.append({
            "event": "tool_call",
            "data": _json_dumps({
                "chat_id": chat_id,
                "message_id": sr.get("tool_call_id") or str(uuid4()),
                "tool_name": sr.get("tool_name", ""),
                "params": {},
                "is_read_only": True,
            }),
        })
        events.append(_build_tool_result_event(sr, chat_id))

    return events


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
