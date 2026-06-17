"""Conversation history projection for LLM-ready messages."""

from datetime import datetime

from src.models.message import MessageType
from src.models.session import ChatSession


def build_llm_history(session: ChatSession) -> list[dict]:
    """Project stored session data into LLM-ready message history."""
    entries: list[tuple[datetime, str, object]] = []

    for m in session.messages:
        ts = datetime.fromisoformat(m.timestamp)
        entries.append((ts, "msg", m))

    # Compatibility fallback for sessions written before TOOL_RESULT messages
    # carried tool_call_id/tool_name metadata.
    has_tool_result_messages = any(m.type == MessageType.TOOL_RESULT for m in session.messages)
    if not has_tool_result_messages:
        for tc in session.executed_tool_list:
            if tc.result is None:
                continue
            ts = datetime.fromisoformat(tc.timestamp)
            entries.append((ts, "tool", tc))

    entries.sort(key=lambda e: e[0])

    history: list[dict] = []
    for _ts, kind, obj in entries:
        if kind == "msg":
            history.append(_message_to_history(obj))
        else:
            history.append(_tool_call_to_history(obj))

    return history


def _message_to_history(m) -> dict:
    role = m.type.value if isinstance(m.type, MessageType) else m.type
    entry: dict = {"role": role, "content": m.content}
    if m.tool_calls:
        entry["tool_calls"] = m.tool_calls
    if m.type == MessageType.TOOL_RESULT:
        entry["role"] = "tool"
        if m.tool_call_id:
            entry["tool_call_id"] = m.tool_call_id
        if m.tool_name:
            entry["name"] = m.tool_name
    if m.reasoning_content:
        entry["reasoning_content"] = m.reasoning_content
    return entry


def _tool_call_to_history(tc) -> dict:
    return {
        "role": "tool",
        "content": _format_tool_result_for_history(tc),
        "tool_call_id": tc.llm_tool_call_id or str(tc.call_id or ""),
        "name": tc.name,
    }


def _format_tool_result_for_history(tc) -> str:
    result = tc.result or {}
    status = result.get("execution_status", tc.execution_status.value)
    output = result.get("output", "")
    error = result.get("error", {})
    if isinstance(error, dict):
        error_msg = error.get("message", "")
    else:
        error_msg = str(error) if error else ""

    parts = [f"[{tc.name}] execution_status={status}"]
    if output:
        parts.append(f"output={output}")
    if error_msg:
        parts.append(f"error={error_msg}")

    return "\n".join(parts)
