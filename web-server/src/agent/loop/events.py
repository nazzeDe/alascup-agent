"""SSE event emission from agent state."""

import json
from uuid import uuid4


def emit_events(state: dict, chat_id: str = "", skip_assistant_count: int = 0) -> list[dict]:
    """Convert agent state into SSE events for streaming to client.

    Only emits assistant messages beyond ``skip_assistant_count`` to
    avoid re-sending messages that were already emitted in prior loop
    iterations.
    """
    events: list[dict] = []

    assistant_msgs = [m for m in state.get("messages", []) if _msg_role(m) == "assistant"]
    for m in assistant_msgs[skip_assistant_count:]:
        if isinstance(m, dict):
            msg_id = m.get("id") or str(uuid4())
        else:
            msg_id = str(getattr(m, "id", uuid4()))
        events.append({"event": "assistant", "data": _json_dumps({"chat_id": chat_id, "message_id": msg_id, "delta": _msg_content(m)})})

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
            }),
        })

    for r in state.get("tool_results") or []:
        res = r.get("result", {})
        events.append({
            "event": "tool_result",
            "data": _json_dumps({
                "chat_id": chat_id,
                "message_id": r.get("tool_call_id") or str(uuid4()),
                "tool_name": r.get("tool_name", ""),
                "execution_status": res.get("execution_status", "SUCCEEDED"),
            }),
        })

    for sr in state.get("streaming_tool_results") or []:
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
        res = sr.get("result", {})
        events.append({
            "event": "tool_result",
            "data": _json_dumps({
                "chat_id": chat_id,
                "message_id": sr.get("tool_call_id") or str(uuid4()),
                "tool_name": sr.get("tool_name", ""),
                "execution_status": res.get("execution_status", "SUCCEEDED"),
            }),
        })

    return events


def _json_dumps(obj) -> str:
    return json.dumps(obj, default=str)


def _msg_role(m) -> str:
    if isinstance(m, dict):
        r = m.get("role", m.get("type", ""))
    else:
        r = getattr(m, "type", getattr(m, "role", ""))
    return {"ai": "assistant", "human": "user", "tool": "tool"}.get(r, r)


def _msg_content(m) -> str:
    if isinstance(m, dict):
        return str(m.get("content", ""))
    return str(getattr(m, "content", ""))
